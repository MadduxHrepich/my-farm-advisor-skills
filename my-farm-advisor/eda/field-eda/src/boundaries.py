"""Boundary metrics: size, shape, dispersion, and the CDL pixel-area check.

The polygon is the unit of analysis, so this module establishes:
* that each field is a valid, unique polygon,
* how large and how compact it is,
* whether the CDL raster area agrees with the polygon area closely enough that CDL
  percentages can be trusted as shares of the field.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

from common import (
    ACRE_M2,
    EQUAL_AREA_CRS,
    Check,
    FarmPaths,
    check,
    common_cdl_years,
    load_boundaries,
    natural_rank,
)


@dataclass
class BoundariesBundle:
    boundaries: gpd.GeoDataFrame
    field_summary: pd.DataFrame
    checks: list[Check]


def _cdl_pixel_area(farms: list[FarmPaths], year: int) -> pd.DataFrame:
    frames = []
    for farm in farms:
        table = pd.read_csv(farm.cdl_paths[year], usecols=["field_id", "pixel_count"])
        totals = (
            table.groupby("field_id", as_index=False)["pixel_count"]
            .sum()
            .rename(columns={"pixel_count": "cdl_pixels"})
        )
        frames.append(totals)
    combined = pd.concat(frames, ignore_index=True)
    combined["cdl_pixel_area_acres"] = combined["cdl_pixels"] * 900.0 / ACRE_M2
    return combined


def run(farms: list[FarmPaths]) -> BoundariesBundle:
    pieces = []
    for farm in farms:
        boundaries = load_boundaries(farm)
        boundaries["grower"] = farm.state_label
        boundaries["state"] = farm.state
        boundaries["farm"] = farm.farm_slug
        boundaries["county"] = farm.county
        pieces.append(boundaries)

    boundaries = gpd.GeoDataFrame(
        pd.concat(pieces, ignore_index=True), geometry="geometry", crs="EPSG:4326"
    )

    projected = boundaries.to_crs(EQUAL_AREA_CRS)
    boundaries["area_m2"] = projected.geometry.area.astype(float)
    boundaries["perimeter_m"] = projected.geometry.length.astype(float)
    boundaries["acres"] = boundaries["area_m2"] / ACRE_M2
    boundaries["area_ha"] = boundaries["area_m2"] / 10_000.0
    boundaries["perimeter_ft"] = boundaries["perimeter_m"] / 0.3048
    boundaries["compactness"] = (
        4.0 * np.pi * boundaries["area_m2"] / boundaries["perimeter_m"] ** 2
    ).clip(upper=1.0)
    centroids = projected.geometry.centroid.to_crs("EPSG:4326")
    boundaries["centroid_lon"] = centroids.x.to_numpy()
    boundaries["centroid_lat"] = centroids.y.to_numpy()

    cdl_years = common_cdl_years(farms)
    latest_year = max(cdl_years)
    pixel_area = _cdl_pixel_area(farms, latest_year)
    summary = boundaries.drop(columns="geometry").merge(
        pixel_area, on="field_id", how="left", validate="1:1"
    )
    summary["area_dev_pct"] = (
        (summary["cdl_pixel_area_acres"] - summary["acres"]) / summary["acres"] * 100.0
    )
    if "area_acres" in summary.columns:
        summary["attribute_area_dev_pct"] = (
            (summary["area_acres"] - summary["acres"]) / summary["acres"] * 100.0
        )
    else:
        summary["attribute_area_dev_pct"] = np.nan
    summary["cdl_year"] = latest_year
    summary = summary.assign(_order=natural_rank(summary["field_id"]))
    summary = (
        summary.sort_values(["state", "_order"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )

    columns = [
        "grower",
        "state",
        "farm",
        "county",
        "field_id",
        "acres",
        "area_ha",
        "perimeter_ft",
        "compactness",
        "centroid_lat",
        "centroid_lon",
        "cdl_year",
        "cdl_pixels",
        "cdl_pixel_area_acres",
        "area_dev_pct",
        "attribute_area_dev_pct",
    ]
    field_summary = summary[columns].copy()

    checks = _validate(boundaries, field_summary)
    return BoundariesBundle(
        boundaries=boundaries, field_summary=field_summary, checks=checks
    )


def _validate(boundaries: gpd.GeoDataFrame, summary: pd.DataFrame) -> list[Check]:
    checks: list[Check] = []
    total = len(summary)
    unique = int(summary["field_id"].nunique())
    checks.append(check("unique field ids", total, unique, unique == total))

    valid = int(boundaries.geometry.is_valid.sum())
    checks.append(check("valid polygons", total, valid, valid == total))

    non_empty = int((~boundaries.geometry.is_empty).sum())
    checks.append(check("non-empty polygons", total, non_empty, non_empty == total))

    positive = int((boundaries["area_m2"] > 0).sum())
    checks.append(check("positive area", total, positive, positive == total))

    compactness_ok = bool(
        ((summary["compactness"] > 0) & (summary["compactness"] <= 1.0)).all()
    )
    checks.append(
        check(
            "compactness in (0, 1]",
            "0 < c <= 1",
            f"{summary['compactness'].min():.3f}-{summary['compactness'].max():.3f}",
            compactness_ok,
        )
    )

    attr_dev = summary["attribute_area_dev_pct"].abs()
    attr_max = float(attr_dev.max()) if attr_dev.notna().any() else 0.0
    checks.append(
        check(
            "computed area matches boundary attribute",
            "<= 2% deviation",
            f"max {attr_max:.2f}%",
            attr_max <= 2.0,
        )
    )

    pixel_dev = summary["area_dev_pct"].abs()
    pixel_max = float(pixel_dev.max())
    checks.append(
        check(
            "CDL pixel area matches polygon",
            "<= 2% deviation",
            f"max {pixel_max:.2f}%",
            pixel_max <= 2.0,
        )
    )

    checks.append(
        check(
            "CDL area available for every field",
            0,
            int(summary["cdl_pixel_area_acres"].isna().sum()),
            summary["cdl_pixel_area_acres"].notna().all(),
        )
    )
    return checks
