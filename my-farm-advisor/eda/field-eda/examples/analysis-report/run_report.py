#!/usr/bin/env python3
"""Assemble a one-time Markdown report from field-EDA outputs.

This is a downstream example consumer, not part of the reusable workflow. It reads the
manifest written by ``run_field_eda.py``, verifies that every figure and table still
matches its recorded hash, and writes a portable report bundle under ``output/``.

It computes no new analysis: all numbers come from the existing tables.

Run from this directory:

    export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
    "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_report.py
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

import pandas as pd

STATE_LABELS = {"IA": "Iowa", "IL": "Illinois", "NE": "Nebraska"}
STATE_ORDER = ["IA", "IL", "NE"]

FIGURES = [
    {
        "id": "B1",
        "category": "Boundaries",
        "level": "across fields / map",
        "file": "b1_field_map_and_acreage.png",
        "job": "Static geospatial map by grower, plus the acreage distribution behind it.",
    },
    {
        "id": "B2",
        "category": "Boundaries",
        "level": "across fields",
        "file": "b2_area_vs_compactness.png",
        "job": "Correlation between field size and compactness within each grower.",
    },
    {
        "id": "B3",
        "category": "Boundaries",
        "level": "comparison",
        "file": "b3_polygon_vs_cdl_area.png",
        "job": "Polygon area versus CDL pixel area, the composition denominator check.",
    },
    {
        "id": "C1",
        "category": "CDL",
        "level": "within field / field-year",
        "file": "c1_crop_sequence_heatmap.png",
        "job": "Observed crop sequence per field and the rotation archetypes.",
    },
    {
        "id": "C2",
        "category": "CDL",
        "level": "grower / across growers",
        "file": "c2_crop_mix_by_grower_year.png",
        "job": "Crop mix by grower-year and whether rotations are synchronized.",
    },
    {
        "id": "C3",
        "category": "CDL",
        "level": "comparison",
        "file": "c3_transition_matrix.png",
        "job": "Observed crop transitions, the rotation-discipline comparison.",
    },
    {
        "id": "W1",
        "category": "Weather",
        "level": "within field / field-year",
        "file": "w1_within_field_precip_anomaly.png",
        "job": "Each field-year's wet/dry position relative to that field's own mean.",
    },
    {
        "id": "W2",
        "category": "Weather",
        "level": "grower / across growers",
        "file": "w2_climate_space.png",
        "job": "Grower climate space at the honest cell-year unit.",
    },
    {
        "id": "W3",
        "category": "Weather",
        "level": "within field / across growers",
        "file": "w3_crop_weather_exposure.png",
        "job": "Corn versus soybean exposure, and whether location separates them.",
    },
]

TABLES = [
    ("field_summary.csv", "Boundaries", "Field geometry, acreage, compactness, CDL area check."),
    ("cdl_field_year.csv", "CDL", "Crop-group shares and dominant crop per field-year."),
    ("rotation_summary.csv", "CDL", "Sequence, transitions, change rate, diversity."),
    ("weather_field_year.csv", "Weather", "Primary season and sensitivity columns per field-year."),
    ("weather_cell_year.csv", "Weather", "De-duplicated weather per grid cell-year."),
    ("crop_weather.csv", "Weather", "Field-year weather joined to the CDL dominant crop."),
    ("weather_qc.csv", "Weather", "Per field-year completeness and range checks."),
    ("weather_sensitivity.csv", "Weather", "Alternate season and GDD definitions with ranks."),
    ("grower_profile.csv", "All", "One row per grower; the report handoff table."),
]


def default_input() -> Path:
    root = Path(
        os.environ.get("DATA_PIPELINE_DATA_ROOT", "~/my-farm-advisor-runtime")
    ).expanduser()
    return root / "data-pipeline" / "eda" / "field-eda"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fmt(value: object, digits: int = 1) -> str:
    return f"{float(value):,.{digits}f}"


def pct(value: object, digits: int = 1) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def signed(value: object, digits: int = 1) -> str:
    return f"{float(value):+,.{digits}f}"


def portable_path(value: str, root: str) -> str:
    """Replace an absolute path under ``root`` with a data-root-relative form."""
    root = root.rstrip("/")
    if root and value == root:
        return "$DATA_PIPELINE_DATA_ROOT"
    if root and value.startswith(f"{root}/"):
        return f"$DATA_PIPELINE_DATA_ROOT/{value[len(root) + 1:]}"
    return value


def portable_value(value, root: str):
    """Recursively replace host paths in strings, lists, and dicts."""
    if isinstance(value, str):
        return portable_path(value, root)
    if isinstance(value, list):
        return [portable_value(item, root) for item in value]
    if isinstance(value, dict):
        return {key: portable_value(item, root) for key, item in value.items()}
    return value


def sanitize_manifest(manifest: dict) -> dict:
    """Return a copy with host-specific paths made portable.

    The committed evidence must be readable without the author's machine, so the
    report bundle keeps the manifest structure and hashes but not the absolute
    data root.
    """
    sanitized = copy.deepcopy(manifest)
    root = str(manifest.get("data_root", ""))
    if not root:
        return sanitized

    sanitized["data_root"] = "$DATA_PIPELINE_DATA_ROOT"
    sanitized["output_dir"] = portable_path(
        str(manifest.get("output_dir", "")), root
    )
    sanitized["command"] = str(manifest.get("command", "")).replace(
        root, "$DATA_PIPELINE_DATA_ROOT"
    )
    sanitized["inputs"] = portable_value(manifest.get("inputs", {}), root)
    return sanitized


def verify_manifest(manifest: dict, input_dir: Path) -> list[str]:
    problems: list[str] = []
    for record in manifest["outputs"]:
        path = input_dir / record["path"]
        if not path.exists():
            problems.append(f"missing: {record['path']}")
        elif sha256_file(path) != record["sha256"]:
            problems.append(f"hash mismatch: {record['path']}")
    return problems


def load_tables(input_dir: Path) -> dict[str, pd.DataFrame]:
    tables_dir = input_dir / "tables"
    return {
        path.stem: pd.read_csv(path)
        for path in sorted(tables_dir.glob("*.csv"))
    }


def compute_metrics(
    tables: dict[str, pd.DataFrame], manifest: dict
) -> dict[str, object]:
    field = tables["field_summary"]
    profile = tables["grower_profile"].set_index("state")
    rotation = tables["rotation_summary"]
    cdl = tables["cdl_field_year"]
    crop_weather = tables["crop_weather"]
    qc = tables["weather_qc"]

    boundaries = []
    for state in STATE_ORDER:
        subset = field.loc[field["state"] == state]
        rho = subset["acres"].corr(subset["compactness"], method="spearman")
        boundaries.append(
            {
                "state": state,
                "label": STATE_LABELS[state],
                "fields": len(subset),
                "acres_min": subset["acres"].min(),
                "acres_median": subset["acres"].median(),
                "acres_max": subset["acres"].max(),
                "rho": rho,
            }
        )

    weather = []
    for state in STATE_ORDER:
        row = profile.loc[state]
        weather.append(
            {
                "state": state,
                "label": STATE_LABELS[state],
                "cells": int(row["weather_cells"]),
                "precip": row["precip_mm"],
                "gdd": row["gdd_c"],
                "heat": row["heat_days_35c"],
                "acres_median": row["acres_median"],
                "alternation": row["alternation_rate_mean"],
                "continuous": int(row["fields_with_continuous_corn"]),
            }
        )

    rotation_rows = []
    for state in STATE_ORDER:
        subset = rotation.loc[rotation["state"] == state]
        rotation_rows.append(
            {
                "state": state,
                "label": STATE_LABELS[state],
                "fields": len(subset),
                "alternation": subset["alternation_rate"].mean(),
                "continuous": int(subset["has_continuous_corn"].sum()),
                "diversity_two": int((subset["crop_diversity"] >= 2).sum()),
            }
        )

    mix = (
        cdl.groupby(["state", "dominant_crop"]).size().unstack(fill_value=0)
    )
    mix_rows = []
    for state in STATE_ORDER:
        row = mix.loc[state] if state in mix.index else pd.Series(dtype=int)
        mix_rows.append(
            {
                "state": state,
                "label": STATE_LABELS[state],
                "corn": int(row.get("Corn", 0)),
                "soy": int(row.get("Soybeans", 0)),
            }
        )

    grouped = (
        crop_weather.groupby(["state", "field_id", "dominant_crop"], sort=False)
        .agg(
            precip=("precip_mm", "mean"),
            gdd=("gdd_c", "mean"),
            years=("year", "nunique"),
        )
        .reset_index()
    )
    corn = grouped.loc[grouped["dominant_crop"] == "Corn"].set_index(
        ["state", "field_id"]
    )
    soy = grouped.loc[grouped["dominant_crop"] == "Soybeans"].set_index(
        ["state", "field_id"]
    )
    paired = corn.join(soy, suffixes=("_corn", "_soy"), how="inner")
    paired["delta_gdd"] = paired["gdd_corn"] - paired["gdd_soy"]
    paired["delta_precip"] = paired["precip_corn"] - paired["precip_soy"]
    paired = paired.reset_index()

    paired_rows = []
    for state in STATE_ORDER:
        subset = paired.loc[paired["state"] == state]
        paired_rows.append(
            {
                "state": state,
                "label": STATE_LABELS[state],
                "fields": len(subset),
                "delta_gdd": subset["delta_gdd"].median(),
                "delta_precip": subset["delta_precip"].median(),
            }
        )

    return {
        "boundaries": boundaries,
        "weather": weather,
        "rotation": rotation_rows,
        "mix": mix_rows,
        "paired": paired_rows,
        "corn_years": int((crop_weather["dominant_crop"] == "Corn").sum()),
        "soy_years": int((crop_weather["dominant_crop"] == "Soybeans").sum()),
        "paired_total": int(len(paired)),
        "acres_median": float(field["acres"].median()),
        "acres_min": float(field["acres"].min()),
        "acres_max": float(field["acres"].max()),
        "compact_min": float(field["compactness"].min()),
        "compact_max": float(field["compactness"].max()),
        "area_dev_median": float(field["area_dev_pct"].median()),
        "area_dev_abs_max": float(field["area_dev_pct"].abs().max()),
        "cells": int(manifest["counts"]["weather_cells"]),
        "qc_pass": bool((qc["status"] == "pass").all()),
        "cdl_years": manifest["definitions"]["cdl_years"],
        "weather_years": manifest["counts"]["weather_years"],
    }


def build_markdown(metrics: dict, manifest: dict, artifact_rows: list[dict]) -> str:
    b = {row["state"]: row for row in metrics["boundaries"]}
    w = {row["state"]: row for row in metrics["weather"]}
    r = {row["state"]: row for row in metrics["rotation"]}
    m = {row["state"]: row for row in metrics["mix"]}
    p = {row["state"]: row for row in metrics["paired"]}

    boundary_table = "\n".join(
        "| {label} | {fields} | {lo} | {med} | {hi} | {rho:.2f} |".format(
            label=b[s]["label"],
            fields=b[s]["fields"],
            lo=fmt(b[s]["acres_min"], 0),
            med=fmt(b[s]["acres_median"], 0),
            hi=fmt(b[s]["acres_max"], 0),
            rho=b[s]["rho"],
        )
        for s in STATE_ORDER
    )
    weather_table = "\n".join(
        "| {label} | {cells} | {precip} | {gdd} | {heat} |".format(
            label=w[s]["label"],
            cells=w[s]["cells"],
            precip=fmt(w[s]["precip"], 0),
            gdd=fmt(w[s]["gdd"], 0),
            heat=fmt(w[s]["heat"], 1),
        )
        for s in STATE_ORDER
    )
    rotation_table = "\n".join(
        "| {label} | {fields} | {alt} | {cont} | {div} |".format(
            label=r[s]["label"],
            fields=r[s]["fields"],
            alt=pct(r[s]["alternation"], 0),
            cont=r[s]["continuous"],
            div=r[s]["diversity_two"],
        )
        for s in STATE_ORDER
    )
    mix_table = "\n".join(
        "| {label} | {corn} | {soy} |".format(
            label=m[s]["label"], corn=m[s]["corn"], soy=m[s]["soy"]
        )
        for s in STATE_ORDER
    )
    paired_table = "\n".join(
        "| {label} | {fields} | {gdd} | {precip} |".format(
            label=p[s]["label"],
            fields=p[s]["fields"],
            gdd=signed(p[s]["delta_gdd"], 0),
            precip=signed(p[s]["delta_precip"], 0),
        )
        for s in STATE_ORDER
    )

    figure_sections: dict[str, list[str]] = {"Boundaries": [], "CDL": [], "Weather": []}
    for figure in FIGURES:
        figure_sections[figure["category"]].append(
            f"### {figure['id']}  {figure['job']}\n\n"
            f"![{figure['id']} {figure['job']}](figures/{figure['file']})\n\n"
        )

    artifact_table = "\n".join(
        "| {kind} | {id} | {category} | {level} | `{file}` | {job} |".format(
            **row
        )
        for row in artifact_rows
    )

    caveats = "\n".join(f"- {item}" for item in manifest["caveats"])
    generated = manifest["generated_at_utc"]
    cdl_years = metrics["cdl_years"]
    weather_years = metrics["weather_years"]

    return f"""# Field-level EDA: boundaries, CDL, and weather

**Question.** Are these 30 fields one comparable population, or several distinct operating
contexts?

**Short answer.** Three operating contexts. Field geometry is broadly interchangeable, but
rotation discipline, crop mix, and heat/water exposure separate Iowa, Illinois, and
Nebraska. Crop choice tracks geography and rotation phase rather than a separate weather
niche.

This report summarizes a static EDA run. It computes nothing new. Generated from
`{generated}`; CDL years {cdl_years[0]}–{cdl_years[-1]}, weather years
{weather_years[0]}–{weather_years[-1]}.

## Scope and data

- 3 growers x 10 fields = 30 fields, 5 weather years, one farm per grower.
- Categories: field boundaries, CDL crop history, and May–Sep growing-season weather.
- Weather is NASA POWER on {metrics['cells']} native grid cells, so field counts overstate
  independent weather samples.
- Not a random sample: the fields are the 10 largest CSB blocks per county.
- No yield, soil, irrigation, NDVI, or management data is used. No p-values are presented
  as inference.

## Headline numbers

- Field size: {fmt(metrics['acres_min'], 0)}–{fmt(metrics['acres_max'], 0)} acres, median
  {fmt(metrics['acres_median'], 0)}. Compactness {metrics['compact_min']:.2f}–{metrics['compact_max']:.2f}.
- Weather: {metrics['cells']} cells. GDD ranks Iowa < Illinois < Nebraska under every
  definition tested; precipitation does not, so only "Nebraska is driest" survives.
- Rotation: alternation is near-strict in Iowa and Illinois and weaker in Nebraska, where
  repeated corn is common.
- Crop-weather: {metrics['corn_years']} corn field-years and {metrics['soy_years']} soybean
  field-years. The {metrics['paired_total']} fields that grew both crops show paired
  differences that do not point one way across growers.

## 1. Boundaries

The "farm" is a portfolio of scattered county blocks, not one contiguous parcel. The map
and acreage strip show the size range; the correlation strip asks whether size predicts
shape; the parity panel checks that CDL pixel area can be used as a composition denominator.

| Grower | Fields | Min ac | Median ac | Max ac | Area–compactness rho |
|---|---:|---:|---:|---:|---:|
{boundary_table}

Polygon versus CDL area: median deviation {signed(metrics['area_dev_median'], 2)}%,
largest absolute deviation {fmt(metrics['area_dev_abs_max'], 2)}%. CDL area is credible as a
denominator.

{''.join(figure_sections['Boundaries'])}

## 2. CDL crop history

Corn and soybean dominate every grower. The interesting difference is discipline: Iowa and
Illinois alternate, Nebraska repeats corn more often.

| Grower | Fields | Mean alternation | Continuous-corn fields | Fields with 2+ crops |
|---|---:|---:|---:|---:|
{rotation_table}

Dominant-crop field-years, {cdl_years[0]}–{cdl_years[-1]}:

| Grower | Corn | Soybeans |
|---|---:|---:|
{mix_table}

{''.join(figure_sections['CDL'])}

## 3. Weather

Weather is compared at the honest unit: cell-year for growers, field-year for within-field
change. Cell-weighted grower means:

| Grower | Cells | May–Sep precip (mm) | GDD (C-days) | Heat days >= 35 C |
|---|---:|---:|---:|---:|
{weather_table}

Iowa is the coolest and wettest of the three; Nebraska is the hottest, driest, and has the
most heat days. Precipitation ordering between Iowa and Illinois is season-sensitive, so the
report does not claim one is wetter. GDD ordering is stable across the alternate definitions.

{''.join(figure_sections['Weather'])}

## 4. Crop-weather synthesis

Corn and soybean occupy the same climate inside each grower. The paired comparison below
uses only fields that grew both crops, so location is held still; the difference still
carries year-to-year weather.

| Grower | Paired fields | Median corn − soy GDD | Median corn − soy precip (mm) |
|---|---:|---:|---:|
{paired_table}

The medians do not share a sign across growers. That is the finding: crop choice is not a
clean weather-sorting mechanism here. Location and rotation phase are.

## Assignment coverage

| Requirement | Artifacts |
|---|---|
| Statistical visualization, boundaries | B1 acreage strip, B2 area vs compactness |
| Comparison / correlation, boundaries | B3 polygon vs CDL area |
| Statistical visualization, CDL | C1 crop sequence, C3 transitions |
| Comparison / correlation, CDL | C2 crop mix by grower-year |
| Statistical visualization, weather | W1 within-field anomaly, W3 exposure panels |
| Comparison / correlation, weather | W2 grower climate space (W3 paired differences support the synthesis) |
| Geospatial map | B1 field boundary map by grower |
| Levels covered | field, field-year, grower, across-grower |

## Limits

{caveats}

## Artifact index

| Kind | ID | Category | Level | File | Job |
|---|---|---|---|---|---|
{artifact_table}

## Reproduce

```bash
export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
cd my-farm-advisor/eda/field-eda/src
"${{DATA_PIPELINE_DATA_ROOT}}/data-pipeline/.venv/bin/python" run_field_eda.py \\
  --out "${{DATA_PIPELINE_DATA_ROOT}}/data-pipeline/eda/field-eda"
cd ../examples/analysis-report
"${{DATA_PIPELINE_DATA_ROOT}}/data-pipeline/.venv/bin/python" run_report.py
```

The report step is a one-time consumer. The reusable workflow ends at tables, figures, and
the manifest.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=default_input())
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent / "output"
    )
    args = parser.parse_args()

    input_dir = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    manifest_path = input_dir / "manifests" / "field_eda_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"manifest not found: {manifest_path}. Run run_field_eda.py first."
        )

    manifest = json.loads(manifest_path.read_text())
    problems = verify_manifest(manifest, input_dir)
    if problems:
        raise SystemExit(
            "refusing to build a stale report:\n  " + "\n  ".join(problems)
        )

    figures = [item for item in manifest["outputs"] if item["path"].startswith("figures/")]
    tables = [item for item in manifest["outputs"] if item["path"].startswith("tables/")]
    if len(figures) != len(FIGURES) or len(tables) != len(TABLES):
        raise SystemExit(
            f"expected {len(FIGURES)} figures and {len(TABLES)} tables, "
            f"found {len(figures)} and {len(tables)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    for folder in ("figures", "tables"):
        target = output_dir / folder
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        for record in manifest["outputs"]:
            if record["path"].startswith(f"{folder}/"):
                shutil.copy2(input_dir / record["path"], target / Path(record["path"]).name)
    sanitized = sanitize_manifest(manifest)
    manifest_text = json.dumps(sanitized, indent=2) + "\n"
    host_root = str(manifest.get("data_root", "")).rstrip("/")
    if host_root and host_root in manifest_text:
        raise SystemExit(
            "refusing to write a manifest that still contains the host data root"
        )
    (output_dir / "field_eda_manifest.json").write_text(
        manifest_text, encoding="utf-8"
    )

    tables_frame = load_tables(input_dir)
    metrics = compute_metrics(tables_frame, manifest)

    artifact_rows: list[dict] = []
    for figure in FIGURES:
        artifact_rows.append(
            {
                "kind": "figure",
                "id": figure["id"],
                "category": figure["category"],
                "level": figure["level"],
                "file": f"figures/{figure['file']}",
                "job": figure["job"],
            }
        )
    for file_name, category, job in TABLES:
        artifact_rows.append(
            {
                "kind": "table",
                "id": "",
                "category": category,
                "level": "",
                "file": f"tables/{file_name}",
                "job": job,
            }
        )
    pd.DataFrame(artifact_rows).to_csv(output_dir / "artifact_index.csv", index=False)

    report = build_markdown(metrics, manifest, artifact_rows)
    (output_dir / "field_eda_report.md").write_text(report, encoding="utf-8")

    print(f"verified {len(figures)} figures and {len(tables)} tables")
    print(f"wrote {output_dir / 'field_eda_report.md'}")
    print(f"wrote {output_dir / 'artifact_index.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
