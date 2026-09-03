# CLAUDE.md

Project instructions for Claude Code. **Read `AGENT.md` first** — it holds the invariants, the
rule-or-LLM policy, and the layout. This file covers working conventions only.

## Before you change anything

1. Read `AGENT.md`. The six invariants are architectural and each has a test that fails the
   build. If a request conflicts with one, **stop and raise it** rather than working around it.
2. Run `make check` (lint + tests). It must be green before you start, so you know what you
   broke.

## While you work

- **Plain `venv` + `pip`.** No uv, no poetry. `make setup` if the venv is missing.
- **`ruff` and `mypy` clean at every phase**, not "mostly clean". `make lint` also runs `tsc`.
- **Clinical content goes in YAML**, never in a `.py` file. See the content table in `AGENT.md`.
- **One component per file** in the frontend.
- **An ADR for every non-obvious decision**, in `docs/adr/`, in the style of ADR-0002.
- **Tests before the phase boundary**, not after. A phase is not done when the code runs; it is
  done when a test would catch it breaking.

## Commit messages

Commit at every phase boundary. In the message, say **whether you reached for a rule or the
LLM, and why** — the brief asks for this explicitly and it is the most useful thing in the
history when revisiting a decision. Record bugs the tests or the eval harness caught, and
whether the gold script or the system turned out to be wrong.

End with:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

## Reporting results

Report what the numbers actually say. `docs/EVALUATION.md` reports the development set *and*
the held-out set precisely because the first number alone is misleading; keep it that way. If
something is mocked, unverified, or measured on synthetic data, say so in the same breath as
the number — `/about` and `UPDATE.md` both do this deliberately.

The eval harness (`eval/`, 50 gold + 12 held-out scripts, the runner and the OCR benchmark)
was removed in the UI-rewrite strip. **`eval/ocr_bench.py` has since been restored** from the
`Baseline` commit and re-run (2026-09-03) to choose between the OCR engines; it works, and its
section of `docs/EVALUATION.md` is current. Everything else there — dialogue, red flags,
summaries — is still a **historical record** whose commands will not run. If extraction
quality is measured again, restore the rest of the harness from `Baseline` rather than writing
a new one, and keep its standing rule: do not fix a held-out miss to improve the held-out
score.

## Things that will bite you

- **`.env` is gitignored and holds a real Groq key.** Never commit it, never print it.
- **Model names drift.** `llama-3.3-70b-versatile` was decommissioned mid-build. Check
  `GET /openai/v1/models` before changing `groq_model`.
- **`ruff format` will reflow the whole tree** if run on a codebase it has not formatted.
  That is fine, but do it in its own commit.
- **Three tests are dormant, not deleted.** `test_contrast_tokens.py`,
  `test_colour_vision_deficiency.py` and the crop-geometry assertion in `test_bbox_geometry.py`
  skip themselves while the frontend is a blank shell, and **re-arm automatically** the moment
  `frontend/src/design/theme.css` and `frontend/src/kiosk/SourceCrop.tsx` exist again. Read
  their docstrings before writing those files — they name the exact bugs they caught.
