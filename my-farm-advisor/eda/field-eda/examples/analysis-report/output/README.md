<!-- Copyright 2026 Clayton Young (borealBytes / Superior Byte Works, LLC) -->
<!-- Licensed under the Apache License, Version 2.0. -->

# Field-EDA report bundle

Open [`field_eda_report.md`](field_eda_report.md) first. That is the report.

- `figures/` holds the nine PNGs the report embeds.
- `tables/` holds the nine CSVs the report cites.
- `artifact_index.csv` maps each figure and table to its category, level, and purpose.
- `field_eda_manifest.json` is provenance: definitions, QC, and SHA-256 hashes. Paths
  are relative to `$DATA_PIPELINE_DATA_ROOT`, so the bundle is readable without the
  author's machine.

Regenerate the bundle with `run_report.py`; see `../README.md`. Do not hand-edit these
files.
