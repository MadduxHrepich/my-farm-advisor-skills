<!-- Copyright 2026 Clayton Young (borealBytes / Superior Byte Works, LLC) -->
<!-- Licensed under the Apache License, Version 2.0. -->

# Analysis Report

One-time report assembly for the field-level EDA. This is a downstream consumer of the
reusable `field-eda` workflow, not part of it. The workflow ends at tables, figures, and a
manifest; this example turns those into a single portable report bundle.

## Input -> Process -> Output

### Input

- `manifests/field_eda_manifest.json` and the `figures/` and `tables/` it records, from a
  completed `run_field_eda.py` run under `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-eda`.

### Process

1. Read the manifest and verify every recorded figure and table still exists and matches its
   SHA-256 hash. Fail loudly rather than cite a stale artifact.
2. Require exactly 9 figures and 9 tables.
3. Read the existing tables for the report numbers. No new statistics are computed.
4. Copy the figures and tables into the output bundle and write the Markdown report.

### Output

- `output/field_eda_report.md`
- `output/artifact_index.csv`
- `output/field_eda_manifest.json`
- `output/figures/` (9 PNGs)
- `output/tables/` (9 CSVs)

The `output/` bundle is a committed demonstration fixture; regenerate it rather than editing
it by hand. It is allowed by `ASSET_POLICY.md`, which permits generated example fixtures under
`examples/**/output/`.

## Run

```bash
export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
cd my-farm-advisor/eda/field-eda/examples/analysis-report
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_report.py
```

The workflow must be run first, so the manifest and artifacts exist:

```bash
export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
cd my-farm-advisor/eda/field-eda/src
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_field_eda.py \
  --out "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/eda/field-eda"
```

Use `--input` to point at a different workflow output directory, and `--output` to write the
bundle somewhere else.
