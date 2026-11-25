"""Shared discovery, loading, validation, and style helpers for field-level EDA.

The module deliberately knows nothing about plotting. It resolves a runtime data root,
discovers grower/farm inputs, loads the three table families, and provides small
validation/manifest primitives that the category modules and CLI share.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ACRE_M2 = 4046.8564224
EQUAL_AREA_CRS = "EPSG:5070"

WEATHER_REQUIRED_COLUMNS = [
    "field_id",
    "lat",
    "lon",
    "date",
    "T2M",
    "T2M_MAX",
    "T2M_MIN",
    "PRECTOTCORR",
    "ALLSKY_SFC_SW_DWN",
    "RH2M",
    "WS10M",
]

# Fixed story order: environment gradient (cool/wet -> intermediate -> hot/dry).
# Tables and figures read in this order so the narrative and the numbers agree.
STATE_META = {
    "IA": {"label": "Iowa", "color": "#2b7a8c", "order": 0},
    "IL": {"label": "Illinois", "color": "#5b8c3e", "order": 1},
    "NE": {"label": "Nebraska", "color": "#c4622d", "order": 2},
}
FALLBACK_COLORS = ["#6c5b7b", "#3f7f93", "#b08968", "#7f9c5e"]

CROP_GROUP_COLORS = {
    "Corn": "#e0a800",
    "Soybeans": "#4f7d3a",
    "Small grain": "#b08d57",
    "Pasture/Hay": "#a9c47f",
    "Other crop": "#9c8aa5",
    "Non-crop": "#bdbdbd",
    "Unclassified": "#e6e6e6",
}
CROP_GROUP_ORDER = [
    "Corn",
    "Soybeans",
    "Small grain",
    "Pasture/Hay",
    "Other crop",
    "Non-crop",
]

_WEATHER_RE = re.compile(r"_weather_(\d{4})_(\d{4})\.csv$")
_CDL_RE = re.compile(r"_(\d{4})_cdl\.csv$")


# ---------------------------------------------------------------------------
# Paths and discovery
# ---------------------------------------------------------------------------


def data_root() -> Path:
    """Resolve the runtime data root, matching the data-pipeline convention."""
    raw = os.environ.get("DATA_PIPELINE_DATA_ROOT", "~/my-farm-advisor-runtime")
    root = Path(raw).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(
            f"DATA_PIPELINE_DATA_ROOT does not exist: {root}. "
            "Set it to the runtime directory, for example ~/my-farm-advisor-runtime."
        )
    return root


def growers_root(root: Path | None = None) -> Path:
    return (root or data_root()) / "data-pipeline" / "growers"


def state_meta(state: str) -> dict[str, object]:
    meta = STATE_META.get(str(state).upper())
    if meta is not None:
        return meta
    index = abs(hash(str(state).upper())) % len(FALLBACK_COLORS)
    return {"label": str(state).upper(), "color": FALLBACK_COLORS[index], "order": 99}


def state_order(state: str) -> int:
    return int(state_meta(state)["order"])


def grower_sort_key(state: str, grower_slug: str) -> tuple[int, str]:
    return (state_order(state), grower_slug)


def natural_key(text: object) -> list[object]:
    """Sort field ids like il-r2 before il-r10."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", str(text))
    ]


def field_sort_key(field_id: object) -> list[object]:
    return natural_key(field_id)


def natural_rank(values: pd.Series) -> pd.Series:
    """Integer sort ranks for ids like il-r2 before il-r10 (safe for pandas sort)."""
    unique = sorted({str(value) for value in values}, key=natural_key)
    mapping = {value: index for index, value in enumerate(unique)}
    return pd.Series(
        [mapping[str(value)] for value in values], index=values.index, dtype="int64"
    )


def pick_weather_table(tables_dir: Path) -> Path:
    candidates: list[tuple[int, int, Path]] = []
    for path in tables_dir.glob("*_weather_*.csv"):
        match = _WEATHER_RE.search(path.name)
        if not match:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        candidates.append((end - start, end, path))
    if not candidates:
        raise FileNotFoundError(
            f"No weather table matching *_weather_<start>_<end>.csv in {tables_dir}"
        )
    # Widest span wins; newest end year breaks ties; name breaks the rest.
    candidates.sort(key=lambda item: (item[0], item[1], item[2].name))
    return candidates[-1][2]


def discover_cdl_tables(tables_dir: Path) -> dict[int, Path]:
    found: dict[int, Path] = {}
    for path in tables_dir.glob("*_cdl.csv"):
        match = _CDL_RE.search(path.name)
        if not match:
            continue
        year = int(match.group(1))
        found.setdefault(year, path)
    return dict(sorted(found.items()))


@dataclass(frozen=True)
class FarmPaths:
    grower_slug: str
    farm_slug: str
    state: str
    state_label: str
    county: str
    display_name: str
    farm_dir: Path
    boundary_path: Path
    tables_dir: Path
    weather_path: Path
    cdl_paths: dict[int, Path] = field(default_factory=dict)
    field_count: int = 0

    @property
    def short_key(self) -> str:
        return self.state_label.lower()

    @property
    def color(self) -> str:
        return str(state_meta(self.state)["color"])

    @property
    def cdl_years(self) -> list[int]:
        return sorted(self.cdl_paths)


def _read_farm_meta(farm_dir: Path) -> dict:
    path = farm_dir / "farm.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def discover_farms(
    grower_slugs: list[str] | None = None,
    root: Path | None = None,
    require_cdl: bool = True,
) -> list[FarmPaths]:
    """Discover farms that have boundaries, weather, and (optionally) CDL tables."""
    base = growers_root(root)
    if not base.exists():
        raise FileNotFoundError(f"Grower root does not exist: {base}")

    selected = set(grower_slugs or [])
    farms: list[FarmPaths] = []
    for grower_dir in sorted(base.iterdir()):
        if not grower_dir.is_dir():
            continue
        if selected and grower_dir.name not in selected:
            continue
        farm_root = grower_dir / "farms"
        if not farm_root.exists():
            continue
        for farm_dir in sorted(farm_root.iterdir()):
            if not farm_dir.is_dir():
                continue
            boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
            tables_dir = farm_dir / "derived" / "tables"
            if not boundary_path.exists() or not tables_dir.exists():
                continue

            boundaries = gpd.read_file(boundary_path)
            if boundaries.empty:
                raise ValueError(f"Boundary file is empty: {boundary_path}")
            if "field_id" not in boundaries.columns:
                raise ValueError(f"Boundary file has no field_id column: {boundary_path}")

            meta = _read_farm_meta(farm_dir)
            state = _first_value(boundaries, "state") or str(meta.get("state", "")).upper()
            county = _first_value(boundaries, "county") or str(meta.get("county", ""))
            display_name = str(
                meta.get("display_name")
                or f"{state_meta(state)['label']} {county}".strip()
            )

            cdl_paths = discover_cdl_tables(tables_dir)
            if require_cdl and len(cdl_paths) < 2:
                raise FileNotFoundError(
                    f"Need at least two CDL year tables in {tables_dir}; "
                    f"found {sorted(cdl_paths)}"
                )

            farms.append(
                FarmPaths(
                    grower_slug=grower_dir.name,
                    farm_slug=farm_dir.name,
                    state=str(state).upper(),
                    state_label=str(state_meta(state)["label"]),
                    county=str(county),
                    display_name=display_name,
                    farm_dir=farm_dir,
                    boundary_path=boundary_path,
                    tables_dir=tables_dir,
                    weather_path=pick_weather_table(tables_dir),
                    cdl_paths=cdl_paths,
                    field_count=int(boundaries["field_id"].nunique()),
                )
            )

    if not farms:
        raise FileNotFoundError(
            f"No farms with boundaries and weather tables found under {base}."
        )
    farms.sort(key=lambda farm: grower_sort_key(farm.state, farm.grower_slug))
    return farms


def _first_value(frame: pd.DataFrame, column: str) -> str:
    if column not in frame.columns:
        return ""
    values = frame[column].dropna().astype(str).str.strip()
    return values.iloc[0] if not values.empty else ""


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_boundaries(farm: FarmPaths) -> gpd.GeoDataFrame:
    boundaries = gpd.read_file(farm.boundary_path)
    if boundaries.crs is None:
        boundaries = boundaries.set_crs("EPSG:4326")
    boundaries = boundaries.to_crs("EPSG:4326")
    if boundaries["field_id"].duplicated().any():
        dupes = sorted(boundaries.loc[boundaries["field_id"].duplicated(), "field_id"])
        raise ValueError(f"Duplicate field_id values in {farm.boundary_path}: {dupes}")
    boundaries["field_id"] = boundaries["field_id"].astype(str)
    boundaries = boundaries.assign(_order=natural_rank(boundaries["field_id"]))
    return boundaries.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def load_weather(farm: FarmPaths) -> pd.DataFrame:
    frame = pd.read_csv(farm.weather_path, parse_dates=["date"])
    missing = [col for col in WEATHER_REQUIRED_COLUMNS if col not in frame.columns]
    if missing:
        raise ValueError(f"{farm.weather_path} is missing columns: {missing}")
    frame["field_id"] = frame["field_id"].astype(str)
    frame["grower"] = farm.state_label
    frame["state"] = farm.state
    frame["grower_slug"] = farm.grower_slug
    frame["farm"] = farm.farm_slug
    return frame


def load_cdl_years(farm: FarmPaths, years: list[int]) -> pd.DataFrame:
    available = farm.cdl_years
    missing = [year for year in years if year not in available]
    if missing:
        raise FileNotFoundError(
            f"{farm.farm_slug} is missing CDL year tables for {missing}"
        )
    frames = []
    for year in years:
        frame = pd.read_csv(farm.cdl_paths[year])
        if "year" in frame.columns:
            observed = sorted(frame["year"].dropna().unique().tolist())
            if observed and observed != [year]:
                raise ValueError(
                    f"{farm.cdl_paths[year]} declares year(s) {observed}, expected {year}"
                )
        frame["year"] = year
        frames.append(frame)
    combined = pd.concat(frames, ignore_index=True)
    combined["field_id"] = combined["field_id"].astype(str)
    combined["grower"] = farm.state_label
    combined["state"] = farm.state
    combined["grower_slug"] = farm.grower_slug
    combined["farm"] = farm.farm_slug
    return combined


def common_cdl_years(farms: list[FarmPaths], start_year: int | None = None) -> list[int]:
    year_sets = [set(farm.cdl_years) for farm in farms]
    shared = set.intersection(*year_sets) if year_sets else set()
    years = sorted(shared)
    if start_year is not None:
        years = [year for year in years if year >= start_year]
    if len(years) < 2:
        raise ValueError(
            "Need at least two CDL years shared by every selected farm; "
            f"found {years}"
        )
    return years


def common_weather_years(weather: pd.DataFrame) -> list[int]:
    years = sorted(weather["date"].dt.year.unique().tolist())
    counts = weather.groupby(weather["date"].dt.year)["date"].nunique()
    incomplete = [int(year) for year in years if int(counts.get(year, 0)) < 365]
    if incomplete:
        raise ValueError(f"Weather years with fewer than 365 days: {incomplete}")
    return [int(year) for year in years]


# ---------------------------------------------------------------------------
# Validation and manifest
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    status: str
    expected: str
    observed: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    def as_dict(self) -> dict[str, str]:
        payload = {
            "name": self.name,
            "status": self.status,
            "expected": self.expected,
            "observed": self.observed,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


def check(
    name: str, expected: object, observed: object, ok: bool, detail: str = ""
) -> Check:
    return Check(
        name=name,
        status="pass" if ok else "fail",
        expected=str(expected),
        observed=str(observed),
        detail=detail,
    )


def raise_on_fail(checks: list[Check], label: str) -> None:
    failures = [item for item in checks if not item.ok]
    if not failures:
        return
    lines = [
        f"{item.name}: expected {item.expected}, observed {item.observed}"
        for item in failures
    ]
    raise ValueError(f"{label} QC failed:\n  " + "\n  ".join(lines))


def checks_status(checks: list[Check]) -> str:
    return "pass" if all(item.ok for item in checks) else "fail"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def ensure_inside_runtime(path: Path, root: Path | None = None) -> Path:
    """Refuse to write outputs outside the runtime data root."""
    resolved = path.expanduser().resolve()
    allowed = (root or data_root()).resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(
            f"Output directory must be inside the runtime data root {allowed}; "
            f"got {resolved}"
        )
    return resolved


def relative_outputs(root: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        records.append(
            {
                "path": str(path.relative_to(root)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


# ---------------------------------------------------------------------------
# Figure style
# ---------------------------------------------------------------------------


def apply_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "savefig.facecolor": "white",
            "figure.facecolor": "white",
            "font.size": 9,
            "axes.titlesize": 11,
            "axes.titleweight": "bold",
            "axes.labelsize": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#444444",
            "axes.labelcolor": "#222222",
            "xtick.color": "#333333",
            "ytick.color": "#333333",
            "legend.frameon": False,
            "legend.fontsize": 8,
            "figure.titlesize": 13,
            "figure.titleweight": "bold",
        }
    )


def save_figure(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig)
    return path


def add_footer(fig: plt.Figure, text: str, y: float = -0.02) -> None:
    fig.text(0.005, y, text, ha="left", va="bottom", fontsize=7.5, color="#555555")
