"""CDL composition, crop groups, and rotation summaries.

Crop grouping is explicit and documented because CDL ships 130+ classes and the
handful present here are a mix of crops, pasture, and non-crop land cover. Shares are
kept in percent-of-field pixels; crop-only share is computed by filtering, never by
assuming the residual is a crop.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from common import (
    CROP_GROUP_ORDER,
    Check,
    FarmPaths,
    check,
    common_cdl_years,
    load_boundaries,
    load_cdl_years,
    natural_rank,
)

CORN_CODE = 1
SOYBEAN_CODE = 5
DOUBLE_CROP_CODE = 26

SMALL_GRAIN_CODES = {21, 22, 23, 24, 25, 27, 28, 29, 30}
PASTURE_HAY_CODES = {36, 37, 58, 62, 176}
NONCROP_CODES = {
    0,
    61,
    63,
    64,
    65,
    81,
    82,
    83,
    87,
    88,
    92,
    111,
    112,
    121,
    122,
    123,
    124,
    131,
    141,
    142,
    143,
    152,
    190,
    195,
}
KNOWN_LAND_COVER = (
    set(range(0, 61))
    | set(range(66, 96))
    | set(range(111, 125))
    | {61, 63, 64, 65, 131, 141, 142, 143, 152, 176, 190, 195}
)

GROUP_TO_COLUMN = {
    "Corn": "corn_pct",
    "Soybeans": "soybean_pct",
    "Small grain": "small_grain_pct",
    "Pasture/Hay": "pasture_hay_pct",
    "Other crop": "other_crop_pct",
    "Non-crop": "noncrop_pct",
}
COLUMN_TO_GROUP = {value: key for key, value in GROUP_TO_COLUMN.items()}
CROP_GROUP_COLUMNS = [GROUP_TO_COLUMN[group] for group in CROP_GROUP_ORDER if group != "Non-crop"]

DEFAULT_ROTATION_START_YEAR = 2021


@dataclass
class CdlBundle:
    field_year: pd.DataFrame
    rotation: pd.DataFrame
    transitions: pd.DataFrame
    crop_mix: pd.DataFrame
    checks: list[Check]
    years: list[int]
    rotation_start_year: int


def crop_group(code: object, name: object = "") -> str:
    """Map a CDL class to one of the six display groups."""
    try:
        code_value = int(code)
    except (TypeError, ValueError):
        return "Non-crop"
    if code_value == CORN_CODE:
        return "Corn"
    if code_value == SOYBEAN_CODE:
        return "Soybeans"
    if code_value in NONCROP_CODES:
        return "Non-crop"
    if code_value in SMALL_GRAIN_CODES:
        return "Small grain"
    if code_value in PASTURE_HAY_CODES:
        return "Pasture/Hay"
    if 1 <= code_value <= 60 or 66 <= code_value <= 80:
        return "Other crop"
    return "Non-crop"


def _index_columns() -> list[str]:
    return ["grower", "state", "farm", "field_id", "year"]


def build_field_year(cdl: pd.DataFrame) -> pd.DataFrame:
    frame = cdl.copy()
    frame["group"] = [
        crop_group(code, name)
        for code, name in zip(frame["crop_code"], frame["crop_name"])
    ]

    index_cols = _index_columns()
    pivot = (
        frame.pivot_table(
            index=index_cols,
            columns="group",
            values="pct",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reindex(columns=CROP_GROUP_ORDER, fill_value=0.0)
        .rename(columns=GROUP_TO_COLUMN)
        .reset_index()
    )

    pixels = (
        frame.groupby(index_cols, as_index=False)
        .agg(pixels_total=("pixel_count", "sum"))
    )
    crop_pixels = (
        frame.loc[frame["group"] != "Non-crop"]
        .groupby(index_cols, as_index=False)
        .agg(crop_pixels=("pixel_count", "sum"))
    )
    double_crop = (
        frame.loc[frame["crop_code"] == DOUBLE_CROP_CODE]
        .groupby(index_cols, as_index=False)
        .agg(dbl_crop_pct=("pct", "sum"))
    )

    field_year = (
        pivot.merge(pixels, on=index_cols, how="left", validate="1:1")
        .merge(crop_pixels, on=index_cols, how="left", validate="1:1")
        .merge(double_crop, on=index_cols, how="left", validate="1:1")
    )
    field_year["crop_pixels"] = field_year["crop_pixels"].fillna(0).astype(int)
    field_year["dbl_crop_pct"] = field_year["dbl_crop_pct"].fillna(0.0)
    field_year["noncrop_pixels"] = field_year["pixels_total"] - field_year["crop_pixels"]
    field_year["crop_share_pct"] = (
        field_year["corn_pct"]
        + field_year["soybean_pct"]
        + field_year["small_grain_pct"]
        + field_year["pasture_hay_pct"]
        + field_year["other_crop_pct"]
    )

    crop_values = field_year[CROP_GROUP_COLUMNS]
    field_year["dominant_crop"] = (
        crop_values.idxmax(axis=1).map(COLUMN_TO_GROUP)
    )
    field_year["dominant_pct"] = crop_values.max(axis=1)

    ordered = [
        "grower",
        "state",
        "farm",
        "field_id",
        "year",
        "pixels_total",
        "crop_pixels",
        "noncrop_pixels",
        "crop_share_pct",
        "corn_pct",
        "soybean_pct",
        "small_grain_pct",
        "pasture_hay_pct",
        "other_crop_pct",
        "noncrop_pct",
        "dbl_crop_pct",
        "dominant_crop",
        "dominant_pct",
    ]
    field_year = field_year[ordered]
    field_year["year"] = field_year["year"].astype(int)
    field_year = field_year.assign(_order=natural_rank(field_year["field_id"]))
    return (
        field_year.sort_values(["state", "_order", "year"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )


def build_rotation(
    field_year: pd.DataFrame, start_year: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected = field_year.loc[field_year["year"] >= start_year].copy()
    index_cols = ["grower", "state", "farm", "field_id"]
    rotation_rows = []
    transition_rows = []
    for keys, group in selected.groupby(index_cols, sort=False):
        group = group.sort_values("year")
        years = group["year"].tolist()
        crops = group["dominant_crop"].tolist()
        pairs = list(zip(crops, crops[1:]))
        pair_years = list(zip(years, years[1:]))
        n_transitions = len(pairs)
        alternations = sum({left, right} == {"Corn", "Soybeans"} for left, right in pairs)
        continuous_corn = sum(left == "Corn" and right == "Corn" for left, right in pairs)
        changes = sum(left != right for left, right in pairs)
        grower, state, farm, field_id = keys
        rotation_rows.append(
            {
                "grower": grower,
                "state": state,
                "farm": farm,
                "field_id": field_id,
                "start_year": int(min(years)),
                "end_year": int(max(years)),
                "years_observed": len(years),
                "sequence": " -> ".join(crops),
                "n_transitions": n_transitions,
                "alternation_rate": (
                    alternations / n_transitions if n_transitions else np.nan
                ),
                "change_rate": changes / n_transitions if n_transitions else np.nan,
                "continuous_corn_transitions": continuous_corn,
                "has_continuous_corn": bool(continuous_corn > 0),
                "crop_diversity": len(set(crops)),
            }
        )
        for (left, right), (from_year, to_year) in zip(pairs, pair_years):
            transition_rows.append(
                {
                    "grower": grower,
                    "state": state,
                    "farm": farm,
                    "field_id": field_id,
                    "from_year": int(from_year),
                    "to_year": int(to_year),
                    "from_crop": left,
                    "to_crop": right,
                    "changed": bool(left != right),
                }
            )

    rotation = pd.DataFrame(rotation_rows)
    rotation = rotation.assign(_order=natural_rank(rotation["field_id"]))
    rotation = (
        rotation.sort_values(["state", "_order"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )
    transitions = pd.DataFrame(transition_rows)
    transitions = transitions.assign(_order=natural_rank(transitions["field_id"]))
    transitions = (
        transitions.sort_values(["state", "_order", "from_year"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )
    return rotation, transitions


def build_crop_mix(field_year: pd.DataFrame) -> pd.DataFrame:
    columns = [GROUP_TO_COLUMN[group] for group in CROP_GROUP_ORDER]
    mix = (
        field_year.groupby(["grower", "state", "year"], as_index=False)
        .agg(
            fields=("field_id", "nunique"),
            **{column: (column, "mean") for column in columns},
        )
    )
    return mix.sort_values(["state", "year"]).reset_index(drop=True)


def _validate(
    cdl: pd.DataFrame,
    field_year: pd.DataFrame,
    rotation: pd.DataFrame,
    farms: list[FarmPaths],
    years: list[int],
) -> list[Check]:
    checks: list[Check] = []
    n_fields = int(field_year["field_id"].nunique())

    per_year = field_year.groupby("year")["field_id"].nunique()
    complete = bool((per_year == n_fields).all()) and sorted(per_year.index) == sorted(years)
    checks.append(
        check(
            "every field has every CDL year",
            f"{n_fields} fields x {len(years)} years",
            per_year.to_dict(),
            complete,
        )
    )

    sums = cdl.groupby(["field_id", "year"])["pct"].sum()
    sums_ok = bool((sums >= 99.5).all() and (sums <= 100.5).all())
    checks.append(
        check(
            "CDL shares sum to 100",
            "99.5-100.5%",
            f"{sums.min():.2f}-{sums.max():.2f}%",
            sums_ok,
        )
    )

    bad_names = int(
        cdl["crop_name"].astype(str).str.startswith("Code_").sum()
    )
    checks.append(check("no unmapped Code_* labels", 0, bad_names, bad_names == 0))

    known_codes = set(int(code) for code in cdl["crop_code"].unique())
    unknown_codes = sorted(known_codes - KNOWN_LAND_COVER)
    checks.append(
        check(
            "CDL codes are known land-cover classes",
            "all known",
            unknown_codes if unknown_codes else "all known",
            not unknown_codes,
        )
    )

    dominant_ok = bool(field_year["dominant_crop"].isin(CROP_GROUP_ORDER[:-1]).all())
    checks.append(
        check(
            "dominant class is a crop group",
            "no Non-crop dominant",
            field_year.loc[~field_year["dominant_crop"].isin(CROP_GROUP_ORDER[:-1]), "dominant_crop"]
            .value_counts()
            .to_dict()
            or "none",
            dominant_ok,
        )
    )

    pixel_spread = (
        field_year.groupby("field_id")["pixels_total"].agg(lambda s: int(s.max() - s.min()))
    )
    checks.append(
        check(
            "CDL pixel total stable across years",
            0,
            int(pixel_spread.max()),
            int(pixel_spread.max()) == 0,
        )
    )

    reference_year = 2023 if 2023 in years else max(years)
    csb = pd.concat(
        [load_boundaries(farm)[["field_id", "crop"]] for farm in farms], ignore_index=True
    )
    reference = field_year.loc[
        field_year["year"] == reference_year, ["field_id", "dominant_crop"]
    ]
    merged = reference.merge(csb, on="field_id", how="inner", validate="1:1")
    agreements = int(
        (
            merged["dominant_crop"].str.lower()
            == merged["crop"].astype(str).str.lower()
        ).sum()
    )
    checks.append(
        Check(
            name=f"CDL {reference_year} majority matches CSB crop attribute",
            status="pass" if agreements == len(merged) else "fail",
            expected=str(len(merged)),
            observed=str(agreements),
            detail="Consistency check only: the CSB crop attribute is CDL-derived, not independent.",
        )
    )

    checks.append(
        check(
            "rotation rows match field count",
            n_fields,
            int(len(rotation)),
            len(rotation) == n_fields,
        )
    )
    return checks


def run(
    farms: list[FarmPaths],
    years: list[int] | None = None,
    rotation_start_year: int = DEFAULT_ROTATION_START_YEAR,
) -> CdlBundle:
    years = years or common_cdl_years(farms)
    frames = [load_cdl_years(farm, years) for farm in farms]
    cdl = pd.concat(frames, ignore_index=True)

    field_year = build_field_year(cdl)
    rotation, transitions = build_rotation(field_year, rotation_start_year)
    crop_mix = build_crop_mix(field_year)
    checks = _validate(cdl, field_year, rotation, farms, years)
    return CdlBundle(
        field_year=field_year,
        rotation=rotation,
        transitions=transitions,
        crop_mix=crop_mix,
        checks=checks,
        years=years,
        rotation_start_year=rotation_start_year,
    )
