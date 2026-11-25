# field-eda examples

This workflow runs against a local runtime bundle; no example data is committed here
because field boundaries, weather tables, and CDL tables are generated assets.

## What a runtime needs

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/
  <grower>/
    farms/
      <farm>/
        farm.json
        boundary/field_boundaries.geojson
        derived/tables/*_weather_<start>_<end>.csv
        derived/tables/*_<year>_cdl.csv
```

At least one grower/farm is required. Each farm must have boundaries, one weather table, and
at least two CDL years for rotation analysis.

## Dry run

```bash
export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
cd my-farm-advisor/eda/field-eda/src
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_field_eda.py \
  --dry-run \
  --out "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-eda"
```

The dry run prints discovered growers, farms, field counts, weather year range, weather
cells, CDL years, and the planned output paths. It writes nothing.

## Full run

```bash
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_field_eda.py \
  --out "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-eda"
```

Then open `tables/grower_profile.csv` and `figures/`. Start with W1 and C1: they carry the
within-field story that the cross-grower figures depend on.

## What the run produces

Nine figures (three per category) and nine tables, plus a manifest:

```
<out>/
  figures/
    b1_field_map_and_acreage.png        # map + acreage strip (geospatial map)
    b2_area_vs_compactness.png          # boundaries: size vs shape
    b3_polygon_vs_cdl_area.png          # boundaries: area parity check
    c1_crop_sequence_heatmap.png        # CDL: observed crop sequence
    c2_crop_mix_by_grower_year.png      # CDL: crop mix and synchrony
    c3_transition_matrix.png            # CDL: observed transitions
    w1_within_field_precip_anomaly.png  # weather: within-field wet/dry
    w2_climate_space.png                # weather: grower climate space
    w3_crop_weather_exposure.png        # weather: corn vs soybean exposure
  tables/
    field_summary.csv
    cdl_field_year.csv
    rotation_summary.csv
    weather_field_year.csv
    weather_cell_year.csv
    crop_weather.csv
    weather_qc.csv
    weather_sensitivity.csv
    grower_profile.csv
  manifests/field_eda_manifest.json
```

The manifest records each output's category, size, and SHA-256 so a downstream consumer can
refuse stale artifacts.

## One-time report example

`analysis-report/` is a downstream consumer, not part of the reusable workflow. It verifies the
manifest and nine-plus-nine artifact count, then writes a portable Markdown report bundle under
`analysis-report/output/`. Run it after the workflow:

```bash
cd my-farm-advisor/eda/field-eda/examples/analysis-report
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_report.py
```

