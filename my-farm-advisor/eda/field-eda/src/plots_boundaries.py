"""Static boundary figures B1-B3.

B1  where the fields are and how large they are (map + acreage strip)
B2  are fields geometrically interchangeable? size vs compactness by grower
B3  does CDL raster area agree with polygon area? (1:1 plus deviation)
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.affinity import scale, translate

from common import EQUAL_AREA_CRS, add_footer, apply_style, save_figure, state_meta


def _order_states(states) -> list[str]:
    return sorted(set(states), key=lambda state: state_meta(state)["order"])


def _local_km(geometry, center_x: float, center_y: float):
    moved = translate(geometry, xoff=-center_x, yoff=-center_y)
    return scale(moved, xfact=1 / 1000.0, yfact=1 / 1000.0, origin=(0, 0))


def _scale_bar(ax, xlim, ylim, color="#333333") -> None:
    span = xlim[1] - xlim[0]
    candidates = [1, 2, 5, 10, 20, 50]
    length = min(candidates, key=lambda value: abs(value - 0.18 * span))
    if length > 0.45 * span:
        length = candidates[max(0, candidates.index(length) - 1)]
    x0 = xlim[0] + 0.05 * span
    y0 = ylim[0] + 0.07 * (ylim[1] - ylim[0])
    ax.plot([x0, x0 + length], [y0, y0], color=color, linewidth=2.6, solid_capstyle="butt")
    ax.text(
        x0 + length / 2,
        y0 + 0.025 * (ylim[1] - ylim[0]),
        f"{length:g} km",
        ha="center",
        va="bottom",
        fontsize=7.5,
        color=color,
    )


def _north_arrow(ax) -> None:
    ax.annotate(
        "",
        xy=(0.95, 0.97),
        xytext=(0.95, 0.86),
        xycoords="axes fraction",
        arrowprops={"arrowstyle": "-|>", "color": "#333333", "linewidth": 1.2},
    )
    ax.text(
        0.95,
        0.985,
        "N",
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=8,
        color="#333333",
    )


def plot_field_map_and_acreage(
    field_summary: pd.DataFrame,
    boundaries: gpd.GeoDataFrame,
    figures_dir: Path,
) -> Path:
    apply_style()
    projected = boundaries.to_crs(EQUAL_AREA_CRS)
    states = _order_states(field_summary["state"])

    fig = plt.figure(figsize=(13.4, 7.7))
    grid = fig.add_gridspec(2, 3, height_ratios=[3.0, 1.45], hspace=0.42, wspace=0.16)

    prepared = []
    for state in states:
        subset = projected.loc[projected["state"] == state].copy()
        union = subset.geometry.union_all()
        center_x, center_y = union.centroid.x, union.centroid.y
        subset["geometry"] = subset.geometry.apply(
            lambda geom: _local_km(geom, center_x, center_y)
        )
        prepared.append((state, subset, center_x, center_y, subset.total_bounds))

    # One shared span keeps field footprints visually comparable across panels.
    span = max(
        max(bounds[2] - bounds[0], bounds[3] - bounds[1])
        for _, _, _, _, bounds in prepared
    ) * 1.18

    for index, (state, subset, center_x, center_y, _) in enumerate(prepared):
        ax = fig.add_subplot(grid[0, index])
        meta = state_meta(state)
        subset.plot(
            ax=ax,
            color=str(meta["color"]),
            edgecolor="#2f2f2f",
            alpha=0.55,
            linewidth=1.0,
        )
        ax.set_xlim(-span / 2, span / 2)
        ax.set_ylim(-span / 2, span / 2)
        ax.set_aspect("equal")
        ax.set_xlabel("km east (local)")
        ax.set_ylabel("km north (local)" if index == 0 else "")
        ax.set_frame_on(False)
        ax.spines["left"].set_visible(True)
        ax.spines["bottom"].set_visible(True)
        ax.tick_params(labelsize=7)
        _scale_bar(ax, ax.get_xlim(), ax.get_ylim())
        _north_arrow(ax)

        grower_rows = field_summary.loc[field_summary["state"] == state]
        ax.set_title(
            f"{meta['label']} - {str(grower_rows['county'].iloc[0])} County\n"
            f"{len(grower_rows)} fields, {grower_rows['acres'].sum():,.0f} ac",
            loc="left",
            fontsize=9.5,
            pad=8,
        )

    # Acreage strip: one point per field, median marked.
    ax_acres = fig.add_subplot(grid[1, :])
    positions = {state: index for index, state in enumerate(states)}
    for state in states:
        subset = field_summary.loc[field_summary["state"] == state].sort_values("acres")
        meta = state_meta(state)
        base = positions[state]
        offsets = np.linspace(-0.17, 0.17, len(subset)) if len(subset) > 1 else [0.0]
        ax_acres.scatter(
            subset["acres"],
            base + offsets,
            s=34,
            color=str(meta["color"]),
            edgecolor="white",
            linewidth=0.7,
            zorder=3,
        )
        median = float(subset["acres"].median())
        ax_acres.plot(
            [median, median],
            [base - 0.28, base + 0.28],
            color="#222222",
            linewidth=2.0,
            zorder=4,
        )
    ax_acres.set_yticks(
        [positions[state] for state in states],
        labels=[
            f"{state_meta(state)['label']}\nmedian "
            f"{field_summary.loc[field_summary['state'] == state, 'acres'].median():,.0f} ac"
            for state in states
        ],
    )
    ax_acres.invert_yaxis()
    ax_acres.set_ylim(len(states) - 0.45, -0.55)
    ax_acres.set_xlabel("Field size (acres)")
    ax_acres.set_title(
        "Field size by grower (one point per field; vertical bar = median)",
        loc="left",
        fontsize=9.5,
    )
    ax_acres.grid(True, axis="x", color="#e6e6e6", linewidth=0.7, zorder=0)
    ax_acres.margins(x=0.10)

    fig.suptitle("B1  Each farm is 10 scattered county blocks, not one contiguous parcel", y=1.005)
    add_footer(
        fig,
        "Polygons drawn in an equal-area projection; all three panels share one scale. "
        "At county scale each field is a small block, so shapes are compared in B2 and B3, not here.",
    )
    return save_figure(fig, figures_dir / "b1_field_map_and_acreage.png")


def plot_area_vs_compactness(field_summary: pd.DataFrame, figures_dir: Path) -> Path:
    apply_style()
    states = _order_states(field_summary["state"])
    fig, axes = plt.subplots(1, len(states), figsize=(12.2, 4.3), sharex=True, sharey=True)
    if len(states) == 1:
        axes = [axes]

    x_min = field_summary["acres"].min() * 0.92
    x_max = field_summary["acres"].max() * 1.06
    y_min = max(0.0, field_summary["compactness"].min() - 0.06)
    y_max = min(1.0, field_summary["compactness"].max() + 0.06)

    for ax, state in zip(axes, states):
        subset = field_summary.loc[field_summary["state"] == state]
        meta = state_meta(state)
        ax.scatter(
            subset["acres"],
            subset["compactness"],
            s=46,
            color=str(meta["color"]),
            edgecolor="white",
            linewidth=0.8,
            zorder=3,
        )
        rho = subset["acres"].corr(subset["compactness"], method="spearman")
        ax.text(
            0.04,
            0.06,
            f"Spearman rho = {rho:.2f}\nn = {len(subset)} (descriptive)",
            transform=ax.transAxes,
            fontsize=8,
            color="#333333",
            va="bottom",
        )
        largest = subset.loc[subset["acres"].idxmax()]
        ax.annotate(
            str(largest["field_id"]),
            xy=(largest["acres"], largest["compactness"]),
            xytext=(4, 6),
            textcoords="offset points",
            fontsize=7,
            color="#444444",
        )
        ax.set_title(str(meta["label"]), loc="left", fontsize=10)
        ax.grid(True, color="#e6e6e6", linewidth=0.7, zorder=0)
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)

    axes[0].set_ylabel("Shape compactness (4 pi A / P^2; 1 = circle)")
    for ax in axes:
        ax.set_xlabel("Field size (acres)")

    fig.suptitle("B2  Fields are irregular everywhere; only Iowa shows a strong size-shape link", y=1.03)
    fig.text(
        0.5,
        0.965,
        "Compactness falls as a field becomes longer or more lobed. It is a shape summary, not a quality score.",
        ha="center",
        fontsize=8.5,
        color="#444444",
    )
    add_footer(
        fig,
        "n = 10 fields per grower. Spearman rho is descriptive; fields are not a random sample.",
    )
    return save_figure(fig, figures_dir / "b2_area_vs_compactness.png")


def plot_polygon_vs_cdl_area(field_summary: pd.DataFrame, figures_dir: Path) -> Path:
    apply_style()
    states = _order_states(field_summary["state"])
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(11.6, 4.9))

    low = min(field_summary["acres"].min(), field_summary["cdl_pixel_area_acres"].min()) * 0.92
    high = max(field_summary["acres"].max(), field_summary["cdl_pixel_area_acres"].max()) * 1.05
    ax_a.plot([low, high], [low, high], color="#666666", linestyle="--", linewidth=1.0, zorder=1)
    ax_a.text(high * 0.97, high * 0.93, "1:1", ha="right", va="top", fontsize=8, color="#555555")

    for state in states:
        subset = field_summary.loc[field_summary["state"] == state]
        meta = state_meta(state)
        ax_a.scatter(
            subset["acres"],
            subset["cdl_pixel_area_acres"],
            s=42,
            color=str(meta["color"]),
            edgecolor="white",
            linewidth=0.7,
            label=str(meta["label"]),
            zorder=3,
        )
        ax_b.scatter(
            subset["acres"],
            subset["area_dev_pct"],
            s=42,
            color=str(meta["color"]),
            edgecolor="white",
            linewidth=0.7,
            zorder=3,
        )
    ax_a.set_xlim(low, high)
    ax_a.set_ylim(low, high)
    ax_a.set_aspect("equal")
    ax_a.set_xlabel("Polygon area (acres)")
    ax_a.set_ylabel("CDL pixel area (acres)")
    ax_a.set_title("a. Raster area against polygon area", loc="left", fontsize=10)
    ax_a.grid(True, color="#e6e6e6", linewidth=0.7, zorder=0)
    ax_a.legend(loc="upper left", fontsize=8)

    ax_b.axhline(0.0, color="#555555", linewidth=1.0)
    for band in (-0.5, 0.5):
        ax_b.axhline(band, color="#999999", linewidth=0.8, linestyle=":")
    ax_b.text(
        0.98,
        0.97,
        "+/- 0.5% band",
        transform=ax_b.transAxes,
        ha="right",
        va="top",
        fontsize=7.8,
        color="#666666",
    )
    ax_b.set_xlabel("Polygon area (acres)")
    ax_b.set_ylabel("CDL pixel area - polygon area (%)")
    ax_b.set_title("b. Deviation from polygon area", loc="left", fontsize=10)
    ax_b.grid(True, color="#e6e6e6", linewidth=0.7, zorder=0)
    ax_b.margins(x=0.06)

    median_dev = float(field_summary["area_dev_pct"].median())
    low_dev = float(field_summary["area_dev_pct"].min())
    high_dev = float(field_summary["area_dev_pct"].max())
    ax_b.text(
        0.98,
        0.88,
        f"median {median_dev:+.2f}%\nrange {low_dev:+.2f}% to {high_dev:+.2f}%",
        transform=ax_b.transAxes,
        ha="right",
        va="top",
        fontsize=8.2,
        color="#333333",
    )

    fig.suptitle("B3  CDL raster area agrees with polygon area within about 1%", y=1.03)
    fig.text(
        0.5,
        0.965,
        "If the raster under- or over-counted field edges, every CDL share would inherit that bias.",
        ha="center",
        fontsize=8.5,
        color="#444444",
    )
    add_footer(
        fig,
        f"CDL {int(field_summary['cdl_year'].iloc[0])} 30 m pixels vs equal-area polygon. "
        "Agreement validates the raster denominator, not the crop labels.",
    )
    return save_figure(fig, figures_dir / "b3_polygon_vs_cdl_area.png")


def build_all(
    field_summary: pd.DataFrame,
    boundaries: gpd.GeoDataFrame,
    figures_dir: Path,
) -> list[Path]:
    apply_style()
    return [
        plot_field_map_and_acreage(field_summary, boundaries, figures_dir),
        plot_area_vs_compactness(field_summary, figures_dir),
        plot_polygon_vs_cdl_area(field_summary, figures_dir),
    ]
