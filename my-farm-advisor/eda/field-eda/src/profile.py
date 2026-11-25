"""One-row-per-grower profile used as the report handoff table.

Three operating contexts are clearer as three rows than as a three-point scatter.
Every column is a summary of a table produced elsewhere in the workflow.
"""

from __future__ import annotations

import pandas as pd

from common import state_meta


def build(
    field_summary: pd.DataFrame,
    cdl_field_year: pd.DataFrame,
    rotation: pd.DataFrame,
    cell_year: pd.DataFrame,
) -> pd.DataFrame:
    states = sorted(
        field_summary["state"].unique(), key=lambda state: state_meta(state)["order"]
    )
    rows = []
    for state in states:
        fields = field_summary.loc[field_summary["state"] == state]
        cdl = cdl_field_year.loc[cdl_field_year["state"] == state]
        rot = rotation.loc[rotation["state"] == state]
        weather = cell_year.loc[cell_year["state"] == state]
        rows.append(
            {
                "grower": str(state_meta(state)["label"]),
                "state": state,
                "county": str(fields["county"].iloc[0]),
                "fields": int(fields["field_id"].nunique()),
                "acres_total": float(fields["acres"].sum()),
                "acres_median": float(fields["acres"].median()),
                "acres_min": float(fields["acres"].min()),
                "acres_max": float(fields["acres"].max()),
                "compactness_median": float(fields["compactness"].median()),
                "cdl_area_dev_median_pct": float(fields["area_dev_pct"].median()),
                "rotation_start_year": int(rot["start_year"].min()),
                "rotation_end_year": int(rot["end_year"].max()),
                "alternation_rate_mean": float(rot["alternation_rate"].mean()),
                "fields_with_continuous_corn": int(rot["has_continuous_corn"].sum()),
                "crop_share_mean_pct": float(cdl["crop_share_pct"].mean()),
                "corn_share_mean_pct": float(cdl["corn_pct"].mean()),
                "soybean_share_mean_pct": float(cdl["soybean_pct"].mean()),
                "noncrop_share_mean_pct": float(cdl["noncrop_pct"].mean()),
                "weather_cells": int(weather["weather_cell"].nunique()),
                "precip_mm": float(weather["precip_mm"].mean()),
                "precip_min_mm": float(weather["precip_mm"].min()),
                "precip_max_mm": float(weather["precip_mm"].max()),
                "gdd_c": float(weather["gdd_c"].mean()),
                "gdd_min_c": float(weather["gdd_c"].min()),
                "gdd_max_c": float(weather["gdd_c"].max()),
                "heat_days_35c": float(weather["heat_days_35c"].mean()),
            }
        )
    return pd.DataFrame(rows)
