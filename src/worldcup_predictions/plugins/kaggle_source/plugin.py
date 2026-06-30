"""Optional Kaggle dataset discovery and structured extraction plugin."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from worldcup_predictions.core.constants import (
    ENDPOINT_KAGGLE_DATASETS_DOWNLOAD,
    ENDPOINT_KAGGLE_DATASETS_LIST,
    ENV_KAGGLE_API_TOKEN,
    KAGGLE_DATASET_SEARCHES,
    KAGGLE_SELECTED_DATASETS,
    SOURCE_KAGGLE,
)
from worldcup_predictions.core.contracts import Artifact, Diagnostic
from worldcup_predictions.core.datasets import KAGGLE_DATASETS, SQUAD_PLAYERS
from worldcup_predictions.core.events import EventName, event_value
from worldcup_predictions.core.metadata import EnvVar, PluginKind, PluginMetadata, QuotaPolicy
from worldcup_predictions.core.plugin import BasePlugin, PluginResult
from worldcup_predictions.plugins.player_impact.imports import match_transfermarkt_zip_to_squad_rows
from worldcup_predictions.plugins.source_runtime import SourceRuntime
from worldcup_predictions.storage.ledger import SourceRequest, stable_hash


class KaggleSourcePlugin(BasePlugin):
    """Discover optional Kaggle datasets and import selected structured facts."""

    id = "kaggle_source"
    version = "0.1.0"
    priority = 125
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SOURCE,
        description="Optionally query Kaggle and extract selected squad-value facts without storing raw archives.",
        datasets_read=(KAGGLE_DATASETS, SQUAD_PLAYERS),
        datasets_written=(KAGGLE_DATASETS, SQUAD_PLAYERS),
        env_vars=(EnvVar(ENV_KAGGLE_API_TOKEN, required=False, description="Kaggle API token with dataset read access."),),
        quota_policy=QuotaPolicy(
            quota_limited=True,
            ledger_required=True,
            description="Dataset searches refresh weekly; selected downloads refresh daily and store extracted facts only.",
        ),
        confidence_policy="Kaggle facts are supplemental and must be joined through canonical country/player rows before model use.",
    )

    def handle(self, event, context, payload):
        runtime = SourceRuntime(self, event, context)
        if context.storage is None:
            return runtime.storage_unavailable_result("Kaggle source")
        token = runtime.env_value(ENV_KAGGLE_API_TOKEN)
        if not token:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "info",
                        f"{ENV_KAGGLE_API_TOKEN} is not configured; Kaggle discovery/import is skipped.",
                    )
                ],
                artifacts=[runtime.structured_artifact(KAGGLE_DATASETS), runtime.structured_artifact(SQUAD_PLAYERS)],
            )

        diagnostics: list[Diagnostic] = []
        search_rows = []
        for slug, query in KAGGLE_DATASET_SEARCHES.items():
            result = self._search(runtime, slug=slug, query=query)
            diagnostics.extend(result.diagnostics)
            search_rows.extend(result.metadata.get("rows") or [])
        search_count = runtime.write_records(KAGGLE_DATASETS, search_rows)
        imported_players = 0
        for dataset_ref in KAGGLE_SELECTED_DATASETS:
            result = self._download_selected(runtime, dataset_ref=dataset_ref)
            diagnostics.extend(result.diagnostics)
            imported_players += int(result.metadata.get("players") or 0)
        return PluginResult(
            plugin_id=self.id,
            event=event_value(event),
            artifacts=[
                Artifact(KAGGLE_DATASETS, "structured_dataset", self.id, data={"rows": search_count}),
                Artifact(SQUAD_PLAYERS, "structured_dataset", self.id, data={"rows": imported_players}),
            ],
            diagnostics=diagnostics,
            metadata={"dataset_rows": search_count, "players": imported_players},
        )

    def _search(self, runtime: SourceRuntime, *, slug: str, query: str) -> PluginResult:
        token = runtime.env_value(ENV_KAGGLE_API_TOKEN)
        if not token:
            return runtime.result(metadata={"rows": []})
        request = SourceRequest(
            source=SOURCE_KAGGLE,
            endpoint=ENDPOINT_KAGGLE_DATASETS_LIST,
            purpose="dataset_search",
            params={"search": query, "pageSize": 10},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(days=7),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Kaggle dataset search", decision.reason, metadata=decision.metadata)
        try:
            payload, _headers = runtime.fetch_json(
                ENDPOINT_KAGGLE_DATASETS_LIST,
                {"search": query, "pageSize": 10},
                headers={"Authorization": f"Bearer {token}"},
            )
        except (OSError, TimeoutError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "Kaggle dataset search failed.", metadata={"query": query, "error": str(exc)})])
        rows = kaggle_dataset_rows(payload if isinstance(payload, list) else [], slug=slug, query=query)
        runtime.record_success(request, message="Fetched Kaggle dataset search.", metadata={"query": query, "rows": len(rows)})
        return runtime.result(metadata={"rows": rows})

    def _download_selected(self, runtime: SourceRuntime, *, dataset_ref: str) -> PluginResult:
        token = runtime.env_value(ENV_KAGGLE_API_TOKEN)
        if not token:
            return runtime.result(metadata={"players": 0})
        squad_rows = runtime.storage.read_records(SQUAD_PLAYERS, latest_only=True)
        if not squad_rows:
            return runtime.result(
                diagnostics=[
                    runtime.diagnostic(
                        "info",
                        "Kaggle selected dataset download skipped because no squad-player rows exist to join yet.",
                        metadata={"dataset_ref": dataset_ref},
                    )
                ],
                metadata={"players": 0},
            )
        request = SourceRequest(
            source=SOURCE_KAGGLE,
            endpoint=f"{ENDPOINT_KAGGLE_DATASETS_DOWNLOAD}/{dataset_ref}",
            purpose="selected_dataset_download",
            params={"dataset_ref": dataset_ref},
            quota_cost=1,
            min_refresh_interval=dt.timedelta(days=1),
        )
        decision = runtime.should_fetch(request)
        if not decision.should_fetch:
            return runtime.skipped_fetch_result("Kaggle selected dataset", decision.reason, metadata=decision.metadata)
        zip_path: Path | None = None
        try:
            zip_path = _download_to_tempfile(f"{ENDPOINT_KAGGLE_DATASETS_DOWNLOAD}/{dataset_ref}")
            rows = match_transfermarkt_zip_to_squad_rows(zip_path, squad_rows)
        except (OSError, TimeoutError, urllib.error.HTTPError, ValueError) as exc:
            runtime.record_error(request, exc)
            return runtime.result(diagnostics=[runtime.diagnostic("warning", "Kaggle selected dataset download/import failed.", metadata={"dataset_ref": dataset_ref, "error": str(exc)})])
        finally:
            if zip_path is not None:
                try:
                    zip_path.unlink()
                except Exception:
                    pass
        count = runtime.write_records(SQUAD_PLAYERS, rows)
        runtime.record_success(request, message="Imported selected Kaggle dataset facts.", metadata={"dataset_ref": dataset_ref, "players": count})
        return runtime.result(metadata={"players": count})


def kaggle_dataset_rows(payload: list[dict[str, Any]], *, slug: str, query: str) -> list[dict[str, Any]]:
    rows = []
    for item in payload:
        dataset_ref = str(item.get("ref") or item.get("datasetRef") or item.get("url") or "")
        if not dataset_ref:
            continue
        rows.append(
            {
                "record_key": stable_hash({"slug": slug, "dataset": dataset_ref}),
                "dataset_ref": dataset_ref,
                "search_slug": slug,
                "search_query": query,
                "title": item.get("title") or item.get("subtitle"),
                "creator_name": item.get("creatorName") or item.get("ownerName"),
                "download_count": item.get("downloadCount"),
                "vote_count": item.get("voteCount"),
                "usability_rating": item.get("usabilityRating"),
                "last_updated": item.get("lastUpdated"),
                "license_name": item.get("licenseName"),
                "metadata": {
                    "description": item.get("description"),
                    "total_bytes": item.get("totalBytes"),
                    "url": item.get("url"),
                },
            }
        )
    return rows


def _download_to_tempfile(url: str) -> Path:
    token = os.environ.get(ENV_KAGGLE_API_TOKEN)
    if not token:
        raise OSError(f"{ENV_KAGGLE_API_TOKEN} is not configured.")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "worldcup-predictions/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310
        data = response.read()
    handle = tempfile.NamedTemporaryFile(prefix="worldcup-kaggle-", suffix=".zip", delete=False)
    try:
        handle.write(data)
        return Path(handle.name)
    finally:
        handle.close()
