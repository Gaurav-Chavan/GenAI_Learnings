# Start here — hand the project to Claude Code

This is a documentation/prompt starter pack, not a working application. It does not contain your CSV, reviewed coordinates, app code, an installed environment, or locally executed tests.

## Place the files

Extract this pack's contents into the project folder you open in VS Code. Put your existing `ncr_ride_bookings.csv` beside `CLAUDE.md` and `PROJECT_BRIEF.md`. Preserve the `docs` subfolder. Review/merge rather than overwrite existing project instructions.

```text
ncr-ridepulse/
  ncr_ride_bookings.csv          # you supply this
  CLAUDE.md
  PROJECT_BRIEF.md
  BUILD_PROMPTS.md
  START_HERE.md
  docs/
    DEVLOG.md
    PRIOR_DATA_AUDIT.md
```

`CLAUDE.md` provides recurring development rules. `PROJECT_BRIEF.md` contains the complete three-tab design, metric definitions, map requirements, and acceptance checks. `BUILD_PROMPTS.md` provides staged tasks. The prior audit is reference material to reproduce against your local CSV, not proof that your local file or environment was tested.

## Initialize uv for a NEW project

Open the integrated terminal in the project root. These commands choose Python 3.12 as a proposed project baseline:

```text
uv --version
uv init --bare --python 3.12
uv python pin 3.12
uv add streamlit pandas plotly pydeck
uv add --dev pytest ruff
```

Do not run `uv init` in an already-initialized project. If `pyproject.toml` or an established Python configuration already exists, ask Claude to inspect it before making changes. Stop on any command error rather than continuing blindly. Network access may be needed to obtain the interpreter and dependencies.

Use `uv run` for project commands; manual environment activation is unnecessary for those commands. Commit the generated uv.lock with code later; do not commit the .venv directory. Do not push or publish anything yet.

## Start Claude in Plan mode

Open the Claude Code extension panel and choose Plan using the permission-mode selector. Paste Prompt 0 from BUILD_PROMPTS.md. Review its understanding and proposed work before allowing edits.

Use the extension's file-reference autocomplete for `@CLAUDE.md` and `@PROJECT_BRIEF.md`. Refer to the CSV by filesystem path and have Claude profile it programmatically; do not paste its full contents into chat.

After accepting the plan, approve Phase 1 only and send Prompt 1. Use manual permission review while starting out. Do not treat CLAUDE.md as a security boundary or turn off permission checks to force commands through.

## Work one phase at a time

Review the preparation report and actual test results before beginning the geographic pilot. The pilot is the first UI milestone because the connection map is the key feature. A lookup template with blank coordinates is not a completed map.

For later phases, use the corresponding prompts from BUILD_PROMPTS.md. Keep the development log current. Starting a new Claude conversation should begin with the brief, rules, and the latest log, not a guess about unfinished work.

After the relevant files are implemented, intended commands are:

```text
uv run python preprocessing.py --input ncr_ride_bookings.csv
uv run python -m pytest -q
uv run ruff check .
uv run streamlit run app.py
```

Do not run the last command until app.py exists. Open the local address printed by Streamlit and perform the supplied browser checks. A passing Python test suite does not establish that map tiles or click interactions work.

## Key reminders

Preserve the raw CSV, do not drop repeated Booking IDs, do not zero-fill missing measures, and never fabricate geographic coordinates. Report unmapped coverage explicitly. All baseline values must be recomputed from your actual local data.

The assignment documentation should record your real prompts, iterations, screenshots, and learnings. Prepare a live demo video of five minutes or less and the codebase link; no completed submission materials are implied by this starter pack.
