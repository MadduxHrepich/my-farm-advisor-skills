"""Static CDL figures C1-C3.

C1  what each field actually grew, year by year
C2  whether rotations are synchronized across a grower
C3  how much of the rotation is real alternation vs repeated corn
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from common import (
    CROP_GROUP_COLORS,
    CROP_GROUP_ORDER,
    add_footer,
    apply_style,
    field_sort_key,
    save_figure,
    state_meta,
)

GROUP_LETTER = {
    "Corn": "C",
    "Soybeans": "S",
    "Small grain": "W",
    "Pasture/Hay": "P",
    "Other crop": "O",
    "Non-crop": "N",
}
GROUP_TEXT_COLOR = {
    "Corn": "#231f00",
    "Soybeans": "#ffffff",
    "Small grain": "#231f00",
    "Pasture/Hay": "#231f00",
    "Other crop": "#ffffff",
    "Non-crop": "#333333",
}


def _ordered_fields(field_year: pd.DataFrame) -> list[str]:
    unique = field_year[["state", "field_id"]].drop_duplicates()
    rows = list(unique.itertuples(index=False))
    rows.sort(key=lambda row: (str(row.state), field_sort_key(row.field_id)))
    return [row.field_id for row in rows]


def _grower_blocks(field_year: pd.DataFrame, ordered: list[str]) -> list[tuple[str, int, int]]:
    state_by_field = (
        field_year[["field_id", "state"]].drop_duplicates().set_index("field_id")["state"]
    )
    blocks = []
    start = 0
    for index in range(1, len(ordered) + 1):
        if index == len(ordered) or state_by_field[ordered[index]] != state_by_field[ordered[start]]:
            blocks.append((str(state_by_field[ordered[start]]), start, index))
            start = index
    return blocks


def plot_crop_sequence(field_year: pd.DataFrame, figures_dir: Path) -> Path:
    apply_style()
    ordered = _ordered_fields(field_year)
    matrix_frame = (
        field_year.pivot(index="field_id", columns="year", values="dominant_crop")
        .reindex(ordered)
        .sort_index(axis=1)
    )
    years = [int(year) for year in matrix_frame.columns]
    category_index = {group: index for index, group in enumerate(CROP_GROUP_ORDER)}
    matrix = matrix_frame.apply(lambda column: column.map(category_index)).to_numpy(dtype=float)
    noncrop_mean = field_year.groupby("field_id")["noncrop_pct"].mean().reindex(ordered)

    cmap = ListedColormap([CROP_GROUP_COLORS[group] for group in CROP_GROUP_ORDER])
    norm = BoundaryNorm(np.arange(-0.5, len(CROP_GROUP_ORDER) + 0.5), cmap.N)
    label_to_group = {index: group for group, index in category_index.items()}

    fig, ax = plt.subplots(figsize=(8.6, 8.6))
    ax.imshow(matrix, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")

    ax.set_xticks(range(len(years)), labels=[str(year) for year in years])
    ax.set_yticks(range(len(ordered)), labels=ordered)
    ax.tick_params(axis="y", labelsize=6.8, length=0)
    ax.tick_params(axis="x", labelsize=9)
    ax.set_xlabel("Crop year")

    for row in range(matrix.shape[0]):
        for column in range(matrix.shape[1]):
            code = matrix[row, column]
            if np.isnan(code):
                continue
            group = label_to_group[int(code)]
            ax.text(
                column,
                row,
                GROUP_LETTER[group],
                ha="center",
                va="center",
                fontsize=7,
                color=GROUP_TEXT_COLOR[group],
                fontweight="bold",
            )

    for state, start, stop in _grower_blocks(field_year, ordered):
        if start > 0:
            ax.axhline(start - 0.5, color="white", linewidth=2.4)
        center = (start + stop - 1) / 2.0
        ax.text(
            1.13,
            1.0 - (center + 0.5) / len(ordered),
            str(state_meta(state)["label"]),
            transform=ax.transAxes,
            ha="left",
            va="center",
            fontsize=9,
            fontweight="bold",
            color=str(state_meta(state)["color"]),
        )

    # Weather window divider: 2021 is the first year of the weather table.
    if 2021 in years:
        divider = years.index(2021) - 0.5
        ax.axvline(divider, color="#222222", linewidth=1.4, linestyle="--")
        ax.text(
            divider + 0.08,
            -1.15,
            "weather window starts",
            fontsize=7.3,
            color="#333333",
            ha="left",
            va="bottom",
        )

    # Side column: mean non-crop share over the panel years.
    side_x = len(years) - 0.5 + 0.34
    ax.axvline(len(years) - 0.5 + 0.06, color="#bbbbbb", linewidth=0.8)
    ax.text(
        side_x,
        -1.15,
        "non-crop\n% mean",
        ha="center",
        va="bottom",
        fontsize=7,
        color="#333333",
    )
    for row, field_id in enumerate(ordered):
        ax.text(
            side_x,
            row,
            f"{noncrop_mean.loc[field_id]:.1f}",
            ha="center",
            va="center",
            fontsize=6.6,
            color="#333333",
        )
    ax.set_xlim(-0.5, len(years) - 0.5 + 0.75)
    ax.set_ylim(len(ordered) - 0.5, -1.5)

    legend_handles = [
        Patch(facecolor=CROP_GROUP_COLORS[group], edgecolor="white", label=group)
        for group in CROP_GROUP_ORDER
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.42, -0.075),
        ncol=6,
        fontsize=7.5,
        handlelength=1.0,
        columnspacing=1.0,
    )

    ax.set_title("C1  What each field grew, 2019-2025", pad=14)
    ax.text(
        0.0,
        1.012,
        "Dominant crop group per field-year; letters mark the group.",
        transform=ax.transAxes,
        fontsize=8,
        color="#444444",
    )
    add_footer(
        fig,
        "Rows are grouped by grower. C = corn, S = soybeans, W = small grain, P = pasture/hay, "
        "O = other crop, N = non-crop. The dashed line marks the first year with matching daily "
        "weather; the side column is mean non-crop pixel share across 2019-2025.",
        y=-0.07,
    )
    return save_figure(fig, figures_dir / "c1_crop_sequence_heatmap.png")


def plot_crop_mix(crop_mix: pd.DataFrame, figures_dir: Path) -> Path:
    apply_style()
    states = sorted(crop_mix["state"].unique(), key=lambda s: state_meta(s)["order"])
    fig, axes = plt.subplots(1, len(states), figsize=(12.4, 4.7), sharey=True)
    if len(states) == 1:
        axes = [axes]

    column_lookup = {
        "Corn": "corn_pct",
        "Soybeans": "soybean_pct",
        "Small grain": "small_grain_pct",
        "Pasture/Hay": "pasture_hay_pct",
        "Other crop": "other_crop_pct",
        "Non-crop": "noncrop_pct",
    }

    for ax, state in zip(axes, states):
        subset = crop_mix.loc[crop_mix["state"] == state].sort_values("year")
        years = subset["year"].tolist()
        x = np.arange(len(years))
        bottom = np.zeros(len(subset))
        for group in CROP_GROUP_ORDER:
            values = subset[column_lookup[group]].to_numpy()
            ax.bar(
                x,
                values,
                bottom=bottom,
                width=0.72,
                color=CROP_GROUP_COLORS[group],
                edgecolor="white",
                linewidth=0.6,
                label=group,
            )
            if group in ("Corn", "Soybeans"):
                for index, value in enumerate(values):
                    if value >= 9:
                        ax.text(
                            x[index],
                            bottom[index] + value / 2,
                            f"{value:.0f}",
                            ha="center",
                            va="center",
                            fontsize=6.8,
                            color=GROUP_TEXT_COLOR[group],
                        )
            bottom = bottom + values
        ax.set_xticks(x, labels=[str(year) for year in years])
        fields = int(subset["fields"].max())
        ax.set_title(
            f"{state_meta(state)['label']}  ({fields} fields per bar)",
            loc="left",
            fontsize=10,
        )
        ax.set_xlabel("Crop year")
        ax.set_ylim(0, 100)
        ax.grid(True, axis="y", color="#e6e6e6", linewidth=0.7, zorder=0)
        ax.set_axisbelow(True)

    axes[0].set_ylabel("Share of field pixels (%, equal-weight fields)")
    handles = [
        Patch(facecolor=CROP_GROUP_COLORS[group], edgecolor="white", label=group)
        for group in CROP_GROUP_ORDER
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=6,
        bbox_to_anchor=(0.5, -0.04),
        fontsize=8,
        handlelength=1.0,
    )
    fig.suptitle("C2  Crop mix by grower-year: rotations run in opposite phase", y=1.03)
    fig.text(
        0.5,
        0.965,
        "Each bar is the mean pixel share across that grower's fields, so every field counts once.",
        ha="center",
        fontsize=8.5,
        color="#444444",
    )
    add_footer(
        fig,
        "Non-crop includes developed land, forest, water, wetlands, and fallow. Shares sum to 100% by construction.",
        y=-0.10,
    )
    return save_figure(fig, figures_dir / "c2_crop_mix_by_grower_year.png")


def plot_transition_matrix(
    transitions: pd.DataFrame, rotation: pd.DataFrame, figures_dir: Path
) -> Path:
    apply_style()
    states = sorted(transitions["state"].unique(), key=lambda s: state_meta(s)["order"])
    categories = [group for group in CROP_GROUP_ORDER if group in set(transitions["from_crop"]) | set(transitions["to_crop"])]
    if not categories:
        categories = ["Corn", "Soybeans"]

    matrices = {}
    max_count = 0
    for state in states:
        subset = transitions.loc[transitions["state"] == state]
        matrix = (
            subset.pivot_table(
                index="from_crop",
                columns="to_crop",
                values="field_id",
                aggfunc="count",
                fill_value=0,
            )
            .reindex(index=categories, columns=categories, fill_value=0)
        )
        matrices[state] = matrix
        max_count = max(max_count, int(matrix.to_numpy().max()))

    fig, axes = plt.subplots(1, len(states), figsize=(12.8, 4.5))
    if len(states) == 1:
        axes = [axes]

    image = None
    for ax, state in zip(axes, states):
        matrix = matrices[state]
        image = ax.imshow(
            matrix.to_numpy(),
            cmap="Blues",
            vmin=0,
            vmax=max_count,
            aspect="equal",
        )
        ax.set_xticks(range(len(categories)), labels=categories, rotation=25, ha="right", fontsize=8)
        ax.set_yticks(range(len(categories)), labels=categories, fontsize=8)
        ax.set_xlabel("Later crop")
        if ax is axes[0]:
            ax.set_ylabel("Earlier crop")

        row_totals = matrix.sum(axis=1).replace(0, np.nan)
        for row in range(len(categories)):
            for column in range(len(categories)):
                count = int(matrix.iloc[row, column])
                pct = (count / row_totals.iloc[row] * 100) if row_totals.iloc[row] else np.nan
                ax.text(
                    column,
                    row,
                    f"{count}\n({pct:.0f}%)" if not np.isnan(pct) else f"{count}",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="white" if count > max_count * 0.55 else "#222222",
                    fontweight="bold" if row == column else "normal",
                )
        grower_rotation = rotation.loc[rotation["state"] == state]
        alternation = grower_rotation["alternation_rate"].mean() * 100
        ax.set_title(
            f"{state_meta(state)['label']}: {len(grower_rotation)} fields\n"
            f"{int(matrix.to_numpy().sum())} transitions, {alternation:.0f}% corn-soy alternation",
            loc="left",
            fontsize=9.5,
        )
        ax.set_frame_on(False)
        ax.tick_params(length=0)

    if image is not None:
        colorbar = fig.colorbar(image, ax=axes.tolist(), fraction=0.035, pad=0.03, aspect=30)
        colorbar.set_label("Number of transitions")
        colorbar.outline.set_visible(False)

    fig.suptitle("C3  Observed dominant-crop transitions, 2021-2025", y=1.04)
    fig.text(
        0.5,
        0.965,
        "Diagonal cells are persistence; off-diagonal cells are alternation. Cell text is count (row %).",
        ha="center",
        fontsize=8.5,
        color="#444444",
    )
    add_footer(
        fig,
        "Counts are observed CDL history, not a forecast. Each grower has 10 fields x 4 transitions = 40.",
        y=-0.08,
    )
    return save_figure(fig, figures_dir / "c3_transition_matrix.png")


def build_all(
    field_year: pd.DataFrame,
    rotation: pd.DataFrame,
    transitions: pd.DataFrame,
    crop_mix: pd.DataFrame,
    figures_dir: Path,
) -> list[Path]:
    apply_style()
    return [
        plot_crop_sequence(field_year, figures_dir),
        plot_crop_mix(crop_mix, figures_dir),
        plot_transition_matrix(transitions, rotation, figures_dir),
    ]
