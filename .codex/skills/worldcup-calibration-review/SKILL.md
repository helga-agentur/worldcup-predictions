---
name: worldcup-calibration-review
description: Use for World Cup prediction calibration and daily diagnostics review after scheduled runs or confirmed scores, especially when deciding whether evidence should improve model defaults, plugin logic, source handling, or provider optimizers. The skill gathers diagnostics, recommends bounded changes, and avoids silently overwriting hardcoded defaults without evidence.
---

# World Cup Calibration Review

Use this skill when the user asks whether calibration should change model weights/defaults, asks to review diagnostics after scheduled runs, asks to improve predictions from logged evidence, asks to review calibration after new final scores, or asks if live calibration recommendations should be promoted.

## Guardrails

- Do not automatically rewrite `src/worldcup_predictions/core/constants.py` or `src/worldcup_predictions/model/contracts.py`.
- Treat `calibration_decisions` as report-only evidence.
- Treat `diagnostics_completeness_audit` as the first gate. If it reports missing evidence for a plugin/core decision, fix diagnostic coverage before tuning weights from that area.
- Use only consensus-confirmed scores. Raw source observations below the result-confirmation threshold are diagnostics, not model-learning data.
- If recommending a model-default change, clearly state the current value, proposed value, evidence, expected effect, and residual risk before editing.
- If the user explicitly asks to improve the model/plugins/predictions, implement only bounded, evidence-backed changes. Prefer source extraction fixes, diagnostic coverage fixes, and plugin logic corrections before changing global weights.
- Do not add speculative CLI flags or workflow options.

## Evidence To Inspect

Prefer the latest structured datasets and generated reports from the project workflow:

- `data/structured/diagnostics_completeness_audit.parquet`
- `data/structured/result_update_audit.parquet`
- `data/structured/calibration_decisions.parquet`
- `data/structured/live_global_calibration.parquet`
- `data/structured/team_calibration.parquet`
- `data/structured/prediction_backtest.parquet`
- `data/structured/prediction_debug_report.parquet`
- `data/structured/prediction_signal_impacts.parquet`
- `data/structured/provider_points.parquet`
- `data/structured/source_diagnostics.parquet`
- `data/structured/extraction_diagnostics.parquet`
- `data/structured/prediction_run_summaries.parquet`
- latest files under `data/reports/`, `data/exports/`, and `data/baselines/` when present
- generated Markdown reports under `reports/`, especially:
  - `reports/diagnostics-completeness.md`
  - `reports/prediction-debug.md`
  - `reports/provider-points.md`
  - `reports/extraction-diagnostics.md`

If structured files are missing, run the normal project workflow instead of inventing evidence:

```bash
docker compose run --rm predictions worldcup-predictions scheduled-update
```

## Review Steps

1. Read `reports/diagnostics-completeness.md` or the latest `diagnostics_completeness_audit` rows. Categorize findings as:
   - missing plugin metadata
   - missing plugin-run diagnostics
   - missing dataset fields needed for later tuning
   - clean enough for model/plugin review
2. Confirm whether new scores reached consensus and triggered `results_updated`.
3. Read the latest `calibration_decisions` rows and identify changed recommendations.
4. Compare current runtime defaults:
   - `BaselineModelConfig` in `src/worldcup_predictions/model/contracts.py`
   - signal weights/caps in `src/worldcup_predictions/core/constants.py`
5. Check whether the evidence is stable:
   - sufficient confirmed-match sample size
   - no dominance by red-card/weather/interruption outliers
   - provider-point/backtest direction improved, not merely one match explained
   - source diagnostics are healthy enough to trust the sample
6. Identify improvement type:
   - **Diagnostics fix**: a plugin/core decision lacks enough inputs/outputs/why.
   - **Source fix**: a source repeatedly fails, rejects useful rows, or misses public data.
   - **Plugin logic fix**: a signal exists but is ignored, inverted, duplicated, or insufficiently bounded.
   - **Weight/default promotion**: only when evidence is stable and the expected effect is clear.
7. Report one of:
   - **No promotion**: keep defaults; continue gathering evidence.
   - **Watch**: recommendation changed, but sample/effect is too weak.
   - **Improve diagnostics/source/plugin**: implement a bounded non-weight improvement if the user asked for improvements.
   - **Promote manually**: propose a small bounded code/config change for model defaults.

## Output Shape

Keep the answer compact:

- **Decision**: no promotion, watch, improve diagnostics/source/plugin, or promote manually.
- **Evidence**: diagnostics-completeness state, confirmed-score count, latest recommendation, point/backtest/debug signals.
- **Why**: the main reason for the decision.
- **Changes made**: exact files changed when the user asked for implementation.
- **Next action**: exact code/config change only if promotion is justified.
