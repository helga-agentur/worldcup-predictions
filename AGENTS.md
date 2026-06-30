# Project Instructions

Start with [`README.md`](README.md). It links to the canonical documentation in `docs/` for architecture, data sources, automation, diagnostics/improvement, advanced commands, and the generated plugin catalog. Do not duplicate those details here.

## Agent Rules

- Treat `README.md` and the linked `docs/` files as the source of truth for project behavior.
- Keep new documentation in the appropriate `docs/` file, then link to it from `README.md` when it is user-facing.
- Do not add new CLI flags, config switches, workflow options, or source integrations speculatively. If a control might be useful but is not clearly needed for the active workflow, ask first.
- Backtests and model calibration are evidence only. Do not silently rewrite runtime defaults; promote model/config changes only through deliberate code changes.
- Use `.codex/skills/worldcup-calibration-review/SKILL.md` when reviewing diagnostics, confirmed-score calibration evidence, source behavior, plugin logic, provider points, or model-default changes.
- Keep `.env` files ignored and never stage or commit secrets.
