"""Join CDL crop labels to growing-season weather and pair crops within a field.

Weather is assigned by location and year, not by crop. This module therefore keeps two
grains separate:

* field-year: the joined exposure table, one row per field and year.
* field: corn-year mean minus soybean-year mean, only for fields that grew both crops.

The paired difference is descriptive. A field's corn and soybean years are different
years, so the difference also carries year-to-year weather.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from common import Check, check, natural_rank, state_order

CROPS = ("Corn", "Soybeans")
PAIRED_METRICS = ("precip_mm", "gdd_c", "years")

WEATHER_COLUMNS = [
    "grower",
    "state",
    "farm",
    "field_id",
    "year",
    "weather_cell",
    "precip_mm",
    "gdd_c",
    "heat_days_35c",
]
CROP_COLUMNS = ["field_id", "year", "dominant_crop", "dominant_pct"]

FIELD_YEAR_ORDER = [
    "grower",
    "state",
    "farm",
    "field_id",
    "year",
    "weather_cell",
    "dominant_crop",
    "crop_share_pct",
    "precip_mm",
    "gdd_c",
    "heat_days_35c",
]


@dataclass
class CropWeatherBundle:
    field_year: pd.DataFrame
    paired: pd.DataFrame
    checks: list[Check]


def _sort_by_site(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    ordered = frame.assign(
        _state=frame["state"].map(state_order),
        _order=natural_rank(frame["field_id"]),
    )
    return (
        ordered.sort_values(["_state", "_order", *columns])
        .drop(columns=["_state", "_order"])
        .reset_index(drop=True)
    )


def join_crop_weather(
    weather_field_year: pd.DataFrame, cdl_field_year: pd.DataFrame
) -> pd.DataFrame:
    """Attach the CDL dominant crop to each weather field-year, one row per field-year."""
    weather = weather_field_year[WEATHER_COLUMNS].copy()
    crop = cdl_field_year[CROP_COLUMNS].rename(
        columns={"dominant_pct": "crop_share_pct"}
    )
    joined = weather.merge(
        crop, on=["field_id", "year"], how="left", validate="one_to_one"
    )
    joined["dominant_crop"] = joined["dominant_crop"].astype("string")
    return _sort_by_site(joined[FIELD_YEAR_ORDER], ["year"])


def paired_differences(crop_weather: pd.DataFrame) -> pd.DataFrame:
    """Corn-year mean minus soybean-year mean for each field that grew both crops."""
    grouped = (
        crop_weather.groupby(
            ["grower", "state", "field_id", "dominant_crop"], sort=False
        )
        .agg(
            years=("year", "nunique"),
            precip_mm=("precip_mm", "mean"),
            gdd_c=("gdd_c", "mean"),
        )
        .reset_index()
    )
    wide = grouped.pivot_table(
        index=["grower", "state", "field_id"],
        columns="dominant_crop",
        values=list(PAIRED_METRICS),
        aggfunc="first",
    )
    wide.columns = [f"{metric}_{crop}".lower() for metric, crop in wide.columns]
    wide = wide.reset_index()

    required = [f"{metric}_{crop}" for metric in PAIRED_METRICS for crop in ("corn", "soybeans")]
    for column in required:
        if column not in wide.columns:
            wide[column] = float("nan")

    paired = wide.dropna(subset=required).copy()
    paired["delta_precip_mm"] = paired["precip_mm_corn"] - paired["precip_mm_soybeans"]
    paired["delta_gdd_c"] = paired["gdd_c_corn"] - paired["gdd_c_soybeans"]
    return _sort_by_site(
        paired[
            [
                "grower",
                "state",
                "field_id",
                "years_corn",
                "years_soybeans",
                "precip_mm_corn",
                "precip_mm_soybeans",
                "gdd_c_corn",
                "gdd_c_soybeans",
                "delta_precip_mm",
                "delta_gdd_c",
            ]
        ],
        [],
    )


def _checks(
    weather: pd.DataFrame,
    crop_weather: pd.DataFrame,
    paired: pd.DataFrame,
) -> list[Check]:
    matched = int(crop_weather["dominant_crop"].notna().sum())
    observed = sorted(crop_weather["dominant_crop"].dropna().unique())
    unexpected = sorted(set(observed) - set(CROPS))

    growers = set(crop_weather["grower"])
    paired_growers = set(paired["grower"])

    return [
        check(
            "crop-weather join complete",
            len(weather),
            matched,
            matched == len(weather),
            "Every weather field-year must carry a CDL crop label.",
        ),
        check(
            "crop labels are corn or soybean",
            ", ".join(CROPS),
            ", ".join(observed),
            not unexpected,
            f"Unexpected crop labels: {unexpected}" if unexpected else "",
        ),
        check(
            "paired crop fields exist",
            "> 0",
            len(paired),
            len(paired) > 0,
            "At least one field must have grown both crops.",
        ),
        check(
            "every grower has a paired field",
            len(growers),
            len(paired_growers),
            paired_growers == growers,
            f"Growers without a paired field: {sorted(growers - paired_growers)}"
            if paired_growers != growers
            else "",
        ),
    ]


def build(
    weather_field_year: pd.DataFrame, cdl_field_year: pd.DataFrame
) -> CropWeatherBundle:
    """Join, validate, and pair crops for one weather run."""
    crop_weather = join_crop_weather(weather_field_year, cdl_field_year)
    paired = paired_differences(crop_weather)
    checks = _checks(weather_field_year, crop_weather, paired)
    return CropWeatherBundle(field_year=crop_weather, paired=paired, checks=checks)


def summarize(
    crop_weather: pd.DataFrame, paired: pd.DataFrame
) -> dict[str, object]:
    """Manifest counts for the crop-weather comparison."""
    by_crop = crop_weather["dominant_crop"].value_counts()
    by_state = paired.groupby("state")["field_id"].nunique()
    return {
        "crop_weather_field_years": int(len(crop_weather)),
        "corn_field_years": int(by_crop.get("Corn", 0)),
        "soybean_field_years": int(by_crop.get("Soybeans", 0)),
        "paired_crop_fields": int(len(paired)),
        "paired_crop_fields_by_state": {
            str(state): int(count) for state, count in by_state.items()
        },
    }
