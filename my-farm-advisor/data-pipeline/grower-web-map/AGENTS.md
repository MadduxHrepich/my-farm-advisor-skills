# Local Instructions

## Purpose

This folder documents the grower-level interactive web map built by
`../src/scripts/reporting/generate_grower_web_map.py`. It is a delivery layer over
runtime field boundaries and SSURGO vectors, not an ingest or analysis step.

## Safe edit scope

Edits should stay in this folder and the generator plus its path helpers unless
the user explicitly asks for a broader skill change. Do not change parent
`README.md`, sibling workflows, or root policy from a subskill task unless
explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first. For routing context read `../README.md` and
`../../soil/ssurgo-soil/GUIDE.md`. The older generic map builder under
`../../admin/interactive-web-map/` is reference only — do not modify it when
working here.

## Hard constraints

- The HTML must stay a single small file: no embedded imagery, rasters, or full
  data bundles. Only field and SSURGO vector GeoJSON plus CDN links.
- Never commit generated HTML or manifests; they live under
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/`.
- Keep the manifest fields stable (`field_count`, `soil_unit_count`,
  `fields_with_soil`, `soil_simplify_tolerance_deg`) — they are the run-time
  verification contract.

## Local validation

Run the generator from the runtime source copy for every grower with fields, then
`./scripts/validate.sh` from the repository root after structural changes. There
is no headless browser in the default environment, so verify structurally
(manifest counts, embedded payload, tile HTTP 200) and open the HTML manually.
