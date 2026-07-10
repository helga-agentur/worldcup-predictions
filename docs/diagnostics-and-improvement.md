# Diagnostics And Improvement

The prediction workflow is evidence-first. It should be possible to explain why a prediction moved, which plugin moved it, which source rows were missing or rejected, and whether a proposed model change is justified.

## Diagnostic Surfaces

Key structured datasets:

- `plugin_run_diagnostics`: one core-recorded row per plugin event, including payload summary, output counts, emitted artifacts/signals, fixture keys, timings, diagnostic levels, and plugin metadata.
- `plugin_event_outputs`: one core-recorded append-only row per detailed plugin output, including signals, artifacts, predictions, optimized tips, and diagnostics.
- `prediction_debug_report`: one row per predicted fixture with probabilities, expected goals, signal coverage, missing signal sources, provider tips, and model adjustment metadata.
- `prediction_signal_impacts`: per-fixture signal rows joined to the resulting prediction and signal adjustment metadata.
- `source_ledger`: API/source request decisions, including calls made, calls skipped, rate limits, quota remaining, and next safe fetch times.
- `source_diagnostics`: source/plugin diagnostics for skips, errors, and zero-row explanations.
- `extraction_diagnostics`: accepted/rejected extraction rows with source, extractor, status, reason, severity, URL/title, and metadata.
- `calibration_decisions`: report-only model/calibration recommendations. These rows must not silently rewrite runtime defaults.
- `provider_points`: virtual provider point totals for optimized tips.
- `postmatch_learning` and `postmatch_review_queue`: postmatch analysis surfaces for misses and review priorities.

Key Markdown reports:

- `reports/diagnostics-completeness.md`
- `reports/prediction-debug.md`
- `reports/source-ledger.md`
- `reports/extraction-diagnostics.md`
- `reports/provider-points.md`
- `reports/prediction-audit.md`
- `reports/bonus-tracker.md`

Generate reports:

```bash
worldcup-predictions reports
```

With Docker:

```bash
docker compose run --rm predictions worldcup-predictions reports
```

## Diagnostics Completeness Audit

The diagnostics completeness audit is the first gate before changing model weights or plugin behavior.

It checks:

- every plugin has enough metadata to explain its role
- API-backed plugins declare quota policy and source-ledger requirements
- plugins that ran emitted core run diagnostics
- prediction, optimized-tip, signal-impact, and debug rows contain fields needed for later analysis

If this report shows missing evidence, fix diagnostics first. Tuning from incomplete evidence is usually worse than waiting.

## Daily Improvement Loop

1. Run or inspect the latest `scheduled-update`.
2. Open `reports/diagnostics-completeness.md`.
3. If diagnostic coverage is incomplete, fix instrumentation or plugin metadata first.
4. Inspect `reports/source-ledger.md` for failing, skipped, or rate-limited sources.
5. Inspect `reports/extraction-diagnostics.md` for repeated extraction rejection reasons.
6. Inspect `reports/provider-points.md` and `prediction_audit` rows for provider-score movement.
7. Inspect `prediction_signal_impacts` and `prediction_debug_report` to see which signals moved each prediction.
8. Inspect `calibration_decisions`, `live_global_calibration`, and `team_calibration` for stable recommendations.
9. Decide one of:
   - no promotion
   - watch
   - fix diagnostics/source/plugin behavior
   - deliberately promote a small model/config change

## Promotion Rules

Backtests and calibration reports are evidence only. They must not silently rewrite runtime defaults.

Promoting a model or signal change should be a deliberate code/config change, usually in:

- `src/worldcup_predictions/core/constants.py`
- `src/worldcup_predictions/model/contracts.py`
- a plugin-specific implementation file when the evidence points to extraction/logic rather than global weights

Before changing runtime defaults, state:

- current value
- proposed value
- evidence
- expected effect
- residual risk

## Project Skill

Codex agents should use:

```text
.codex/skills/worldcup-calibration-review/SKILL.md
```

Use it when reviewing diagnostics, confirmed-score calibration evidence, source behavior, plugin logic, provider points, or model defaults.

## Common Improvement Types

Diagnostics fix:

- add missing metadata
- log missing input/output fields
- write clearer extraction rejection reasons

Source fix:

- repair an extractor
- improve source-ledger handling
- adjust robots-aware discovery
- handle rate-limit headers more accurately
- add or tighten a shared quota scope when one provider-level block is causing repeated sibling requests

Plugin logic fix:

- cap an overstrong signal
- correct signal direction
- remove duplicated influence
- improve confidence calculation

Model/default promotion:

- only after enough confirmed-match evidence
- avoid overreacting to red cards, weather interruptions, or one-off blowouts
- prefer small bounded changes

## Runtime Observability

Long commands log timestamped phase lines to stdout (captured in the cron log), e.g. `phase=site_build duration=42.1s rss=1250MB (+180MB)`. The same entries are stored per run in `prediction_run_summaries` under `maintenance.phase_timings`, together with `maintenance.peak_rss_mb` and the number of Parquet datasets flushed at the end of the run.

Per-plugin rows in `plugin_run_diagnostics` carry `duration_ms`, `rss_mb_after`, and `rss_mb_delta`, so a plugin that starts leaking memory or slowing down is visible per event without host-level tooling. When investigating a slow or killed run, start with the phase lines in `logs/scheduled-update.log`, then drill into `plugin_run_diagnostics` for the workflow portion.
