---
name: grower-web-map
description: Build one self-contained interactive HTML map per grower, with every downloaded field boundary, SSURGO map-unit polygons coloured by drainage class, click popups, and a sidebar field list.
version: 1.0.0
author: Boreal Bytes
tags: [web-map, leaflet, grower, fields, ssurgo, interactive, html]
---

# Workflow: grower-web-map

## What this produces

One HTML file per grower, plus a manifest:

```text
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/derived/reports/<grower>_grower_map.html
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/manifests/grower_web_map.json
```

The HTML embeds only two small vector layers — the farm boundary GeoJSON and the
per-field SSURGO map-unit polygons — over keyless Esri World Imagery tiles.
No imagery, rasters, or zip bundles are embedded, so a six-field grower lands in
roughly 150–320 KB and the file opens straight from disk in any browser.

## When to use it

Use this when a grower needs a shareable map of their own fields: review a farm,
check field location against imagery, or read the soil context of each field.
It is grower-level and multi-farm, unlike the single-GeoJSON
[interactive web map](../../admin/interactive-web-map/GUIDE.md), which stays the
generic one-file map builder.

The grower must already have downloaded boundaries (and, for the soil layer,
SSURGO vectors) under the runtime tree. Build them first with
`scripts/run_farm_pipeline.py`; see [../README.md](../README.md).

## Command

Run from the runtime source copy, not the skill checkout:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"

# every runtime grower
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py --all-growers

# one grower
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py --grower-slug illinois-grower
```

Optional: `--soil-budget-kb 350` (default) caps the embedded SSURGO payload per
grower. The generator dissolves soil polygons by `mukey`, simplifies at 5e-5 deg,
drops units under 0.1 ac, and falls back to 1e-4 then 2e-4 deg when a grower is
over budget. The tolerance actually used is recorded in the manifest.

Exit code is non-zero if a grower has no boundaries, so a scheduled run cannot
silently produce nothing.

## What is in the map

- **Esri World Imagery** basemap with an optional place-labels overlay and an
  **Esri World Street Map** alternative, switchable in the layer control. The
  reference labels overlay is auto-hidden on the street basemap, which already
  labels its own features. Both basemaps are keyless Esri services;
  OpenStreetMap public tiles are deliberately not used because they reject
  referer-less requests from local `file://` pages. Tiles and Leaflet 1.9.4 load
  from CDNs, so the viewer needs internet access.
- **Field boundaries** for every farm of the grower, coloured by crop, with a
  crisp outline layer above the soil fill.
- **Field popup**: grower, farm, field name, crop, acres, county, state, boundary
  source, plus the SSURGO summary for that field — dominant series, drainage
  class, total available water storage, average organic matter and pH, erosion
  risk, and pH constraint. It opens from a sidebar row, or from a click inside a
  field when the soil layer is hidden.
- **SSURGO map units** drawn over the field fill and coloured by drainage class.
  Clicking one opens a popup with the field identity plus series name, dominant
  component percent, map unit key, drainage class, and profile OM, pH, and AWC.
  Clicking a sidebar row always opens the field-level popup, whichever layer is on.
- **Sidebar** with grower totals and one row per field (crop, acres, dominant
  series). Clicking a row flies to that field and opens its popup; a
  **Fit all fields** button restores the grower extent.
- **Legend** for crop colours and SSURGO drainage classes.

## Where the data comes from

| Layer | Source path (runtime) |
| --- | --- |
| Field boundaries | `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson` |
| SSURGO polygons | `growers/<grower>/farms/<farm>/fields/<field>/soil/ssurgo_soil_types.geojson` |
| Soil attributes | `growers/<grower>/farms/<farm>/derived/tables/<farm>_fields_soil.csv` |
| Field soil summary | `growers/<grower>/farms/<farm>/fields/<field>/soil/ssurgo_summary.csv` |
| Grower and farm names | `growers/<grower>/grower.json`, `farms/<farm>/farm.json` |

The polygon files carry `mukey` but no soil attributes, so the generator joins
each map unit to its dominant component using the per-field, per-`mukey` rows in
the farm soil table. If the vector cache is missing, that farm simply gets no
soil layer; the field popups keep the attribute summary, and the legend omits the
drainage section.

## Verification checklist

1. `git status` clean after staging; generated HTML and manifests stay outside
   the skill checkout.
2. For each grower, confirm the manifest `field_count` equals the number of
   features in `boundary/field_boundaries.geojson`, and `soil_unit_count` is
   close to the count of distinct `mukey` values across the field soil vectors
   (slivers under 0.1 ac are intentionally dropped).
3. Check `fields_with_soil == field_count` for US farms.
4. Open the HTML and confirm the imagery tiles render, the layer control toggles
   soil, a field click shows grower/farm/soil rows, and the sidebar zooms.
5. `./scripts/validate.sh` from the repository root after doc changes.

## Related

- [Data pipeline overview](../README.md)
- [SSURGO soil workflow](../../soil/ssurgo-soil/GUIDE.md)
- [Basemap reference](../references/basemap.md)
- Generic single-file builder: [interactive-web-map](../../admin/interactive-web-map/GUIDE.md)
