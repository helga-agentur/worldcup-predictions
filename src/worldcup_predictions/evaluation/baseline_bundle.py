"""Refactor-safety baseline bundle creation."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from worldcup_predictions.core.datasets import BASELINE_BUNDLES, DATASET_CONTRACTS
from worldcup_predictions.core.workflow import WorkflowRun
from worldcup_predictions.documentation import render_plugin_catalog
from worldcup_predictions.evaluation.prediction_export import write_prediction_export
from worldcup_predictions.evaluation.prediction_snapshots import write_prediction_snapshot
from worldcup_predictions.evaluation.reports import write_standard_reports
from worldcup_predictions.storage.ledger import stable_hash, utc_now


def create_baseline_bundle(
    *,
    project_root: Path,
    storage,
    run: WorkflowRun,
    plugins,
    baseline_id: str,
) -> dict[str, Any]:
    """Create a folder with all artifacts needed to compare future refactors."""

    bundle_dir = Path(project_root) / "data" / "baselines" / baseline_id
    bundle_dir.mkdir(parents=True, exist_ok=True)
    snapshot_id = f"{baseline_id}:snapshot"
    snapshot_rows = write_prediction_snapshot(
        storage,
        snapshot_id,
        run.predictions,
        run.optimized_tips,
        run_id=run.context.run_id,
    )
    reports = write_standard_reports(storage, Path(project_root), run_id=run.context.run_id)
    prediction_export = write_prediction_export(
        storage,
        bundle_dir / "predictions.json",
        export_id=baseline_id,
        run_id=run.context.run_id,
    )
    dataset_fingerprints = dataset_fingerprints_for_storage(storage)
    (bundle_dir / "dataset_fingerprints.json").write_text(
        json.dumps(dataset_fingerprints, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "plugins.md").write_text(render_plugin_catalog(plugins), encoding="utf-8")
    source_ledger_path = ""
    if hasattr(storage, "export_source_ledger"):
        exported = Path(storage.export_source_ledger())
        if exported.exists():
            source_ledger_path = str(bundle_dir / "source_ledger.parquet")
            shutil.copy2(exported, source_ledger_path)

    diagnostic_levels = Counter(diagnostic.level for diagnostic in run.diagnostics)
    metadata = {
        "baseline_id": baseline_id,
        "run_id": run.context.run_id,
        "created_at_utc": utc_now().isoformat(),
        "snapshot_id": snapshot_id,
        "snapshot_rows": snapshot_rows,
        "prediction_count": len(run.predictions),
        "optimized_tip_count": len(run.optimized_tips),
        "diagnostic_levels": dict(sorted(diagnostic_levels.items())),
        "prediction_export": prediction_export,
        "dataset_fingerprints": dataset_fingerprints,
        "reports": reports,
        "source_ledger_path": source_ledger_path,
        "plugin_count": len(tuple(plugins)),
    }
    (bundle_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "README.md").write_text(_bundle_readme(metadata), encoding="utf-8")
    manifest = {
        "record_key": stable_hash({"baseline_id": baseline_id, "path": str(bundle_dir)}),
        "baseline_id": baseline_id,
        "path": str(bundle_dir),
        "run_id": run.context.run_id,
        "snapshot_id": snapshot_id,
        "created_at_utc": metadata["created_at_utc"],
        "prediction_count": len(run.predictions),
        "optimized_tip_count": len(run.optimized_tips),
        "snapshot_rows": snapshot_rows,
        "dataset_count": len(dataset_fingerprints),
        "metadata": metadata,
    }
    storage.write_records(BASELINE_BUNDLES, [manifest], source="baseline_bundle", run_id=run.context.run_id)
    return manifest


def dataset_fingerprints_for_storage(storage) -> list[dict[str, Any]]:
    """Return stable content fingerprints for registered structured datasets."""

    fingerprints = []
    for dataset in sorted(DATASET_CONTRACTS):
        rows = storage.read_records(dataset)
        stripped = [_strip_record(row) for row in rows]
        observed_values = [
            str((row.get("_record") or {}).get("observed_at_utc") or "")
            for row in rows
            if row.get("_record")
        ]
        fingerprints.append(
            {
                "dataset": dataset,
                "row_count": len(rows),
                "latest_observed_at_utc": max(observed_values) if observed_values else "",
                "content_hash": stable_hash(stripped),
            }
        )
    return fingerprints


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_record"}


def _bundle_readme(metadata: dict[str, Any]) -> str:
    return (
        f"# Baseline Bundle {metadata['baseline_id']}\n\n"
        f"- Run id: `{metadata['run_id']}`\n"
        f"- Snapshot id: `{metadata['snapshot_id']}`\n"
        f"- Predictions: {metadata['prediction_count']}\n"
        f"- Optimized tips: {metadata['optimized_tip_count']}\n"
        f"- Snapshot rows: {metadata['snapshot_rows']}\n"
        "\nUse `metadata.json`, `dataset_fingerprints.json`, `plugins.md`, "
        "`predictions.json`, and `source_ledger.parquet` to compare future refactors.\n"
    )
