# Advanced Commands

This page collects operational, evaluation, and maintenance commands beyond the basic install path in the README.

Commands are shown without Docker first. To run them through Docker, prefix with:

```bash
docker compose run --rm predictions
```

Example:

```bash
docker compose run --rm predictions worldcup-predictions backtest
```

## Plugin And Documentation Commands

List registered plugins:

```bash
worldcup-predictions plugins
```

Regenerate the plugin catalog:

```bash
worldcup-predictions docs-plugins
```

Write standard Markdown reports:

```bash
worldcup-predictions reports
```

## Prediction Commands

Print the next predictions:

```bash
worldcup-predictions predict --limit 4
```

Run the standard source/signal/prediction/provider-tip workflow:

```bash
worldcup-predictions workflow --limit 4
```

Run the hourly-style full update:

```bash
worldcup-predictions scheduled-update
```

Export one comparison-friendly JSON file with predictions, score matrices, provider tips, diagnostics, signal impacts, and run summaries:

```bash
worldcup-predictions export-predictions
```

## Static Site Commands

Build the static site from the published prediction ledger:

```bash
worldcup-predictions site-build
```

Serve it locally:

```bash
worldcup-predictions site-serve --host 127.0.0.1 --port 8000
```

With Docker and port publishing:

```bash
docker compose run --rm --service-ports predictions worldcup-predictions site-serve --host 0.0.0.0 --port 8000
```

## Backtesting And Calibration

Backtest current SRF predictions on finished fixtures:

```bash
worldcup-predictions backtest
```

Evaluate transparent model candidates on historical World Cups:

```bash
worldcup-predictions calibrate-model
```

Calibration reports evidence only. They do not silently rewrite runtime defaults.

Audit frozen pre-match predictions against final scores:

```bash
worldcup-predictions audit-predictions
```

## Snapshots And Refactor Safety

Create a prediction regression snapshot:

```bash
worldcup-predictions snapshot-predictions before-refactor --limit 64
```

Compare two snapshots:

```bash
worldcup-predictions compare-snapshots before-refactor after-refactor
```

Create a refactor-safety baseline bundle:

```bash
worldcup-predictions baseline-bundle before-refactor
```

Baseline bundles include prediction exports, reports, plugin metadata, source ledger export, dataset fingerprints, and metadata.

## Postmatch And Provider Scoring

Build postmatch learning and review queue rows:

```bash
worldcup-predictions postmatch-learning
```

Score virtual optimized tips for a provider:

```bash
worldcup-predictions provider-points srf.ch
worldcup-predictions provider-points 20min.ch
```

Track provider bonus answers and virtual match-tip points:

```bash
worldcup-predictions bonus-tracker srf.ch
worldcup-predictions bonus-tracker 20min.ch
```

These are virtual scoring commands: they answer how many points the model would have if its optimized tips had been entered.

## Entity Maintenance

Validate stored team labels against the canonical registry:

```bash
worldcup-predictions validate-entities
```

Generate entity alias candidates from stored structured data:

```bash
worldcup-predictions generate-entity-aliases
```

The daily simulation/maintenance cadence should keep entity maintenance fresh for normal server operation.

## Tournament Simulation

Run the standard 20,000-iteration tournament simulation:

```bash
worldcup-predictions simulate-tournament
```

By default this starts from the current stored tournament state: confirmed scores are fixed and only unresolved matches are sampled.

Run the same simulation as if no tournament match had been played yet:

```bash
worldcup-predictions simulate-tournament --from-day-one
```

This ignores stored final scores and avoids using current tournament results as model-history inputs. Both modes use the same simulator; only the prepared input state differs.

## Tests

Run the test suite:

```bash
python -m unittest discover -s tests
```

With Docker:

```bash
docker compose run --rm predictions python -m unittest discover -s tests
```
