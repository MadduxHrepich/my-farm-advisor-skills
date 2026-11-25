#!/usr/bin/env python3
"""Run the reusable field-level EDA workflow.

Examples
--------
Dry run (writes nothing)::

    python run_field_eda.py --dry-run

Weather only while iterating::

    python run_field_eda.py --category weather

Full run::

    python run_field_eda.py --category all
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

import crop_weather as crop_weather_mod
import weather as weather_mod
from common import (
    Check,
    FarmPaths,
    checks_status,
    common_cdl_years,
    common_weather_years,
    data_root,
    discover_farms,
    ensure_inside_runtime,
    load_boundaries,
    load_weather,
    raise_on_fail,
    sha256_file,
    utc_now,
    write_json,
)

CATEGORIES = ("all", "boundaries", "cdl", "weather")


@dataclass
class WeatherBundle:
    daily: pd.DataFrame
    coords: pd.DataFrame
    field_year: pd.DataFrame
    cell_year: pd.DataFrame
    grower_year: pd.DataFrame
    sensitivity: pd.DataFrame
    stability: dict
    checks: list[Check]
    qc: pd.DataFrame
    years: list[int] = field(default_factory=list)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Field-level EDA across boundaries, CDL, and growing-season weather.",
    )
    parser.add_argument(
        "--grower",
        action="append",
        default=None,
        help="Grower slug to include; repeatable. Defaults to every grower under the data root.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory inside the runtime data root. Defaults to "
        "<data root>/data-pipeline/eda/field-eda.",
    )
    parser.add_argument(
        "--category",
        choices=CATEGORIES,
        default="all",
        help="Limit the run to one category (default: all).",
    )
    parser.add_argument(
        "--season-start",
        default="05-01",
        help="Primary season start as MM-DD (default: 05-01).",
    )
    parser.add_argument(
        "--season-end",
        default="09-30",
        help="Primary season end as MM-DD (default: 09-30).",
    )
    parser.add_argument(
        "--gdd-base-c",
        type=float,
        default=weather_mod.GDD_BASE_C,
        help="GDD base temperature in C (default: 10).",
    )
    parser.add_argument(
        "--gdd-cap-c",
        type=float,
        default=weather_mod.GDD_CAP_C,
        help="GDD upper cap on the daily mean in C (default: 30).",
    )
    parser.add_argument(
        "--cdl-start-year",
        type=int,
        default=2019,
        help="Earliest CDL year to include when years are shared across farms (default: 2019).",
    )
    parser.add_argument(
        "--rotation-start-year",
        type=int,
        default=2021,
        help="First year of the rotation window, aligned with the weather window (default: 2021).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print discovery, counts, and planned outputs; write nothing.",
    )
    parser.add_argument(
        "--tables-only",
        action="store_true",
        help="Write tables and manifest but skip figures.",
    )
    return parser.parse_args(argv)


def resolve_output(args: argparse.Namespace) -> tuple[Path, Path]:
    root = data_root()
    raw = args.out or str(root / "data-pipeline" / "eda" / "field-eda")
    out_dir = ensure_inside_runtime(Path(raw), root)
    return root, out_dir


def expected_field_ids(farms: list[FarmPaths]) -> set[str]:
    fields: set[str] = set()
    for farm in farms:
        boundaries = load_boundaries(farm)
        fields.update(boundaries["field_id"].astype(str))
    return fields


def build_weather_bundle(
    farms: list[FarmPaths],
    start: tuple[int, int],
    end: tuple[int, int],
    base: float,
    cap: float,
) -> WeatherBundle:
    frames = [load_weather(farm) for farm in farms]
    daily = pd.concat(frames, ignore_index=True)
    years = common_weather_years(daily)

    coords = weather_mod.field_coordinates(daily)
    daily = weather_mod.add_weather_cell(daily, coords)

    checks, qc = weather_mod.qc_weather(
        daily, expected_field_ids(farms), years, start=start, end=end
    )
    raise_on_fail(checks, "weather")

    field_year = weather_mod.summarize_field_year(
        daily, coords, start=start, end=end, base=base, cap=cap
    )
    cell_year = weather_mod.summarize_cell_year(field_year)
    grower_year = weather_mod.grower_year_means(cell_year)
    sensitivity, stability = weather_mod.sensitivity_table(cell_year)

    return WeatherBundle(
        daily=daily,
        coords=coords,
        field_year=field_year,
        cell_year=cell_year,
        grower_year=grower_year,
        sensitivity=sensitivity,
        stability=stability,
        checks=checks,
        qc=qc,
        years=years,
    )


def write_table(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format="%.4f")
    return path


def run_weather(
    bundle: WeatherBundle,
    crop_bundle: crop_weather_mod.CropWeatherBundle,
    out_dir: Path,
    tables_only: bool,
) -> tuple[list[Path], dict]:
    tables_dir = out_dir / "tables"
    figures_dir = out_dir / "figures"
    written: list[Path] = []

    written.append(write_table(bundle.field_year, tables_dir / "weather_field_year.csv"))
    written.append(write_table(bundle.cell_year, tables_dir / "weather_cell_year.csv"))
    written.append(
        write_table(crop_bundle.field_year, tables_dir / "crop_weather.csv")
    )
    written.append(write_table(bundle.qc, tables_dir / "weather_qc.csv"))
    written.append(
        write_table(bundle.sensitivity, tables_dir / "weather_sensitivity.csv")
    )
    # The pairwise table existed only to feed the retired W3 sharing figure.
    retired_table = tables_dir / "weather_pairwise.csv"
    if retired_table.exists():
        retired_table.unlink()

    if not tables_only:
        import plots_weather

        written.extend(
            plots_weather.build_all(
                bundle.field_year,
                bundle.cell_year,
                bundle.grower_year,
                crop_bundle.field_year,
                crop_bundle.paired,
                figures_dir,
            )
        )
        # W3 changed job from weather sharing to crop-weather exposure. Remove the
        # retired outputs so a re-run leaves exactly the planned figure set.
        for retired_name in (
            "w3_weather_similarity_vs_distance.png",
            "w3_weather_sharing_tiers.png",
        ):
            retired = figures_dir / retired_name
            if retired.exists():
                retired.unlink()

    summary = {
        "fields": int(bundle.field_year["field_id"].nunique()),
        "field_years": int(len(bundle.field_year)),
        "weather_cells": int(bundle.cell_year["weather_cell"].nunique()),
        "cell_years": int(len(bundle.cell_year)),
        "weather_years": [int(year) for year in bundle.years],
        "sensitivity_stable": bundle.stability,
    }
    summary.update(
        crop_weather_mod.summarize(crop_bundle.field_year, crop_bundle.paired)
    )
    return written, summary


def print_dry_run(
    farms: list[FarmPaths],
    out_dir: Path,
    args: argparse.Namespace,
    bundle: WeatherBundle | None,
) -> None:
    print("field-eda dry run (no files will be written)")
    print(f"output: {out_dir}")
    print("farms:")
    for farm in farms:
        print(
            f"  {farm.state_label:<10} {farm.grower_slug}/{farm.farm_slug}  "
            f"fields={farm.field_count}  weather={farm.weather_path.name}  "
            f"cdl_years={farm.cdl_years}"
        )
    if bundle is not None:
        print(
            "weather:"
            f" rows={len(bundle.daily)}"
            f" years={bundle.years}"
            f" cells={bundle.cell_year['weather_cell'].nunique()}"
            f" field-years={len(bundle.field_year)}"
            f" qc={checks_status(bundle.checks)}"
        )
    planned = [f"tables/{name}" for name in _planned_tables(args.category)]
    if not args.tables_only:
        planned.extend(f"figures/{name}" for name in _planned_figures(args.category))
    planned.append("manifests/field_eda_manifest.json")
    print("planned outputs:")
    for path in planned:
        print(f"  {path}")


def _planned_tables(category: str) -> list[str]:
    tables = {
        "weather": [
            "weather_field_year.csv",
            "weather_cell_year.csv",
            "crop_weather.csv",
            "weather_qc.csv",
            "weather_sensitivity.csv",
        ],
        "boundaries": ["field_summary.csv"],
        "cdl": ["cdl_field_year.csv", "rotation_summary.csv"],
        "all": [
            "field_summary.csv",
            "cdl_field_year.csv",
            "rotation_summary.csv",
            "weather_field_year.csv",
            "weather_cell_year.csv",
            "crop_weather.csv",
            "weather_qc.csv",
            "weather_sensitivity.csv",
            "grower_profile.csv",
        ],
    }
    return tables[category]


def _planned_figures(category: str) -> list[str]:
    figures = {
        "weather": [
            "w1_within_field_precip_anomaly.png",
            "w2_climate_space.png",
            "w3_crop_weather_exposure.png",
        ],
        "boundaries": [
            "b1_field_map_and_acreage.png",
            "b2_area_vs_compactness.png",
            "b3_polygon_vs_cdl_area.png",
        ],
        "cdl": [
            "c1_crop_sequence_heatmap.png",
            "c2_crop_mix_by_grower_year.png",
            "c3_transition_matrix.png",
        ],
        "all": [
            "b1_field_map_and_acreage.png",
            "b2_area_vs_compactness.png",
            "b3_polygon_vs_cdl_area.png",
            "c1_crop_sequence_heatmap.png",
            "c2_crop_mix_by_grower_year.png",
            "c3_transition_matrix.png",
            "w1_within_field_precip_anomaly.png",
            "w2_climate_space.png",
            "w3_crop_weather_exposure.png",
        ],
    }
    return figures[category]


def build_manifest(
    args: argparse.Namespace,
    root: Path,
    out_dir: Path,
    farms: list[FarmPaths],
    figures_written: list[Path],
    table_written: list[Path],
    weather_summary: dict | None,
    boundaries_summary: dict | None,
    cdl_summary: dict | None,
    checks: list[Check],
    cdl_years: list[int],
) -> dict:
    counts: dict[str, object] = {"fields": sum(farm.field_count for farm in farms)}
    if weather_summary:
        counts.update(weather_summary)
    if boundaries_summary:
        counts.update(boundaries_summary)
    if cdl_summary:
        counts.update(cdl_summary)

    caveats = [
        "Fields are the largest CSB blocks per county, not a random sample; treat group "
        "differences as descriptions of these three cases.",
        "Weather is sampled on a small number of grid cells. Fields sharing a cell have "
        "identical daily series, so field counts overstate independent weather samples.",
        "Season-definition sensitivity is reported in weather_sensitivity.csv; a precipitation "
        "ranking may change between May-Sep and Apr-Oct.",
        "Crop-weather differences pair corn years with soybean years on the same field, so "
        "they also carry year-to-year weather; they are not a crop effect.",
        "No yield data is used. Weather exposure is not yield, and no yield claim is made.",
        "Crop transitions describe observed CDL history only. No next-crop prediction is made.",
        "No p-values or causal claims. Co-occurrence is not causation.",
    ]
    if cdl_summary:
        caveats.insert(
            3,
            "CDL composition includes non-crop classes so shares sum to about 100%; crop-only "
            "share must be computed by filtering, not from a residual.",
        )

    written = [path for path in (table_written + figures_written) if path.exists()]
    output_records = [
        {
            "path": str(path.relative_to(out_dir)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(written)
    ]

    return {
        "tool": "field-eda",
        "format_version": 1,
        "generated_at_utc": utc_now(),
        "command": " ".join([Path(sys.executable).name, *sys.argv]),
        "data_root": str(root),
        "output_dir": str(out_dir),
        "category": args.category,
        "definitions": {
            "season_start": args.season_start,
            "season_end": args.season_end,
            "sensitivity_season": "04-01 to 10-31",
            "gdd_base_c": args.gdd_base_c,
            "gdd_cap_c": args.gdd_cap_c,
            "gdd_uncapped_sensitivity": True,
            "heat_day_threshold_c": weather_mod.HEAT_THRESHOLD_C,
            "rain_day_mm": weather_mod.RAIN_DAY_MM,
            "heavy_rain_day_mm": weather_mod.HEAVY_RAIN_MM,
            "grower_weather_weighting": "cell-weighted",
            "crop_weather_crops": list(crop_weather_mod.CROPS),
            "crop_weather_window": "primary season, May-Sep",
            "crop_weather_pairing": "per field, mean by crop, corn minus soybean",
            "cdl_years": cdl_years,
            "rotation_start_year": args.rotation_start_year,
        },
        "inputs": {
            "growers": [farm.grower_slug for farm in farms],
            "farms": [farm.farm_slug for farm in farms],
            "fields": sum(farm.field_count for farm in farms),
            "weather_tables": [str(farm.weather_path) for farm in farms],
            "weather_cells": (
                int(weather_summary["weather_cells"]) if weather_summary else None
            ),
            "cdl_tables": [str(path) for farm in farms for path in farm.cdl_paths.values()],
        },
        "counts": counts,
        "qc": {
            "status": checks_status(checks) if checks else "pass",
            "checks": [item.as_dict() for item in checks],
        },
        "outputs": output_records,
        "caveats": caveats,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    start = weather_mod.parse_mmdd(args.season_start)
    end = weather_mod.parse_mmdd(args.season_end)
    if start > end:
        raise SystemExit("--season-start must not be after --season-end")
    if args.gdd_cap_c < args.gdd_base_c:
        raise SystemExit("--gdd-cap-c must be >= --gdd-base-c")

    root, out_dir = resolve_output(args)
    # W3 joins weather to the CDL crop label, so a weather run also needs CDL.
    require_cdl = args.category in ("all", "cdl", "weather")
    farms = discover_farms(args.grower, root=root, require_cdl=require_cdl)
    cdl_years = common_cdl_years(farms, start_year=args.cdl_start_year) if require_cdl else []

    weather_bundle: WeatherBundle | None = None
    boundaries_bundle = None
    cdl_bundle = None
    checks: list[Check] = []

    if args.category in ("all", "weather"):
        weather_bundle = build_weather_bundle(
            farms, start, end, args.gdd_base_c, args.gdd_cap_c
        )
        checks.extend(weather_bundle.checks)

    if args.dry_run:
        print_dry_run(farms, out_dir, args, weather_bundle)
        return 0

    # CDL is an input to the crop-weather figure, so build it once and reuse it.
    if require_cdl:
        import cdl

        cdl_bundle = cdl.run(
            farms, cdl_years, rotation_start_year=args.rotation_start_year
        )
        checks.extend(cdl_bundle.checks)
        raise_on_fail(cdl_bundle.checks, "cdl")

    tables_written: list[Path] = []
    figures_written: list[Path] = []
    weather_summary = None
    boundaries_summary = None
    cdl_summary = None

    if weather_bundle is not None:
        crop_bundle = crop_weather_mod.build(
            weather_bundle.field_year, cdl_bundle.field_year
        )
        checks.extend(crop_bundle.checks)
        raise_on_fail(crop_bundle.checks, "crop-weather")
        written, weather_summary = run_weather(
            weather_bundle, crop_bundle, out_dir, args.tables_only
        )
        for path in written:
            if path.parent.name == "figures":
                figures_written.append(path)
            else:
                tables_written.append(path)
        print(
            f"weather: {weather_summary['fields']} fields, "
            f"{weather_summary['field_years']} field-years, "
            f"{weather_summary['weather_cells']} cells, "
            f"{weather_summary['paired_crop_fields']} paired crop fields"
        )
        print(f"  qc: {checks_status(weather_bundle.checks)}")

    if args.category in ("all", "boundaries"):
        import boundaries
        import plots_boundaries

        boundaries_bundle = boundaries.run(farms)
        checks.extend(boundaries_bundle.checks)
        raise_on_fail(boundaries_bundle.checks, "boundaries")
        tables_written.append(
            write_table(boundaries_bundle.field_summary, out_dir / "tables" / "field_summary.csv")
        )
        if not args.tables_only:
            figures_written.extend(
                plots_boundaries.build_all(
                    boundaries_bundle.field_summary,
                    boundaries_bundle.boundaries,
                    out_dir / "figures",
                )
            )
        boundaries_summary = {
            "boundary_fields": int(len(boundaries_bundle.field_summary)),
            "boundary_acres_median": round(
                float(boundaries_bundle.field_summary["acres"].median()), 2
            ),
        }
        print(f"boundaries: {boundaries_summary['boundary_fields']} fields")

    if args.category in ("all", "cdl"):
        import plots_cdl

        tables_written.append(
            write_table(cdl_bundle.field_year, out_dir / "tables" / "cdl_field_year.csv")
        )
        tables_written.append(
            write_table(cdl_bundle.rotation, out_dir / "tables" / "rotation_summary.csv")
        )
        if not args.tables_only:
            figures_written.extend(
                plots_cdl.build_all(
                    cdl_bundle.field_year,
                    cdl_bundle.rotation,
                    cdl_bundle.transitions,
                    cdl_bundle.crop_mix,
                    out_dir / "figures",
                )
            )
        cdl_summary = {
            "cdl_field_years": int(len(cdl_bundle.field_year)),
            "cdl_years": cdl_years,
            "rotation_start_year": args.rotation_start_year,
            "rotation_alternation_mean": round(
                float(cdl_bundle.rotation["alternation_rate"].mean()), 3
            ),
            "fields_with_continuous_corn": int(
                cdl_bundle.rotation["has_continuous_corn"].sum()
            ),
        }
        print(f"cdl: {cdl_summary['cdl_field_years']} field-years, years {cdl_years}")

    if args.category == "all":
        import profile

        profile_frame = profile.build(
            boundaries_bundle.field_summary,
            cdl_bundle.field_year,
            cdl_bundle.rotation,
            weather_bundle.cell_year,
        )
        tables_written.append(
            write_table(profile_frame, out_dir / "tables" / "grower_profile.csv")
        )
        print(f"profile: {len(profile_frame)} growers")

    manifest = build_manifest(
        args,
        root,
        out_dir,
        farms,
        figures_written,
        tables_written,
        weather_summary,
        boundaries_summary,
        cdl_summary,
        checks,
        cdl_years,
    )
    manifest_path = out_dir / "manifests" / "field_eda_manifest.json"
    write_json(manifest_path, manifest)

    print(f"wrote {len(tables_written)} tables and {len(figures_written)} figures")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
