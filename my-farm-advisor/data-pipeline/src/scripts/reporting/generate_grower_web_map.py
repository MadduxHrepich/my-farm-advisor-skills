#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Generate a lightweight interactive HTML map for each grower.

The map embeds exactly two small vector layers — the downloaded field
boundaries and the per-field SSURGO map-unit polygons — over keyless ESRI
World Imagery tiles. No imagery, rasters, or binary bundles are embedded, so
each HTML file stays in the low hundreds of kilobytes and opens straight from
disk in a browser.

Output (canonical runtime paths):

    growers/<grower>/derived/reports/<grower>_grower_map.html
    growers/<grower>/manifests/grower_web_map.json

Usage:
    python scripts/reporting/generate_grower_web_map.py --all-growers
    python scripts/reporting/generate_grower_web_map.py --grower-slug illinois-grower
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS / "lib"))

from paths import (  # noqa: E402
    DATA_ROOT,
    farm_boundary_path,
    farm_soil_sample_path,
    field_dir,
    field_soil_summary_path,
    grower_dir,
    grower_manifest_dir,
    grower_web_map_path,
)

ESRI_IMAGERY_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery"
    "/MapServer/tile/{z}/{y}/{x}"
)
ESRI_LABELS_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Reference"
    "/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
)
# OSM public tiles now reject referer-less requests from local file:// pages,
# so the street basemap stays on the same keyless Esri service as the imagery.
ESRI_STREET_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map"
    "/MapServer/tile/{z}/{y}/{x}"
)

FIELD_COLORS = {
    "corn": "#d97706",
    "soybeans": "#16a34a",
    "wheat": "#ca8a04",
    "cotton": "#94a3b8",
    "other": "#64748b",
}
DRAINAGE_COLORS = {
    "Excessively drained": "#0ea5e9",
    "Somewhat excessively drained": "#38bdf8",
    "Well drained": "#22c55e",
    "Moderately well drained": "#a3e635",
    "Somewhat poorly drained": "#f59e0b",
    "Poorly drained": "#ef4444",
    "Very poorly drained": "#7f1d1d",
}
DRAINAGE_FALLBACK_COLOR = "#94a3b8"
SOIL_TOLERANCES = (5e-5, 1e-4, 2e-4)
DEFAULT_SOIL_BUDGET_KB = 350
MIN_SOIL_UNIT_ACRES = 0.1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _round_coords(value: Any, digits: int) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, (list, tuple)):
        return [_round_coords(item, digits) for item in value]
    if isinstance(value, dict):
        return {key: _round_coords(item, digits) for key, item in value.items()}
    return value


def _json_blob(payload: Any) -> str:
    """Compact JSON that cannot terminate the embedding <script> element."""
    return json.dumps(payload, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")


def _display_grower_name(grower_slug: str, meta: dict[str, Any]) -> str:
    display = str(meta.get("display_name") or "").strip()
    if display and display != grower_slug:
        return display
    return grower_slug.replace("-", " ").replace("_", " ").title()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _discover_growers() -> list[str]:
    root = DATA_ROOT / "growers"
    if not root.is_dir():
        return []
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir() and (path / "farms").is_dir()
    )


def _norm_crop(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "corn" in text:
        return "corn"
    if "soy" in text:
        return "soybeans"
    if "wheat" in text:
        return "wheat"
    if "cotton" in text:
        return "cotton"
    return "other"


def _weighted_mean(rows: pd.DataFrame, column: str, weights: pd.Series) -> float | None:
    if column not in rows.columns:
        return None
    values = pd.to_numeric(rows[column], errors="coerce")
    mask = values.notna() & weights.notna() & (weights > 0)
    if mask.any():
        weight_sum = float(weights[mask].sum())
        if weight_sum > 0:
            return float((values[mask] * weights[mask]).sum() / weight_sum)
    if values.notna().any():
        return float(values.mean())
    return None


def _soil_attribute_table(farm_slug: str, grower_slug: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Per (field_id, mukey) dominant component and profile-weighted properties."""
    csv_path = farm_soil_sample_path(grower_slug, farm_slug)
    if not csv_path.exists():
        return {}
    try:
        frame = pd.read_csv(csv_path)
    except (OSError, pd.errors.ParserError):
        return {}
    if frame.empty or not {"field_id", "mukey", "compname"}.issubset(frame.columns):
        return {}

    attributes: dict[tuple[str, str], dict[str, Any]] = {}
    for (field_id, mukey), group in frame.groupby(["field_id", "mukey"], dropna=True):
        ordered = group.sort_values("comppct_r", ascending=False) if "comppct_r" in group else group
        dominant = ordered.iloc[0]
        component_rows = ordered[ordered["compname"] == dominant["compname"]]
        if "hzdept_r" in component_rows and "hzdepb_r" in component_rows:
            thickness = (
                pd.to_numeric(component_rows["hzdepb_r"], errors="coerce")
                - pd.to_numeric(component_rows["hzdept_r"], errors="coerce")
            ).clip(lower=0)
        else:
            thickness = pd.Series([1.0] * len(component_rows), index=component_rows.index)
        component_pct = pd.to_numeric(pd.Series([dominant.get("comppct_r")]), errors="coerce").iloc[0]
        attributes[(str(field_id), str(mukey))] = {
            "series": str(dominant["compname"]),
            "component_pct": None if pd.isna(component_pct) else float(component_pct),
            "drainage": str(dominant.get("drainagecl") or "").strip() or None,
            "om": _weighted_mean(component_rows, "om_r", thickness),
            "ph": _weighted_mean(component_rows, "ph1to1h2o_r", thickness),
            "awc": _weighted_mean(component_rows, "awc_r", thickness),
        }
    return attributes


def _field_soil_summary(grower_slug: str, farm_slug: str, field_slug: str) -> dict[str, Any]:
    path = field_soil_summary_path(grower_slug, farm_slug, field_slug)
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError):
        return {}
    if frame.empty:
        return {}
    row = frame.iloc[0]
    return {
        "soil_series": _clean_text(row.get("dominant_soil")),
        "soil_drainage": _clean_text(row.get("drainage_class")),
        "soil_aws": _clean_number(row.get("total_aws_inches")),
        "soil_om": _clean_number(row.get("avg_om_pct")),
        "soil_ph": _clean_number(row.get("avg_ph")),
        "soil_erosion": _clean_text(row.get("erosion_risk")),
        "soil_ph_constraint": _clean_text(row.get("ph_constraint")),
    }


def _clean_text(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _clean_number(value: Any) -> float | None:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(number) else float(number)


# --------------------------------------------------------------------------- #
# payload builders
# --------------------------------------------------------------------------- #


def _collect_fields(grower_slug: str, farm_slug: str) -> tuple[gpd.GeoDataFrame, list[dict[str, Any]]]:
    boundary_path = farm_boundary_path(grower_slug, farm_slug)
    if not boundary_path.exists():
        return gpd.GeoDataFrame(), []
    fields = gpd.read_file(boundary_path)
    if fields.empty:
        return fields, []
    if fields.crs is not None and str(fields.crs).upper() not in {"EPSG:4326", "WGS 84"}:
        fields = fields.to_crs("EPSG:4326")
    elif fields.crs is None:
        fields = fields.set_crs("EPSG:4326", allow_override=True)

    records: list[dict[str, Any]] = []
    for _, row in fields.iterrows():
        bounds = row.geometry.bounds
        field_id = str(row.get("field_id") or "")
        field_slug = str(row.get("field_slug") or field_id)
        soil = _field_soil_summary(grower_slug, farm_slug, field_slug)
        records.append(
            {
                "field_id": field_id,
                "field_slug": field_slug,
                "farm_slug": farm_slug,
                "crop": _clean_text(row.get("crop")),
                "acres": _clean_number(row.get("area_acres")),
                "county": _clean_text(row.get("county")),
                "state": _clean_text(row.get("state")),
                "source": _clean_text(row.get("source")),
                "bbox": [float(bounds[0]), float(bounds[1]), float(bounds[2]), float(bounds[3])],
                **soil,
            }
        )
    return fields, records


def _build_soil_layer(
    grower_slug: str,
    farm_slug: str,
    field_records: list[dict[str, Any]],
    attributes: dict[tuple[str, str], dict[str, Any]],
    tolerance: float,
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for record in field_records:
        field_id = record["field_id"]
        field_slug = record.get("field_slug") or field_id
        soil_path = field_dir(grower_slug, farm_slug, field_slug) / "soil" / "ssurgo_soil_types.geojson"
        if not soil_path.exists():
            continue
        try:
            polygons = gpd.read_file(soil_path)
        except Exception as exc:  # noqa: BLE001 - a broken cache must not kill the map
            print(f"  WARN: unreadable SSURGO cache {soil_path}: {exc}", file=sys.stderr)
            continue
        if polygons.empty:
            continue
        if polygons.crs is not None and str(polygons.crs).upper() != "EPSG:4326":
            polygons = polygons.to_crs("EPSG:4326")
        elif polygons.crs is None:
            polygons = polygons.set_crs("EPSG:4326", allow_override=True)

        polygons = polygons[polygons.geometry.notna() & ~polygons.geometry.is_empty].copy()
        if polygons.empty:
            continue
        try:
            polygons["geometry"] = polygons.geometry.make_valid()
        except Exception:  # noqa: BLE001 - older shapely without make_valid is fine
            pass
        polygons = polygons[polygons["mukey"].notna()]
        if polygons.empty:
            continue

        dissolved = polygons[["mukey", "geometry"]].dissolve(by="mukey").reset_index()
        try:
            utm = dissolved.estimate_utm_crs()
            areas = dissolved.to_crs(utm).geometry.area / 4046.8564224
            dissolved = dissolved[areas >= MIN_SOIL_UNIT_ACRES]
        except Exception:  # noqa: BLE001 - area filter is best-effort
            pass
        if dissolved.empty:
            continue
        dissolved["geometry"] = dissolved.geometry.simplify(tolerance, preserve_topology=True)

        for _, unit in dissolved.iterrows():
            mukey = str(unit["mukey"])
            attrs = attributes.get((field_id, mukey), {})
            geometry = unit.geometry
            if geometry is None or geometry.is_empty:
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "field_id": field_id,
                        "farm_slug": farm_slug,
                        "mukey": mukey,
                        "series": attrs.get("series"),
                        "component_pct": attrs.get("component_pct"),
                        "drainage": attrs.get("drainage"),
                        "om": attrs.get("om"),
                        "ph": attrs.get("ph"),
                        "awc": attrs.get("awc"),
                    },
                    "geometry": _round_coords(geometry.__geo_interface__, 5),
                }
            )
    return features


def _build_payload(
    grower_slug: str,
    soil_budget_bytes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    grower_meta = _load_json(grower_dir(grower_slug) / "grower.json")
    grower_name = _display_grower_name(grower_slug, grower_meta)

    farms_payload: list[dict[str, Any]] = []
    field_features: list[dict[str, Any]] = []
    all_field_records: list[dict[str, Any]] = []
    per_farm_records: list[tuple[str, str, list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]] = []

    farms_root = grower_dir(grower_slug) / "farms"
    for farm_path in sorted(path for path in farms_root.iterdir() if path.is_dir()):
        farm_slug = farm_path.name
        farm_meta = _load_json(farm_path / "farm.json")
        farm_name = str(farm_meta.get("display_name") or farm_slug.replace("-", " ").title())
        fields_gdf, records = _collect_fields(grower_slug, farm_slug)
        if not records:
            print(f"  WARN: no field boundaries for {grower_slug}/{farm_slug}", file=sys.stderr)
            continue

        for record, (_, row) in zip(records, fields_gdf.iterrows()):
            field_features.append(
                {
                    "type": "Feature",
                    "properties": dict(record),
                    "geometry": _round_coords(row.geometry.__geo_interface__, 6),
                }
            )
        all_field_records.extend(records)
        attributes = _soil_attribute_table(farm_slug, grower_slug)
        per_farm_records.append((farm_slug, farm_name, records, attributes))
        farms_payload.append(
            {
                "slug": farm_slug,
                "name": farm_name,
                "fields": [record["field_id"] for record in records],
            }
        )

    soil_features: list[dict[str, Any]] = []
    tolerance_used = SOIL_TOLERANCES[0]
    for tolerance in SOIL_TOLERANCES:
        candidate: list[dict[str, Any]] = []
        for farm_slug, _farm_name, records, attributes in per_farm_records:
            candidate.extend(_build_soil_layer(grower_slug, farm_slug, records, attributes, tolerance))
        tolerance_used = tolerance
        soil_features = candidate
        if len(_json_blob({"type": "FeatureCollection", "features": candidate})) <= soil_budget_bytes:
            break

    payload = {
        "grower": {"slug": grower_slug, "name": grower_name},
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "farms": farms_payload,
        "fields": {"type": "FeatureCollection", "features": field_features},
        "soil": {"type": "FeatureCollection", "features": soil_features},
        "soil_tolerance_deg": tolerance_used,
        "field_colors": FIELD_COLORS,
        "drainage_colors": DRAINAGE_COLORS,
        "drainage_fallback": DRAINAGE_FALLBACK_COLOR,
    }
    stats = {
        "field_count": len(all_field_records),
        "farm_count": len(farms_payload),
        "soil_unit_count": len(soil_features),
        "soil_tolerance_deg": tolerance_used,
        "total_acres": round(sum(record["acres"] or 0 for record in all_field_records), 1),
        "fields_with_soil": sum(1 for record in all_field_records if record.get("soil_series")),
    }
    return payload, stats


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>%%TITLE%%</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body { height: 100%; margin: 0; }
  body { font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #0f172a; }
  #app { display: flex; height: 100vh; }
  #sidebar { width: 330px; flex: 0 0 330px; overflow-y: auto; background: #f8fafc; border-right: 1px solid #e2e8f0; display: flex; flex-direction: column; }
  #sidebar header { padding: 16px 16px 12px; border-bottom: 1px solid #e2e8f0; background: #fff; position: sticky; top: 0; z-index: 2; }
  #sidebar h1 { font-size: 17px; margin: 0 0 4px; }
  #sidebar .meta { color: #475569; font-size: 12px; }
  #tools { padding: 10px 16px; border-bottom: 1px solid #e2e8f0; background: #fff; }
  #tools button { border: 1px solid #cbd5e1; background: #fff; border-radius: 6px; padding: 6px 10px; font-size: 12px; cursor: pointer; }
  #tools button:hover { background: #f1f5f9; }
  .farm { padding: 10px 10px 4px; }
  .farm h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: #475569; margin: 8px 6px 6px; }
  .field { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; border: 1px solid transparent; background: none; border-radius: 6px; padding: 6px; cursor: pointer; font: inherit; }
  .field:hover { background: #eef2f7; }
  .field.active { background: #e0f2fe; border-color: #7dd3fc; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 10px; }
  .field .label { flex: 1; min-width: 0; }
  .field .name { font-weight: 600; }
  .field .sub { color: #64748b; font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #sidebar footer { margin-top: auto; padding: 12px 16px; border-top: 1px solid #e2e8f0; color: #64748b; font-size: 11px; background: #fff; }
  #mapwrap { position: relative; flex: 1; }
  #map { position: absolute; inset: 0; }
  #legend { position: absolute; right: 12px; bottom: 24px; z-index: 500; background: rgba(255,255,255,.94); border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px; font-size: 11px; max-width: 230px; }
  #legend h3 { margin: 0 0 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #475569; }
  #legend .row { display: flex; align-items: center; gap: 6px; margin: 2px 0; }
  #legend .sw { width: 12px; height: 12px; border-radius: 3px; border: 1px solid rgba(15,23,42,.15); }
  .pop { min-width: 210px; }
  .pop h4 { margin: 0 0 6px; font-size: 13px; }
  .pop table { border-collapse: collapse; width: 100%; font-size: 12px; }
  .pop th { text-align: left; color: #475569; font-weight: 600; padding: 2px 8px 2px 0; vertical-align: top; white-space: nowrap; }
  .pop td { padding: 2px 0; }
  .pop .src { margin-top: 6px; color: #64748b; font-size: 10px; }
  @media (max-width: 760px) {
    #app { flex-direction: column; }
    #sidebar { width: 100%; flex: 0 0 40vh; border-right: 0; border-bottom: 1px solid #e2e8f0; }
    #mapwrap { flex: 1 1 60vh; }
  }
</style>
</head>
<body>
<div id="app">
  <aside id="sidebar">
    <header>
      <h1 id="grower-name"></h1>
      <div class="meta" id="grower-meta"></div>
    </header>
    <div id="tools"><button type="button" id="fit-all">Fit all fields</button></div>
    <div id="field-list"></div>
    <footer>
      <div id="generated"></div>
      <div>Fields &amp; SSURGO polygons embedded from pipeline output. Imagery &copy; Esri.</div>
    </footer>
  </aside>
  <div id="mapwrap">
    <div id="map"></div>
    <div id="legend"></div>
  </div>
</div>
<script id="map-data" type="application/json">%%DATA%%</script>
<script>
(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("map-data").textContent);
  const farmName = {};
  DATA.farms.forEach(function (farm) { farmName[farm.slug] = farm.name; });

  const map = L.map("map", {zoomControl: false, scrollWheelZoom: true});
  L.control.zoom({position: "topright"}).addTo(map);

  // Field fill sits under the soil polygons so a click inside a field lands on
  // the SSURGO map unit, whose popup also carries the field metadata. The crisp
  // field outlines are non-interactive and stay on top.
  map.createPane("fieldPane");
  map.getPane("fieldPane").style.zIndex = 340;
  map.createPane("soilPane");
  map.getPane("soilPane").style.zIndex = 350;
  map.createPane("outlinePane");
  map.getPane("outlinePane").style.zIndex = 450;

  const imagery = L.tileLayer("%%ESRI_IMAGERY%%", {
    maxZoom: 19,
    attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community'
  }).addTo(map);

  const labels = L.tileLayer("%%ESRI_LABELS%%", {maxZoom: 19, opacity: 0.9});
  const street = L.tileLayer("%%ESRI_STREET%%", {
    maxZoom: 19,
    attribution: 'Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors'
  });

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[ch];
    });
  }
  function row(key, value) {
    if (value == null || value === "") { return ""; }
    return "<tr><th>" + esc(key) + "</th><td>" + esc(value) + "</td></tr>";
  }
  function num(value, suffix, digits) {
    if (value == null || value === "" || isNaN(Number(value))) { return null; }
    return Number(value).toFixed(digits) + (suffix || "");
  }

  function fieldPopup(props) {
    return '<div class="pop"><h4>Field ' + esc(props.field_id) + "</h4><table>" +
      row("Grower", DATA.grower.name) +
      row("Farm", farmName[props.farm_slug] || props.farm_slug) +
      row("Field name", props.field_id) +
      row("Crop", props.crop) +
      row("Area", num(props.acres, " ac", 1)) +
      row("County", props.county) +
      row("State", props.state) +
      row("SSURGO series", props.soil_series) +
      row("Drainage", props.soil_drainage) +
      row("Total AWS", num(props.soil_aws, " in", 2)) +
      row("Avg OM", num(props.soil_om, " %", 2)) +
      row("Avg pH", num(props.soil_ph, "", 2)) +
      row("Erosion risk", props.soil_erosion) +
      row("pH constraint", props.soil_ph_constraint) +
      "</table><div class='src'>Boundary source: " + esc(props.source || "unknown") +
      " &middot; SSURGO: USDA-NRCS</div></div>";
  }

  function soilPopup(props, field) {
    field = field || {};
    return '<div class="pop"><h4>' + esc(props.series || "SSURGO map unit") + "</h4><table>" +
      row("Grower", DATA.grower.name) +
      row("Farm", farmName[props.farm_slug] || props.farm_slug) +
      row("Field name", props.field_id) +
      row("Crop", field.crop) +
      row("Area", num(field.acres, " ac", 1)) +
      row("Map unit key", props.mukey) +
      row("Dominant component", num(props.component_pct, " %", 0)) +
      row("Drainage class", props.drainage) +
      row("Profile OM", num(props.om, " %", 2)) +
      row("Profile pH", num(props.ph, "", 2)) +
      row("Profile AWC", num(props.awc, "", 2)) +
      "</table><div class='src'>SSURGO &middot; USDA-NRCS</div></div>";
  }

  function fieldStyle(feature) {
    const color = DATA.field_colors[(feature.properties.crop || "").toLowerCase()] || DATA.field_colors.other;
    return {color: color, weight: 2.4, opacity: 0.95, fillColor: color, fillOpacity: 0.06};
  }

  const layerByField = {};
  const fieldProps = {};
  DATA.fields.features.forEach(function (feature) { fieldProps[feature.properties.field_id] = feature.properties; });
  const fieldLayer = L.geoJSON(DATA.fields, {
    pane: "fieldPane",
    style: fieldStyle,
    onEachFeature: function (feature, layer) {
      const props = feature.properties;
      layer.bindPopup(fieldPopup(props), {maxWidth: 320});
      layer.bindTooltip("Field " + props.field_id + (props.crop ? " · " + props.crop : ""), {sticky: true});
      layer.on("mouseover", function () { layer.setStyle({weight: 4, fillOpacity: 0.16}); });
      layer.on("mouseout", function () { fieldLayer.resetStyle(layer); });
      layer.on("click", function () { setActive(props.field_id); });
      layerByField[props.field_id] = layer;
    }
  });

  const soilLayer = L.geoJSON(DATA.soil, {
    pane: "soilPane",
    style: function (feature) {
      const drainage = feature.properties.drainage;
      return {
        color: "#334155",
        weight: 0.6,
        opacity: 0.7,
        fillColor: DATA.drainage_colors[drainage] || DATA.drainage_fallback,
        fillOpacity: 0.38
      };
    },
    onEachFeature: function (feature, layer) {
      const props = feature.properties;
      const field = fieldProps[props.field_id] || {};
      layer.bindPopup(soilPopup(props, field), {maxWidth: 320});
      layer.bindTooltip(
        "Field " + props.field_id + (props.series ? " · " + props.series : ""),
        {sticky: true}
      );
      layer.on("click", function () { setActive(props.field_id); });
    }
  });

  // Crisp field outlines above the soil fill; non-interactive so clicks reach
  // the soil polygons or the field fill underneath.
  const outlineLayer = L.geoJSON(DATA.fields, {
    pane: "outlinePane",
    interactive: false,
    style: function (feature) {
      const color = DATA.field_colors[(feature.properties.crop || "").toLowerCase()] || DATA.field_colors.other;
      return {color: color, weight: 2.4, opacity: 0.95, fill: false};
    }
  });

  const fieldGroup = L.layerGroup([fieldLayer, outlineLayer]).addTo(map);
  if (DATA.soil.features.length) { soilLayer.addTo(map); }

  labels.addTo(map);
  const overlays = {"Field boundaries": fieldGroup, "Place labels": labels};
  if (DATA.soil.features.length) {
    overlays["SSURGO soil map units (drainage)"] = soilLayer;
  }
  L.control.layers({"ESRI World Imagery": imagery, "Esri World Street Map": street}, overlays, {collapsed: false}).addTo(map);

  // The Esri reference labels are drawn for imagery; the street basemap already
  // carries its own labels, so hide the overlay there and restore it on imagery.
  let placeLabelsWanted = true;
  let suppressLabelTracking = false;
  function setOverlayCheckbox(name, checked) {
    document.querySelectorAll(".leaflet-control-layers-overlays label").forEach(function (label) {
      if (label.textContent.trim() === name) { label.querySelector("input").checked = checked; }
    });
  }
  map.on("overlayadd", function (e) {
    if (!suppressLabelTracking && e.layer === labels) { placeLabelsWanted = true; }
  });
  map.on("overlayremove", function (e) {
    if (!suppressLabelTracking && e.layer === labels) { placeLabelsWanted = false; }
  });
  map.on("baselayerchange", function (e) {
    if (e.layer === street) {
      if (map.hasLayer(labels)) {
        suppressLabelTracking = true;
        map.removeLayer(labels);
        suppressLabelTracking = false;
        placeLabelsWanted = true;
        setOverlayCheckbox("Place labels", false);
      }
    } else if (e.layer === imagery && placeLabelsWanted) {
      if (!map.hasLayer(labels)) {
        suppressLabelTracking = true;
        labels.addTo(map);
        suppressLabelTracking = false;
      }
      setOverlayCheckbox("Place labels", true);
    }
  });

  const allBounds = fieldLayer.getBounds();
  map.fitBounds(allBounds, {padding: [30, 30]});

  // sidebar
  document.getElementById("grower-name").textContent = DATA.grower.name;
  const fieldTotal = DATA.fields.features.length;
  const acres = DATA.fields.features.reduce(function (sum, f) { return sum + (Number(f.properties.acres) || 0); }, 0);
  document.getElementById("grower-meta").textContent =
    DATA.farms.length + (DATA.farms.length === 1 ? " farm" : " farms") + " · " +
    fieldTotal + (fieldTotal === 1 ? " field" : " fields") + " · " +
    acres.toLocaleString(undefined, {maximumFractionDigits: 0}) + " ac";
  document.getElementById("generated").textContent = "Generated " + DATA.generated_at + " · runtime data-pipeline";

  const list = document.getElementById("field-list");
  DATA.farms.forEach(function (farm) {
    const section = document.createElement("section");
    section.className = "farm";
    const heading = document.createElement("h2");
    heading.textContent = farm.name;
    section.appendChild(heading);
    DATA.fields.features.forEach(function (feature) {
      const props = feature.properties;
      if (props.farm_slug !== farm.slug) { return; }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "field";
      button.dataset.fieldId = props.field_id;
      const color = DATA.field_colors[(props.crop || "").toLowerCase()] || DATA.field_colors.other;
      button.innerHTML = '<span class="dot" style="background:' + color + '"></span>' +
        '<span class="label"><span class="name">' + esc(props.field_id) + "</span>" +
        '<span class="sub">' + esc((props.crop || "unknown crop") +
          (props.acres != null ? " · " + Number(props.acres).toFixed(0) + " ac" : "") +
          (props.soil_series ? " · " + props.soil_series : "")) + "</span></span>";
      button.addEventListener("click", function () { zoomToField(props); });
      section.appendChild(button);
    });
    list.appendChild(section);
  });

  function setActive(fieldId) {
    document.querySelectorAll(".field").forEach(function (el) {
      el.classList.toggle("active", el.dataset.fieldId === fieldId);
    });
  }

  function zoomToField(props) {
    const b = props.bbox;
    if (!b || b.length !== 4) { return; }
    const layer = layerByField[props.field_id];
    map.flyToBounds(L.latLngBounds([[b[1], b[0]], [b[3], b[2]]]), {padding: [60, 60], maxZoom: 17});
    if (layer) { layer.openPopup(); }
    setActive(props.field_id);
  }

  document.getElementById("fit-all").addEventListener("click", function () {
    map.flyToBounds(allBounds, {padding: [30, 30]});
    setActive(null);
  });

  // legend
  const legend = document.getElementById("legend");
  legend.innerHTML = "<h3>Field crop</h3>" + Object.keys(DATA.field_colors).map(function (crop) {
    return '<div class="row"><span class="sw" style="background:' + DATA.field_colors[crop] + '"></span>' +
      esc(crop.charAt(0).toUpperCase() + crop.slice(1)) + "</div>";
  }).join("") +
  (DATA.soil.features.length ? "<h3 style='margin-top:8px'>SSURGO drainage</h3>" + Object.keys(DATA.drainage_colors).map(function (name) {
    return '<div class="row"><span class="sw" style="background:' + DATA.drainage_colors[name] + '"></span>' + esc(name) + "</div>";
  }).join("") : "");

  setActive(null);
})();
</script>
</body>
</html>
"""


def _render_html(payload: dict[str, Any], title: str) -> str:
    return (
        HTML_TEMPLATE.replace("%%TITLE%%", html_lib.escape(title))
        .replace("%%DATA%%", _json_blob(payload))
        .replace("%%ESRI_IMAGERY%%", ESRI_IMAGERY_URL)
        .replace("%%ESRI_LABELS%%", ESRI_LABELS_URL)
        .replace("%%ESRI_STREET%%", ESRI_STREET_URL)
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grower-slug", action="append", default=None, help="Grower slug (repeatable)")
    parser.add_argument("--all-growers", action="store_true", help="Generate a map for every runtime grower")
    parser.add_argument(
        "--soil-budget-kb",
        type=int,
        default=DEFAULT_SOIL_BUDGET_KB,
        help="Re-simplify SSURGO polygons above this embedded size (default: %(default)s)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    slugs = list(args.grower_slug or [])
    if args.all_growers:
        slugs.extend(_discover_growers())
    slugs = sorted(set(slugs))
    if not slugs:
        print("ERROR: pass --all-growers or at least one --grower-slug", file=sys.stderr)
        return 2
    if args.soil_budget_kb < 50:
        print("ERROR: --soil-budget-kb must be >= 50", file=sys.stderr)
        return 2

    budget_bytes = args.soil_budget_kb * 1024
    failed = False
    for grower_slug in slugs:
        print("=" * 60)
        print(f"Grower web map: {grower_slug}")
        try:
            payload, stats = _build_payload(grower_slug, budget_bytes)
        except FileNotFoundError as exc:
            print(f"  SKIP: {exc}", file=sys.stderr)
            failed = True
            continue
        if not payload["fields"]["features"]:
            print("  SKIP: no field boundaries found", file=sys.stderr)
            failed = True
            continue

        title = f"{payload['grower']['name']} — grower field map"
        html = _render_html(payload, title)
        output_path = grower_web_map_path(grower_slug)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

        manifest = {
            "dataset": "grower_web_map",
            "grower_slug": grower_slug,
            "grower_name": payload["grower"]["name"],
            "generated_at": payload["generated_at"],
            "html_path": str(output_path),
            "html_size_bytes": output_path.stat().st_size,
            "field_count": stats["field_count"],
            "farm_count": stats["farm_count"],
            "total_acres": stats["total_acres"],
            "soil_unit_count": stats["soil_unit_count"],
            "fields_with_soil": stats["fields_with_soil"],
            "soil_simplify_tolerance_deg": stats["soil_tolerance_deg"],
            "basemaps": {"primary": ESRI_IMAGERY_URL, "labels": ESRI_LABELS_URL, "street": ESRI_STREET_URL},
            "sources": {
                "fields": [
                    str(farm_boundary_path(grower_slug, farm["slug"])) for farm in payload["farms"]
                ],
                "ssurgo": "per-field soil/ssurgo_soil_types.geojson + derived/tables/*_fields_soil.csv",
            },
        }
        manifest_path = grower_manifest_dir(grower_slug) / "grower_web_map.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        print(
            f"  ok  {grower_slug}: {stats['field_count']} fields, {stats['soil_unit_count']} soil units, "
            f"{output_path.stat().st_size / 1024:.0f} KB"
        )
        print(f"      {output_path}")
        print(f"      {manifest_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
