"""Weather QC and growing-season metrics.

Design notes
------------
* The primary season is May 1 - September 30. April-October and calendar-year values are
  sensitivity columns only; they exist to test whether a ranking is season-dependent.
* GDD uses a 10 C base and a 30 C cap. The uncapped value is kept so the cap's effect is
  measurable rather than assumed.
* Weather is sampled on a small number of grid points, so field-level summaries are
  pseudo-replicated. `summarize_cell_year` is the honest unit for grower comparisons.
"""

from __future__ import annotations

import calendar

import numpy as np
import pandas as pd

from common import Check, check, natural_rank

GDD_BASE_C = 10.0
GDD_CAP_C = 30.0
HEAT_THRESHOLD_C = 35.0
RAIN_DAY_MM = 1.0
HEAVY_RAIN_MM = 25.0
SENTINEL_LIMIT = -900.0

PRIMARY_SEASON_START = (5, 1)
PRIMARY_SEASON_END = (9, 30)
SENSITIVITY_SEASON_START = (4, 1)
SENSITIVITY_SEASON_END = (10, 31)


# ---------------------------------------------------------------------------
# Primitive metrics
# ---------------------------------------------------------------------------


def parse_mmdd(text: str) -> tuple[int, int]:
    """Parse '05-01' or '5-1' into (5, 1); month/day must be valid."""
    parts = str(text).strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Season boundary must look like MM-DD, got {text!r}")
    month, day = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError(f"Season boundary out of range: {text!r}")
    return month, day


def season_mask(
    dates: pd.Series,
    start: tuple[int, int] = PRIMARY_SEASON_START,
    end: tuple[int, int] = PRIMARY_SEASON_END,
) -> pd.Series:
    """Boolean mask for an inclusive month/day window (no wrap-around)."""
    if start > end:
        raise ValueError(f"Season start {start} must not be after end {end}")
    months = dates.dt.month.to_numpy()
    days = dates.dt.day.to_numpy()
    mask = ((months > start[0]) | ((months == start[0]) & (days >= start[1]))) & (
        (months < end[0]) | ((months == end[0]) & (days <= end[1]))
    )
    return pd.Series(mask, index=dates.index)


def expected_season_days(
    year: int,
    start: tuple[int, int] = PRIMARY_SEASON_START,
    end: tuple[int, int] = PRIMARY_SEASON_END,
) -> int:
    dates = pd.date_range(
        start=f"{year:04d}-{start[0]:02d}-{start[1]:02d}",
        end=f"{year:04d}-{end[0]:02d}-{end[1]:02d}",
        freq="D",
    )
    return int(len(dates))


def expected_year_days(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def daily_gdd(
    tmax: pd.Series,
    tmin: pd.Series,
    base: float = GDD_BASE_C,
    cap: float | None = GDD_CAP_C,
) -> pd.Series:
    """Daily growing degree days from daily extremes.

    Uses the mean of Tmax and Tmin, applies the cap to that mean, then the base.
    When `cap` is None the result is the uncapped sensitivity variant.
    """
    if cap is not None and cap < base:
        raise ValueError(f"GDD cap ({cap}) must be >= base ({base})")
    tmean = (tmax.astype(float) + tmin.astype(float)) / 2.0
    upper = float("inf") if cap is None else cap
    adjusted = tmean.clip(lower=base, upper=upper)
    return (adjusted - base).clip(lower=0.0)


def percent_of_field_mean(values: pd.Series, keys: pd.Series) -> pd.Series:
    """Percent of each key's own mean; safe when a key mean is zero."""
    means = values.groupby(keys).transform("mean")
    return (values / means.replace(0, np.nan)) * 100.0


# ---------------------------------------------------------------------------
# Field and cell identifiers
# ---------------------------------------------------------------------------


def field_coordinates(daily: pd.DataFrame) -> pd.DataFrame:
    coords = daily.groupby("field_id", as_index=False).agg(
        grower=("grower", "first"),
        state=("state", "first"),
        farm=("farm", "first"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        lat_nunique=("lat", "nunique"),
        lon_nunique=("lon", "nunique"),
    )
    coords = coords.assign(_order=natural_rank(coords["field_id"]))
    coords = coords.sort_values("_order").drop(columns="_order").reset_index(drop=True)
    # NASA POWER stores a per-field sample point but returns values on its native
    # 0.5 deg (lat) x 0.625 deg (lon) grid, so the grid centre is the shared cell id.
    # Snapping reproduces the observed identical-series groups exactly.
    cell_lat = (coords["lat"] / 0.5).round() * 0.5
    cell_lon = (coords["lon"] / 0.625).round() * 0.625
    coords["weather_cell"] = [
        f"{lat:.3f}_{lon:.3f}" for lat, lon in zip(cell_lat, cell_lon)
    ]
    return coords


def add_weather_cell(daily: pd.DataFrame, coords: pd.DataFrame) -> pd.DataFrame:
    frame = daily.merge(
        coords[["field_id", "weather_cell"]], on="field_id", how="left", validate="m:1"
    )
    if frame["weather_cell"].isna().any():
        missing = sorted(frame.loc[frame["weather_cell"].isna(), "field_id"].unique())
        raise ValueError(f"Fields missing weather-cell assignment: {missing}")
    return frame


def _attach_gdd(daily: pd.DataFrame, base: float, cap: float) -> pd.DataFrame:
    frame = daily.copy()
    frame["gdd"] = daily_gdd(frame["T2M_MAX"], frame["T2M_MIN"], base=base, cap=cap)
    frame["gdd_uncapped"] = daily_gdd(frame["T2M_MAX"], frame["T2M_MIN"], base=base, cap=None)
    frame["heat_day"] = (frame["T2M_MAX"] >= HEAT_THRESHOLD_C).astype(int)
    frame["rain_day"] = (frame["PRECTOTCORR"] >= RAIN_DAY_MM).astype(int)
    frame["heavy_rain_day"] = (frame["PRECTOTCORR"] >= HEAVY_RAIN_MM).astype(int)
    frame["year"] = frame["date"].dt.year
    return frame


# ---------------------------------------------------------------------------
# QC
# ---------------------------------------------------------------------------


def qc_weather(
    daily: pd.DataFrame,
    expected_fields: set[str],
    expected_years: list[int],
    start: tuple[int, int] = PRIMARY_SEASON_START,
    end: tuple[int, int] = PRIMARY_SEASON_END,
) -> tuple[list[Check], pd.DataFrame]:
    """Validate completeness, physical ranges, and grid-cell sharing.

    Returns dataset-level checks plus one QC row per field-year. The caller decides
    whether to raise; `raise_on_fail` is the strict default.
    """
    checks: list[Check] = []
    daily = daily.copy()
    daily["year"] = daily["date"].dt.year
    observed_fields = set(daily["field_id"].unique())
    checks.append(
        check(
            "weather fields match boundaries",
            len(expected_fields),
            len(observed_fields),
            observed_fields == expected_fields,
        )
    )

    duplicates = int(daily.duplicated(["field_id", "date"]).sum())
    checks.append(check("one row per field-date", 0, duplicates, duplicates == 0))

    nulls = int(daily[["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]].isna().sum().sum())
    checks.append(check("no nulls in core variables", 0, nulls, nulls == 0))

    numeric = daily[
        [
            "T2M",
            "T2M_MAX",
            "T2M_MIN",
            "PRECTOTCORR",
            "ALLSKY_SFC_SW_DWN",
            "RH2M",
            "WS10M",
        ]
    ]
    sentinels = int((numeric <= SENTINEL_LIMIT).sum().sum())
    checks.append(check("no sentinel values <= -900", 0, sentinels, sentinels == 0))

    t2m_violations = int(
        (
            (daily["T2M"] < daily["T2M_MIN"] - 0.01)
            | (daily["T2M"] > daily["T2M_MAX"] + 0.01)
        ).sum()
    )
    checks.append(
        check("T2M within [T2M_MIN, T2M_MAX]", 0, t2m_violations, t2m_violations == 0)
    )

    nonnegative = daily[["PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "WS10M"]]
    negatives = int((nonnegative < 0).sum().sum())
    checks.append(check("precip/solar/wind non-negative", 0, negatives, negatives == 0))

    rh_bad = int(((daily["RH2M"] < 0) | (daily["RH2M"] > 100)).sum())
    checks.append(check("RH2M within 0-100", 0, rh_bad, rh_bad == 0))

    coords = field_coordinates(daily)
    constant_coords = int(
        ((coords["lat_nunique"] == 1) & (coords["lon_nunique"] == 1)).sum()
    )
    checks.append(
        check(
            "one sample coordinate per field",
            len(coords),
            constant_coords,
            constant_coords == len(coords),
        )
    )
    cells_per_field = coords["weather_cell"].nunique()
    checks.append(
        check(
            "one weather cell per field",
            len(coords),
            int(coords.groupby("field_id")["weather_cell"].nunique().max()),
            int(coords.groupby("field_id")["weather_cell"].nunique().max()) == 1,
        )
    )

    # Fields that share a cell must share an identical daily series.
    cell_series = (
        daily.groupby(["weather_cell", "date"])["T2M"].nunique().reset_index(name="n")
    )
    max_distinct = int(cell_series["n"].max())
    checks.append(
        check(
            "one daily series per cell",
            1,
            max_distinct,
            max_distinct == 1,
            detail=f"{len(coords)} fields map to {cells_per_field} cells",
        )
    )
    series_groups = daily.groupby(["field_id", "date"])["T2M"].first().unstack("date")
    distinct_series = int(series_groups.drop_duplicates().shape[0])
    checks.append(
        check(
            "cell count equals distinct daily series",
            cells_per_field,
            distinct_series,
            distinct_series == cells_per_field,
        )
    )

    # Date continuity per field across the full requested window.
    window_start = pd.Timestamp(f"{min(expected_years):04d}-01-01")
    window_end = pd.Timestamp(f"{max(expected_years):04d}-12-31")
    expected_total = len(pd.date_range(window_start, window_end, freq="D"))
    continuity = daily.groupby("field_id")["date"].agg(["min", "max", "nunique"])
    continuity_ok = bool(
        (
            (continuity["min"] == window_start)
            & (continuity["max"] == window_end)
            & (continuity["nunique"] == expected_total)
        ).all()
    )
    checks.append(
        check(
            "continuous daily coverage per field",
            expected_total,
            f"{int(continuity['nunique'].min())}-{int(continuity['nunique'].max())} days",
            continuity_ok,
        )
    )

    observed_dates = set(daily["date"])
    leap_years = [year for year in expected_years if calendar.isleap(year)]
    leap_missing = [
        year for year in leap_years if pd.Timestamp(f"{year:04d}-02-29") not in observed_dates
    ]
    checks.append(
        check(
            "leap day present when in window",
            f"{len(leap_years)} leap day(s)",
            f"missing {leap_missing}" if leap_missing else "all present",
            not leap_missing,
        )
    )

    # Per field-year coverage, including the primary season.
    years_present = sorted(daily["year"].unique().tolist())
    checks.append(
        check(
            "weather years complete",
            expected_years,
            years_present,
            years_present == sorted(expected_years),
        )
    )

    season = daily.loc[season_mask(daily["date"], start, end)]
    season_counts = season.groupby(["field_id", "year"]).size().rename("season_days")
    expected_by_year = {year: expected_season_days(year, start, end) for year in expected_years}
    season_ok = all(
        int(season_counts.get((field, year), 0)) == expected_by_year[year]
        for field in coords["field_id"]
        for year in expected_years
    )
    checks.append(
        check(
            "primary-season days complete",
            "all field-years",
            f"{int(season_counts.min())}-{int(season_counts.max())} days",
            season_ok,
        )
    )

    qc_rows = []
    for row in coords.itertuples():
        field_daily = daily.loc[daily["field_id"] == row.field_id]
        for year in expected_years:
            field_year = field_daily.loc[field_daily["year"] == year]
            season_year = field_year.loc[season_mask(field_year["date"], start, end)]
            year_nulls = int(
                field_year[["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]].isna().sum().sum()
            )
            year_sentinels = int(
                (
                    field_year[
                        ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"]
                    ]
                    <= SENTINEL_LIMIT
                )
                .sum()
                .sum()
            )
            year_t2m_bad = int(
                (
                    (field_year["T2M"] < field_year["T2M_MIN"] - 0.01)
                    | (field_year["T2M"] > field_year["T2M_MAX"] + 0.01)
                ).sum()
            )
            year_days = int(field_year["date"].nunique())
            season_days = int(season_year["date"].nunique())
            year_ok = (
                year_nulls == 0
                and year_sentinels == 0
                and year_t2m_bad == 0
                and year_days == expected_year_days(year)
                and season_days == expected_by_year[year]
            )
            qc_rows.append(
                {
                    "grower": row.grower,
                    "field_id": row.field_id,
                    "year": year,
                    "weather_cell": row.weather_cell,
                    "days_in_year": year_days,
                    "expected_days_in_year": expected_year_days(year),
                    "season_days": season_days,
                    "expected_season_days": expected_by_year[year],
                    "null_count": year_nulls,
                    "sentinel_count": year_sentinels,
                    "t2m_violations": year_t2m_bad,
                    "status": "pass" if year_ok else "fail",
                }
            )

    qc_frame = pd.DataFrame(qc_rows)
    qc_frame = qc_frame.assign(_order=natural_rank(qc_frame["field_id"]))
    qc_frame = (
        qc_frame.sort_values(["grower", "_order", "year"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )
    checks.append(
        check(
            "field-year QC rows pass",
            len(qc_frame),
            int((qc_frame["status"] == "pass").sum()),
            bool((qc_frame["status"] == "pass").all()),
        )
    )
    return checks, qc_frame.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Season summaries
# ---------------------------------------------------------------------------


def _season_aggregates(daily: pd.DataFrame, start: tuple[int, int], end: tuple[int, int]) -> pd.DataFrame:
    selected = daily.loc[season_mask(daily["date"], start, end)]
    grouped = selected.groupby(["field_id", "year"], sort=False)
    return grouped.agg(
        days=("date", "nunique"),
        precip_mm=("PRECTOTCORR", "sum"),
        gdd_c=("gdd", "sum"),
        heat_days_35c=("heat_day", "sum"),
        rain_days_1mm=("rain_day", "sum"),
        heavy_rain_days_25mm=("heavy_rain_day", "sum"),
        max_daily_precip_mm=("PRECTOTCORR", "max"),
        mean_t2m_c=("T2M", "mean"),
        mean_tmax_c=("T2M_MAX", "mean"),
        mean_tmin_c=("T2M_MIN", "mean"),
        mean_rh_pct=("RH2M", "mean"),
        mean_solar_mj_m2=("ALLSKY_SFC_SW_DWN", "mean"),
        mean_wind_ms=("WS10M", "mean"),
        gdd_uncapped_c=("gdd_uncapped", "sum"),
    ).reset_index()


def summarize_field_year(
    daily: pd.DataFrame,
    coords: pd.DataFrame,
    start: tuple[int, int] = PRIMARY_SEASON_START,
    end: tuple[int, int] = PRIMARY_SEASON_END,
    base: float = GDD_BASE_C,
    cap: float = GDD_CAP_C,
) -> pd.DataFrame:
    """One row per field-year with primary season, sensitivity, and anomaly columns."""
    prepared = _attach_gdd(daily, base=base, cap=cap)
    primary = _season_aggregates(prepared, start, end)
    primary["expected_season_days"] = [
        expected_season_days(int(year), start, end) for year in primary["year"]
    ]

    sensitivity = _season_aggregates(
        prepared, SENSITIVITY_SEASON_START, SENSITIVITY_SEASON_END
    )[["field_id", "year", "precip_mm", "gdd_c"]].rename(
        columns={
            "precip_mm": "precip_apr_oct_mm",
            "gdd_c": "gdd_apr_oct_c",
        }
    )

    annual = (
        prepared.groupby(["field_id", "year"], sort=False)["PRECTOTCORR"]
        .sum()
        .reset_index(name="precip_year_mm")
    )

    frame = (
        primary.merge(sensitivity, on=["field_id", "year"], validate="1:1")
        .merge(annual, on=["field_id", "year"], validate="1:1")
        .merge(
            coords[["field_id", "grower", "state", "farm", "weather_cell", "lat", "lon"]],
            on="field_id",
            how="left",
            validate="m:1",
        )
    )

    frame["precip_pct_of_field_mean"] = percent_of_field_mean(
        frame["precip_mm"], frame["field_id"]
    )
    field_gdd_mean = frame.groupby("field_id")["gdd_c"].transform("mean")
    frame["gdd_anomaly_c"] = frame["gdd_c"] - field_gdd_mean

    ordered = [
        "grower",
        "state",
        "farm",
        "field_id",
        "year",
        "weather_cell",
        "lat",
        "lon",
        "days",
        "expected_season_days",
        "precip_mm",
        "gdd_c",
        "heat_days_35c",
        "rain_days_1mm",
        "heavy_rain_days_25mm",
        "max_daily_precip_mm",
        "mean_t2m_c",
        "mean_tmax_c",
        "mean_tmin_c",
        "mean_rh_pct",
        "mean_solar_mj_m2",
        "mean_wind_ms",
        "precip_apr_oct_mm",
        "gdd_apr_oct_c",
        "precip_year_mm",
        "gdd_uncapped_c",
        "precip_pct_of_field_mean",
        "gdd_anomaly_c",
    ]
    frame = frame[ordered].rename(columns={"days": "season_days"})
    frame["year"] = frame["year"].astype(int)
    frame = frame.assign(_order=natural_rank(frame["field_id"]))
    return (
        frame.sort_values(["state", "_order", "year"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )


def summarize_cell_year(field_year: pd.DataFrame) -> pd.DataFrame:
    """Collapse field-years onto the honest weather unit: grower x cell x year."""
    grouped = field_year.groupby(
        ["grower", "state", "weather_cell", "year"], sort=False
    )
    cell_year = grouped.agg(
        field_count=("field_id", "nunique"),
        season_days=("season_days", "max"),
        precip_mm=("precip_mm", "first"),
        gdd_c=("gdd_c", "first"),
        heat_days_35c=("heat_days_35c", "first"),
        rain_days_1mm=("rain_days_1mm", "first"),
        heavy_rain_days_25mm=("heavy_rain_days_25mm", "first"),
        max_daily_precip_mm=("max_daily_precip_mm", "first"),
        mean_t2m_c=("mean_t2m_c", "first"),
        mean_tmax_c=("mean_tmax_c", "first"),
        precip_apr_oct_mm=("precip_apr_oct_mm", "first"),
        gdd_apr_oct_c=("gdd_apr_oct_c", "first"),
        precip_year_mm=("precip_year_mm", "first"),
        gdd_uncapped_c=("gdd_uncapped_c", "first"),
    ).reset_index()
    return cell_year.sort_values(
        ["state", "weather_cell", "year"]
    ).reset_index(drop=True)


def grower_year_means(cell_year: pd.DataFrame) -> pd.DataFrame:
    """Cell-weighted grower-year means; one row per grower-year plus field total."""
    grouped = cell_year.groupby(["grower", "state", "year"], sort=False)
    means = grouped.agg(
        cells=("weather_cell", "nunique"),
        fields=("field_count", "sum"),
        precip_mm=("precip_mm", "mean"),
        gdd_c=("gdd_c", "mean"),
        heat_days_35c=("heat_days_35c", "mean"),
        precip_apr_oct_mm=("precip_apr_oct_mm", "mean"),
        gdd_apr_oct_c=("gdd_apr_oct_c", "mean"),
    ).reset_index()
    return means.sort_values(["state", "year"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------


def sensitivity_table(cell_year: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    """Compare primary definitions against alternate season/GDD definitions.

    Ranks are across growers within one definition. A rank order that changes between
    definitions means the headline ordering is not robust to that choice.
    """

    def weighted(column: str) -> pd.Series:
        return cell_year.groupby("grower")[column].mean()

    specs = [
        ("precip", "May-Sep (primary)", "precip_mm"),
        ("precip", "Apr-Oct", "precip_apr_oct_mm"),
        ("precip", "Calendar year", "precip_year_mm"),
        ("gdd", "May-Sep capped (primary)", "gdd_c"),
        ("gdd", "Apr-Oct capped", "gdd_apr_oct_c"),
        ("gdd", "May-Sep uncapped", "gdd_uncapped_c"),
    ]
    state_lookup = cell_year.groupby("grower")["state"].first()
    rows = []
    orderings: dict[str, list[list[str]]] = {"precip": [], "gdd": []}
    for metric, definition, column in specs:
        values = weighted(column)
        ranks = values.rank(method="min").astype(int)
        low_to_high = values.sort_values().index.tolist()
        orderings[metric].append(low_to_high)
        for grower, value in values.items():
            rows.append(
                {
                    "metric": metric,
                    "definition": definition,
                    "grower": grower,
                    "state": state_lookup.get(grower, ""),
                    "value": float(value),
                    "rank_low_to_high": int(ranks.loc[grower]),
                }
            )
    frame = pd.DataFrame(rows)
    stability = {}
    for metric, orders in orderings.items():
        unique_orders = {tuple(order) for order in orders}
        stability[metric] = {
            "orderings": [list(order) for order in orders],
            "stable": len(unique_orders) == 1,
        }
    return frame, stability
