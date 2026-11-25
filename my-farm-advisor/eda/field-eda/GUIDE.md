---
name: field-eda
description: Field-level exploratory analysis that compares field boundaries, CDL crop history, and growing-season weather across fields, within growers, and across growers. Produces static tables, PNG figures, and a manifest from local runtime data.
version: 1.0.0
author: Boreal Bytes
tags: [eda, fields, boundaries, cdl, weather, rotation, static-plots]
---

# Workflow: field-eda

## Description

Compare three things that all describe the same 30 fields but at different levels:
field geometry (boundaries), cropping practice (CDL), and environment (weather).
The workflow answers a single question: **are these fields one comparable population, or
several distinct operating contexts?**

It writes tidy tables first, then a small set of static PNGs where every figure has an
explicit job. It never writes an interactive dashboard and never writes report prose.

## When to Use This Workflow

- You have a runtime with one or more growers, each with a farm, field boundaries,
  CDL year tables, and a multi-year weather table.
- You need to compare fields within a grower, growers against each other, or one field
  against its own history.
- You need reviewable static evidence before writing a one-time report.
- You do **not** need yield, soil, irrigation, or satellite analysis. Those are out of scope.

## Story This Workflow Is Built Around

Three corn-soybean operating contexts, not 30 replicates:

| Context | Rotation behavior | Environment |
|---|---|---|
| Iowa | near-strict corn/soy alternation | coolest, wettest recent seasons |
| Illinois | mostly alternation, some continuous corn | intermediate |
| Nebraska | corn-heavy with repeated corn years | hottest, driest, most heat days |

The workflow is written so that a different dataset can still run; the story above is the
interpretation of *this* runtime, not a hard-coded expectation. Tables carry the evidence;
figures state the comparison, not a causal claim.

## Why Weather Is Part of the Story

Weather is not a separate report. It does three jobs the other categories need:

1. **Context.** Rotation differences co-occur with a heat/water gradient. The workflow
   quantifies that gradient instead of assuming the growers share a climate.
2. **Crop comparison.** Corn and soybean are compared within fields that grew both, so W3
   can show whether crop sorts the climate or location does.
3. **Independence limit.** Fields sample a small number of weather grid cells. Fields in
   the same cell have identical daily series, so field-level weather differences are not
   30 independent observations. W1 and the cell-level tables make that explicit.

## Quick Start

```bash
export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
cd my-farm-advisor/eda/field-eda/src
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_field_eda.py \
  --out "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-eda"
```

Review order:

```bash
# 1. Confirm discovery and planned outputs without writing anything
run_field_eda.py --dry-run

# 2. Build one category while iterating
run_field_eda.py --category weather

# 3. Full run
run_field_eda.py --category all
```

`--category` accepts `all`, `boundaries`, `cdl`, or `weather`. `--tables-only` skips figures.
A `weather` run also reads the CDL tables, because W3 joins the crop label to weather.

## Inputs

Read from `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/`:

- `boundary/field_boundaries.geojson` — one polygon per field, EPSG:4326, with `field_id`,
  `state`, `county`.
- `derived/tables/*_weather_<start>_<end>.csv` — daily field weather. Expected columns:
  `field_id, lat, lon, date, T2M, T2M_MAX, T2M_MIN, PRECTOTCORR, ALLSKY_SFC_SW_DWN, RH2M, WS10M`.
- `derived/tables/*_<year>_cdl.csv` — one row per field × CDL class × year with
  `field_id, year, crop_code, crop_name, pixel_count, pct`.
- `farm.json` — display name and metadata, used only for labels.

No network access is used and no downloader is invoked.

## Outputs

Everything is written under `--out`, which must be inside the runtime data root.

```
<out>/
  tables/
    field_summary.csv          # geometry, size, shape, CDL pixel-area check
    cdl_field_year.csv         # class-group shares and dominant crop per field-year
    rotation_summary.csv       # sequence, transitions, change rate, diversity
    weather_field_year.csv     # primary season + sensitivity columns per field-year
    weather_cell_year.csv      # de-duplicated weather per grid cell-year
    crop_weather.csv           # field-year weather joined to the CDL dominant crop
    weather_qc.csv             # per field-year completeness and range checks
    weather_sensitivity.csv    # primary vs alternate season/GDD definitions, with ranks
    grower_profile.csv         # one row per grower: the report handoff table
  figures/
    b1_field_map_and_acreage.png
    b2_area_vs_compactness.png
    b3_polygon_vs_cdl_area.png
    c1_crop_sequence_heatmap.png
    c2_crop_mix_by_grower_year.png
    c3_transition_matrix.png
    w1_within_field_precip_anomaly.png
    w2_climate_space.png
    w3_crop_weather_exposure.png
  manifests/field_eda_manifest.json
```

`grower_profile.csv` is the single table a report can quote. Three contexts are clearer as
three rows than as a three-point scatter. It carries size, compactness, CDL area deviation,
rotation rates, crop shares, and cell-weighted weather means. `crop_weather.csv` carries the
field-year crop label and exposure used by W3. `weather_sensitivity.csv` is supplementary:
it shows whether a seasonal ranking is stable across alternate definitions and is not plotted.

## Figure Contract

| ID | File | Level | Job |
|---|---|---|---|
| B1 | `b1_field_map_and_acreage.png` | across fields/growers | Show that each "farm" is 10 scattered county blocks, not one contiguous parcel; show size range |
| B2 | `b2_area_vs_compactness.png` | across fields | Test whether fields are geometrically interchangeable |
| B3 | `b3_polygon_vs_cdl_area.png` | comparison | Validate that CDL pixel area agrees with polygon area, so composition denominators are credible |
| C1 | `c1_crop_sequence_heatmap.png` | within field / across fields | Show each field's observed crop sequence and rotation archetypes |
| C2 | `c2_crop_mix_by_grower_year.png` | across fields/growers | Show whole-grower crop mix and whether rotations are synchronized |
| C3 | `c3_transition_matrix.png` | comparison | Quantify rotation discipline with observed transitions |
| W1 | `w1_within_field_precip_anomaly.png` | within field | Show each field's wet/dry sequence and expose shared cells |
| W2 | `w2_climate_space.png` | across growers | Place the three growers in heat/water space at the honest unit (cell-year) |
| W3 | `w3_crop_weather_exposure.png` | within field / across growers | Test whether corn and soybean sit in different temperature/water space, or whether location separates them; pair crops only within fields that grew both |

If a figure does not earn its place, revise or drop it before adding prose.

## Assignment Coverage

The nine artifacts satisfy the assignment's per-category counts without a tenth figure.

| Category | Statistical visualization 1 | Statistical visualization 2 | Comparison / correlation |
|---|---|---|---|
| Boundaries | B1 acreage strip | B2 area vs compactness | B3 polygon vs CDL area |
| CDL | C1 crop sequence | C3 transition matrix | C2 crop mix by grower-year |
| Weather | W1 within-field anomaly | W3 crop-weather exposure | W2 grower climate space |

| Requirement | Artifact |
|---|---|
| Geospatial map | B1, three grower panels on local equal-area axes |
| Field level | B2, B3, C1, W1, W3 |
| Field-year level | C1, W1, W3 |
| Grower level | B1, C2, W2 |
| Across-grower level | B1, C2, W2, W3 |

B1 carries both the map and the acreage distribution, so it appears in two rows. W3 supports
the crop-weather synthesis in addition to its statistical-visualization slot; the paired
differences are read from `crop_weather.csv`, not counted as a third comparison.

## Definitions

- **Primary season:** May 1 – September 30 (configurable with `--season-start`/`--season-end`).
- **GDD:** `max(min((T2M_MAX + T2M_MIN) / 2, cap) - base, 0)` summed daily,
  base 10 °C, cap 30 °C, in °C-days.
- **Season precipitation:** sum of `PRECTOTCORR` in mm.
- **Heat days:** days with `T2M_MAX >= 35 °C`.
- **Rain day:** `PRECTOTCORR >= 1 mm`; **heavy-rain day:** `>= 25 mm`.
- **Sensitivity (columns only, not plots):** April–October season, calendar-year
  precipitation, uncapped GDD.
- **Weather cell:** NASA POWER's native 0.5 deg latitude x 0.625 deg longitude grid cell,
  obtained by snapping each stored sample point to the grid centre. Fields in one cell share
  one daily series; the workflow verifies that cell count equals distinct series count.
- **Crop-weather pairing:** for each field that grew both crops, average the primary-season
  exposure over its corn years and over its soybean years, then subtract soybean from corn.
  The pair holds location still but not year, so it is descriptive, not a crop effect.
- **Grower summaries:** cell-weighted (mean over cells, then over cells), because one cell
  can hold many fields.
- **Dominant crop:** largest crop group excluding non-crop classes.
- **Compactness:** `4 * pi * area / perimeter^2` in an equal-area projection; 1 is a circle.

## Interpretation Guardrails

- This is a description of three cases drawn from 10 largest CSB fields per county, not a
  random sample. Do not present p-values as population inference.
- Weather is pseudo-replicated at the field level. Report the cell count alongside any
  field count.
- Seasonal precipitation ranking can flip with the season definition. Check the sensitivity
  columns before claiming one grower is wetter than another.
- CDL `pct` includes non-crop classes. Crop-only share must be computed by filtering, never
  inferred from a residual.
- Crop transitions are observed history, not a forecast. The workflow deliberately does not
  emit a predicted next crop.
- Field size x CDL crop diversity is deliberately not plotted. `crop_diversity` is 2 for 26 of
  30 fields and 1 for the other 4, and crop-group counts per field-year vary only 1-3, mostly
  through non-crop edges. A correlation would report noise, so the relationship is documented
  as unsupported rather than drawn.
- No soil, terrain, NDVI, irrigation, yield, or management data is used.

## Tests

```bash
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" -m unittest discover -s tests -v
```

Tests cover GDD base/cap behavior, season inclusion, leap years, percent-of-field-mean,
crop-weather join completeness, crop-label validation, and paired crop-difference
arithmetic.

## One-Time Report

Report assembly is outside this workflow contract. When a human needs a document, the example
consumer at `examples/analysis-report/run_report.py` reads the manifest, verifies all nine
figures and nine tables by SHA-256, and writes a Markdown report plus a copied artifact bundle
under that example's `output/`. It computes no new statistics. See
`examples/analysis-report/README.md` for the command.

## Out of Scope

Report prose, committed figures, new data downloads, dashboards, model fitting, causal
claims, yield analysis, and next-crop prediction. The one-time report is assembled later,
outside this subskill.
