# Local Instructions

## Purpose

This folder owns the reusable field-level EDA workflow: boundary, CDL, and growing-season
weather comparisons written as tables and static figures.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly asks for a
broader skill change. Do not change parent `SKILL.md`, sibling EDA workflows, or root policy
from a subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first. If routing context is needed, read `../INDEX.md` and `../../SKILL.md`.
For data conventions, read `../../data-pipeline/README.md` and the weather/CDL guides under
`../../weather/` and `../../soil/cdl-cropland/`.

## Data boundary

The workflow reads runtime data under `${DATA_PIPELINE_DATA_ROOT}` and writes only under the
`--out` directory, which is validated to be inside the runtime data root. Never commit
runtime tables, figures, or downloaded assets.

## Interpretation boundary

Keep outputs descriptive. Do not add significance tests presented as inference, causal
language, or a predicted next crop. Weather must stay tied to the rotation/site story and
must report the shared-grid-cell limitation.

## Local validation

Run `./scripts/validate.sh` from the repository root after structural changes. Then run the
unit tests and a `--dry-run` against the runtime:

```bash
export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" -m unittest discover -s tests -v
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" run_field_eda.py --dry-run
```

Inspect every regenerated PNG against the five-point vision review in
`../../../CONTRIBUTING.md` before treating a figure as done.

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or root files.
Do not duplicate root-wide asset, vendor, or validation policy here except this pointer to
`../../../AGENTS.md`.
