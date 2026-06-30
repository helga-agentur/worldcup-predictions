# worldcup-predictions

[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/runtime-docker-2496ED.svg)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Abstract

This repository is a plugin-based data science project for FIFA World Cup 2026 score predictions and Tippspiel optimization. It combines historical international results, current tournament state, public fixtures and scores, betting-market signals, weather, public analysis, squad/player enrichment, live calibration, and provider-specific scoring rules into one reproducible prediction workflow.

The core model produces provider-neutral match forecasts: expected goals, home/draw/away probabilities, an exact-score probability matrix, and the most likely integer score. Provider optimizer plugins then translate those neutral forecasts into Tippspiel recommendations, currently for `srf.ch` and `20min.ch`.

The project is designed for automation and auditability. Source plugins store extracted structured facts in DuckDB/Parquet instead of raw response caches. Quota-limited APIs are protected by a source ledger, and prediction-impacting plugins emit diagnostics so model behavior can be reviewed and improved over time.

Created and maintained by [David Pacassi Torrico](https://github.com/dpacassi) at [Helga](https://github.com/helga-agentur).

## What It Does

- Runs an automated World Cup prediction workflow from data ingestion to published static pages.
- Keeps the football model provider-neutral, then applies separate optimizer plugins for Tippspiel rules.
- Stores structured facts, prediction ledgers, diagnostics, source health, and provider-point ledgers.
- Publishes a no-JavaScript-required static Webseite plus a JSON API.
- Supports hourly automation and daily tournament simulation through Docker-friendly CLI commands.

## Outputs

Each run can produce:

- Decimal expected scores and integer score tips for defined fixtures.
- Home/draw/away probabilities and exact-score probability matrices.
- SRF and 20min optimized tips plus virtual points ledgers.
- Source diagnostics, plugin influence reports, score-confirmation evidence, and calibration review data.
- Static HTML pages and JSON files for `tippspiel.helga.ch`-style publishing.

## Quick Start

Docker is the recommended setup. It keeps the Python runtime, dependencies, CLI, and local static-site server consistent across machines.

```bash
git clone git@github.com:helga-agentur/worldcup-predictions.git
cd worldcup-predictions
cp .env.example .env
docker compose build predictions
docker compose run --rm predictions worldcup-predictions plugins
docker compose run --rm predictions worldcup-predictions scheduled-update
docker compose run --rm predictions worldcup-predictions simulate-tournament
docker compose run --rm predictions worldcup-predictions reports
docker compose run --rm --service-ports predictions worldcup-predictions site-serve --host 0.0.0.0 --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/).

For API keys, data sources, automation, generated files, and operational details, use the documentation below.

## More Documentation

| Topic | Documentation |
| --- | --- |
| Architecture, plugin runtime, and model boundaries | [`docs/architecture.md`](docs/architecture.md) |
| APIs, datasets, scraping sources, and environment variables | [`docs/data-sources.md`](docs/data-sources.md) |
| Cron jobs, generated files, static site publishing, and cache policy | [`docs/automation.md`](docs/automation.md) |
| Diagnostics, calibration review, source health, and improvement workflow | [`docs/diagnostics-and-improvement.md`](docs/diagnostics-and-improvement.md) |
| CLI reference and less common maintenance commands | [`docs/advanced-commands.md`](docs/advanced-commands.md) |
| Generated built-in plugin catalog | [`docs/plugins.md`](docs/plugins.md) |
