"""Static weather figures W1-W3.

Each figure answers one question:
  W1  within-field: how wet or dry was each field-year relative to that field?
  W2  across growers: where do the three growers sit in heat/water space?
  W3  by crop: does corn vs soybean separate temperature/water exposure, or does
      location do it?
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import TwoSlopeNorm

from common import (
    CROP_GROUP_COLORS,
    add_footer,
    apply_style,
    field_sort_key,
    save_figure,
    state_meta,
    state_order,
)

WET_DRY_CMAP = "BrBG"
CROP_MARKERS = {"Corn": "^", "Soybeans": "o"}


def _ordered_fields(field_year: pd.DataFrame) -> list[str]:
    unique = field_year[["state", "field_id"]].drop_duplicates()
    rows = list(unique.itertuples(index=False))
    rows.sort(key=lambda row: (str(row.state), field_sort_key(row.field_id)))
    return [row.field_id for row in rows]


def _grower_blocks(field_year: pd.DataFrame, ordered: list[str]) -> list[tuple[str, int, int]]:
    state_by_field = (
        field_year[["field_id", "state"]].drop_duplicates().set_index("field_id")["state"]
    )
    blocks: list[tuple[str, int, int]] = []
    start = 0
    for index in range(1, len(ordered) + 1):
        if index == len(ordered) or state_by_field[ordered[index]] != state_by_field[ordered[start]]:
            blocks.append((str(state_by_field[ordered[start]]), start, index))
            start = index
    return blocks


def plot_within_field_precip(
    field_year: pd.DataFrame, figures_dir: Path
) -> Path:
    apply_style()
    ordered = _ordered_fields(field_year)
    matrix_frame = (
        field_year.pivot(index="field_id", columns="year", values="precip_pct_of_field_mean")
        .reindex(ordered)
        .sort_index(axis=1)
    )
    years = [int(year) for year in matrix_frame.columns]
    matrix = matrix_frame.to_numpy(dtype=float)

    vmin = float(min(60.0, np.nanmin(matrix)))
    vmax = float(max(140.0, np.nanmax(matrix)))
    norm = TwoSlopeNorm(vcenter=100.0, vmin=vmin, vmax=vmax)

    fig, ax = plt.subplots(figsize=(7.8, 8.4))
    image = ax.imshow(
        matrix,
        aspect="auto",
        cmap=WET_DRY_CMAP,
        norm=norm,
        interpolation="nearest",
    )

    ax.set_xticks(range(len(years)), labels=[str(year) for year in years])
    ax.set_yticks(range(len(ordered)), labels=ordered)
    ax.tick_params(axis="y", labelsize=7.2, length=0)
    ax.tick_params(axis="x", labelsize=9)
    ax.set_xlabel("Growing season (May 1 - Sep 30)")
    ax.set_ylabel("Field (grouped by grower)")

    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            value = matrix[row, column]
            if np.isnan(value):
                continue
            text_color = "white" if abs(value - 100.0) > 20 else "#222222"
            ax.text(
                column,
                row,
                f"{value:.0f}",
                ha="center",
                va="center",
                fontsize=6.8,
                color=text_color,
            )

    for state, start, stop in _grower_blocks(field_year, ordered):
        if start > 0:
            ax.axhline(start - 0.5, color="white", linewidth=2.2)
        center = (start + stop - 1) / 2.0
        ax.text(
            1.015,
            1.0 - (center + 0.5) / len(ordered),
            str(state_meta(state)["label"]),
            transform=ax.transAxes,
            ha="left",
            va="center",
            fontsize=9,
            fontweight="bold",
            color=str(state_meta(state)["color"]),
        )

    ax.set_title("W1  Within-field growing-season precipitation, 2021-2025", pad=28)
    ax.text(
        0.0,
        1.012,
        "Each cell is that field-year's May-Sep total as a percent of the field's own five-year mean.",
        transform=ax.transAxes,
        fontsize=8.5,
        color="#444444",
    )

    colorbar = fig.colorbar(image, ax=ax, orientation="horizontal", fraction=0.04, pad=0.07, aspect=45)
    colorbar.set_label("% of the field's 2021-2025 mean (100 = field average)")
    colorbar.outline.set_visible(False)

    add_footer(
        fig,
        "Fields in the same row block and year share a NASA POWER grid cell, so identical values are "
        "expected. Brown = drier than that field's mean; blue-green = wetter.",
    )
    return save_figure(fig, figures_dir / "w1_within_field_precip_anomaly.png")


def plot_climate_space(
    cell_year: pd.DataFrame, grower_year: pd.DataFrame, figures_dir: Path
) -> Path:
    apply_style()
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(11.4, 4.9))

    for state in sorted(cell_year["state"].unique(), key=lambda s: state_meta(s)["order"]):
        subset = cell_year.loc[cell_year["state"] == state]
        meta = state_meta(state)
        ax_a.scatter(
            subset["precip_mm"],
            subset["gdd_c"],
            s=18 + 22 * subset["field_count"],
            color=str(meta["color"]),
            alpha=0.78,
            edgecolor="white",
            linewidth=0.7,
            label=str(meta["label"]),
            zorder=3,
        )
    ax_a.set_xlabel("May-Sep precipitation (mm)")
    ax_a.set_ylabel("May-Sep GDD (C-days, base 10 / cap 30)")
    ax_a.set_title("a. Cell-years (n = %d)" % len(cell_year), loc="left", fontsize=10)
    ax_a.grid(True, color="#e6e6e6", linewidth=0.7, zorder=0)
    ax_a.legend(loc="lower left", fontsize=8)

    # Label the three most decision-relevant extremes without dressing the panel up.
    labels = []
    seen: set[tuple[str, int]] = set()
    hottest = cell_year.loc[cell_year["gdd_c"].idxmax()]
    wettest = cell_year.loc[cell_year["precip_mm"].idxmax()]
    driest = cell_year.loc[cell_year["precip_mm"].idxmin()]
    for row, offset in [
        (hottest, (8, 6)),
        (wettest, (-10, 8)),
        (driest, (8, -12)),
    ]:
        key = (str(row.state), int(row.year))
        if key in seen:
            continue
        seen.add(key)
        labels.append((row, offset))
    for row, offset in labels:
        ax_a.annotate(
            f"{row.state} {int(row.year)}",
            xy=(row.precip_mm, row.gdd_c),
            xytext=offset,
            textcoords="offset points",
            fontsize=7.5,
            color="#333333",
        )

    name_offsets = {
        "IA": (10, 8, "left"),
        "IL": (-10, 10, "right"),
        "NE": (10, 8, "left"),
    }
    for state in sorted(grower_year["state"].unique(), key=lambda s: state_meta(s)["order"]):
        subset = grower_year.loc[grower_year["state"] == state].sort_values("year")
        meta = state_meta(state)
        ax_b.plot(
            subset["precip_mm"],
            subset["gdd_c"],
            color=str(meta["color"]),
            linewidth=1.5,
            zorder=2,
        )
        # Year is encoded by marker size (small = first year, large = last year) so the
        # trajectory stays readable where the three paths cross.
        sizes = np.linspace(26.0, 86.0, len(subset))
        ax_b.scatter(
            subset["precip_mm"],
            subset["gdd_c"],
            s=sizes,
            color=str(meta["color"]),
            edgecolor="white",
            linewidth=0.8,
            zorder=3,
        )
        if len(subset) > 1:
            previous = subset.iloc[-2]
            last = subset.iloc[-1]
            ax_b.annotate(
                "",
                xy=(last["precip_mm"], last["gdd_c"]),
                xytext=(previous["precip_mm"], previous["gdd_c"]),
                arrowprops={
                    "arrowstyle": "-|>",
                    "color": str(meta["color"]),
                    "linewidth": 1.0,
                    "shrinkA": 10,
                    "shrinkB": 10,
                },
                zorder=4,
            )
        else:
            last = subset.iloc[-1]
        dx, dy, alignment = name_offsets.get(
            str(state).upper(), (10, 8, "left")
        )
        ax_b.annotate(
            str(meta["label"]),
            xy=(last["precip_mm"], last["gdd_c"]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=8.5,
            fontweight="bold",
            color=str(meta["color"]),
            ha=alignment,
            va="center",
        )
    ax_b.text(
        0.02,
        0.03,
        "Marker size increases 2021 -> 2025; arrow marks the final year.",
        transform=ax_b.transAxes,
        fontsize=7.8,
        color="#555555",
    )
    ax_b.set_xlabel("May-Sep precipitation (mm)")
    ax_b.set_ylabel("May-Sep GDD (C-days)")
    ax_b.set_title("b. Grower-year means, cell-weighted", loc="left", fontsize=10)
    ax_b.grid(True, color="#e6e6e6", linewidth=0.7, zorder=0)
    ax_b.margins(x=0.16, y=0.12)

    fig.suptitle(
        "W2  Climate space: Nebraska is hotter and drier; Iowa's recent years are cool and wet",
        y=1.02,
    )
    fig.text(
        0.5,
        0.965,
        "Marker area in a scales with fields sharing a cell, so a large marker is one weather sample, not many.",
        ha="center",
        fontsize=8.5,
        color="#444444",
    )
    add_footer(
        fig,
        "Source: NASA POWER daily weather, 2021-2025, primary season May 1 - Sep 30. "
        "Panel b uses one value per cell-year, then averages cells within a grower.",
    )
    return save_figure(fig, figures_dir / "w2_climate_space.png")


def plot_crop_weather(
    crop_weather: pd.DataFrame,
    paired: pd.DataFrame,
    cell_count: int,
    figures_dir: Path,
) -> Path:
    """Compare corn and soybean exposure without pretending crop is random.

    Panel a shows every field-year. The grower clusters separate, while corn and soybean
    overlap inside each cluster. Panels b-c use only fields that grew both crops, so
    location is held still and the question becomes whether the corn years were warmer or
    wetter than the soybean years on the same field.
    """
    apply_style()
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(13.8, 5.0),
        gridspec_kw={"width_ratios": [1.6, 1.0, 1.0]},
    )
    ax_a, ax_b, ax_c = axes

    crops = [
        crop
        for crop in CROP_MARKERS
        if (crop_weather["dominant_crop"] == crop).any()
    ]
    for crop in crops:
        subset = crop_weather.loc[crop_weather["dominant_crop"] == crop]
        ax_a.scatter(
            subset["precip_mm"],
            subset["gdd_c"],
            s=26,
            marker=CROP_MARKERS[crop],
            color=CROP_GROUP_COLORS[crop],
            alpha=0.80,
            edgecolor="white",
            linewidth=0.5,
            label=f"{crop} ({len(subset)} field-years)",
            zorder=3,
        )

    name_offsets = {"IA": (14, -14), "IL": (16, 2), "NE": (-14, 14)}
    for state in sorted(crop_weather["state"].unique(), key=state_order):
        subset = crop_weather.loc[crop_weather["state"] == state]
        meta = state_meta(state)
        ax_a.annotate(
            str(meta["label"]),
            xy=(subset["precip_mm"].mean(), subset["gdd_c"].mean()),
            xytext=name_offsets.get(str(state).upper(), (10, 10)),
            textcoords="offset points",
            ha="center",
            fontsize=9,
            fontweight="bold",
            color=str(meta["color"]),
            bbox={
                "boxstyle": "round,pad=0.18",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.78,
            },
            zorder=6,
        )

    ax_a.set_xlabel("May-Sep precipitation (mm)")
    ax_a.set_ylabel("May-Sep GDD (C-days)")
    ax_a.set_title(
        f"a. Field-years by crop (n = {len(crop_weather)})", loc="left", fontsize=10
    )
    ax_a.grid(True, color="#e6e6e6", linewidth=0.7, zorder=0)
    ax_a.legend(loc="lower left", fontsize=8, frameon=True)
    ax_a.margins(x=0.12, y=0.12)
    ax_a.text(
        0.98,
        0.03,
        f"{cell_count} weather cells supply every field",
        transform=ax_a.transAxes,
        ha="right",
        va="bottom",
        fontsize=7.8,
        color="#555555",
    )

    states = sorted(paired["state"].unique(), key=state_order)
    panel_specs = [
        (
            ax_b,
            "delta_gdd_c",
            "Corn-year minus soybean-year GDD (C-days)",
            "b. Paired GDD difference",
        ),
        (
            ax_c,
            "delta_precip_mm",
            "Corn-year minus soybean-year precipitation (mm)",
            "c. Paired water difference",
        ),
    ]
    rng = np.random.default_rng(20261125)
    for ax, column, xlabel, title in panel_specs:
        for row, state in enumerate(states):
            subset = paired.loc[paired["state"] == state]
            if subset.empty:
                continue
            jitter = rng.uniform(-0.11, 0.11, len(subset))
            ax.scatter(
                subset[column],
                row + jitter,
                s=30,
                color=str(state_meta(state)["color"]),
                alpha=0.75,
                edgecolor="white",
                linewidth=0.5,
                zorder=3,
            )
            median = float(subset[column].median())
            ax.plot(
                [median, median],
                [row - 0.22, row + 0.22],
                color="#111111",
                linewidth=2.2,
                zorder=5,
            )
            ax.annotate(
                f"{median:+.0f}",
                xy=(median, row - 0.22),
                xytext=(0, -5),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=7.6,
                color="#222222",
            )
        ax.axvline(0.0, color="#777777", linewidth=1.0, linestyle="--", zorder=1)
        ax.set_yticks(
            range(len(states)),
            labels=[
                f"{state_meta(state)['label']}\n"
                f"(n={int((paired['state'] == state).sum())})"
                for state in states
            ],
        )
        ax.tick_params(axis="y", length=0)
        ax.set_ylim(-0.60, len(states) - 0.40)
        ax.set_xlabel(xlabel)
        ax.set_title(title, loc="left", fontsize=10)
        ax.grid(True, axis="x", color="#e6e6e6", linewidth=0.7, zorder=0)
        ax.set_axisbelow(True)
        ax.margins(x=0.14)

    corn = int((crop_weather["dominant_crop"] == "Corn").sum())
    soybean = int((crop_weather["dominant_crop"] == "Soybeans").sum())
    fig.suptitle("W3  Crop does not separate the climate; location does", y=1.03)
    fig.text(
        0.5,
        0.955,
        f"Corn and soybean field-years overlap inside every grower; the {len(paired)} fields "
        "that grew both crops show paired differences straddling zero.",
        ha="center",
        fontsize=8.5,
        color="#444444",
    )
    add_footer(
        fig,
        f"Panel a: one point per field-year ({corn} corn, {soybean} soybean) from "
        f"{cell_count} weather cells. Panels b-c: mean by crop on each paired field, corn "
        "minus soybean; positive means the corn years were higher. Crop is confounded with "
        "year and rotation phase, so these are descriptive differences, not a crop effect. "
        "No yield data is used.",
        y=-0.16,
    )
    return save_figure(fig, figures_dir / "w3_crop_weather_exposure.png")


def build_all(
    field_year: pd.DataFrame,
    cell_year: pd.DataFrame,
    grower_year: pd.DataFrame,
    crop_weather: pd.DataFrame,
    paired_crop_weather: pd.DataFrame,
    figures_dir: Path,
) -> list[Path]:
    apply_style()
    return [
        plot_within_field_precip(field_year, figures_dir),
        plot_climate_space(cell_year, grower_year, figures_dir),
        plot_crop_weather(
            crop_weather,
            paired_crop_weather,
            int(cell_year["weather_cell"].nunique()),
            figures_dir,
        ),
    ]
