"""Command line interface for the new plugin workflow."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import Sequence

from worldcup_predictions import __version__
from worldcup_predictions.core.datasets import (
    MARKET_OUTRIGHTS,
    MODEL_CALIBRATION,
    PREDICTION_BACKTEST,
    PREDICTION_LEDGER,
    SIMULATION_RUNS,
    SIMULATION_SUMMARY,
)
from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.i18n import load_translation_catalog
from worldcup_predictions.core.plugin import PluginManager
from worldcup_predictions.core.workflow import PredictionWorkflow
from worldcup_predictions.documentation import render_plugin_catalog
from worldcup_predictions.entities.dynamic_aliases import build_generated_alias_rows
from worldcup_predictions.entities.validation import build_entity_validation_rows
from worldcup_predictions.evaluation import (
    BACKTEST_DATASET,
    backtest_historical,
    backtest_srf,
    knockout_backtest_summary,
    summarize_backtest_by,
    summarize_backtest_rows,
    write_provider_knockout_audit,
)
from worldcup_predictions.evaluation.automation_hooks import (
    ACTION_TRIGGER_CURRENT_STATE_SIMULATION,
    AutomationHook,
    run_automation_hooks,
)
from worldcup_predictions.evaluation.baseline_bundle import create_baseline_bundle
from worldcup_predictions.evaluation.model_calibration import calibrate_baseline_model, write_model_calibration
from worldcup_predictions.evaluation.audit import build_prediction_audit_rows
from worldcup_predictions.evaluation.diagnostics_completeness import write_diagnostics_completeness_audit
from worldcup_predictions.evaluation.bonus_tracker import build_bonus_tracker_rows
from worldcup_predictions.evaluation.data_hooks import run_data_update_hooks
from worldcup_predictions.evaluation.postmatch import write_postmatch_outputs
from worldcup_predictions.evaluation.prediction_export import write_prediction_export
from worldcup_predictions.evaluation.prediction_ledger import write_prediction_ledger
from worldcup_predictions.evaluation.prediction_snapshots import (
    compare_snapshots,
    comparison_summary,
    utc_label,
    write_prediction_snapshot,
)
from worldcup_predictions.evaluation.published_prediction_ledger import write_published_prediction_ledger
from worldcup_predictions.evaluation.provider_points import build_provider_points_rows
from worldcup_predictions.evaluation.reports import write_standard_reports
from worldcup_predictions.evaluation.scheduled_update import summarize_source_ledger_rows, write_prediction_run_summary
from worldcup_predictions.market_prior import team_strengths_from_outrights
from worldcup_predictions.model import BaselineModel, BaselineModelConfig, HistoricalResult, load_historical_results
from worldcup_predictions.model.baseline import compute_elo
from worldcup_predictions.plugins import builtin_plugins
from worldcup_predictions.plugins.providers.ch_srf import best_srf_bonus_answers, evaluate_srf_bonus_questions
from worldcup_predictions.plugins.providers.ch_20min import best_twenty_min_bonus_answers, evaluate_twenty_min_bonus_questions
from worldcup_predictions.simulations import SimulationInputs, TournamentSimulator, pair_key
from worldcup_predictions.simulations.worldcup_2026 import ROUND_NAMES
from worldcup_predictions.site import build_site, serve_site
from worldcup_predictions.site.generator import gtm_container_id_from_env
from worldcup_predictions.storage.ledger import stable_hash
from worldcup_predictions.tournament.repository import (
    load_results,
    load_tournament_state,
)
from worldcup_predictions.tournament import FixtureRecord, TeamRef


SIMULATION_LOGIC_VERSION = "outright-matrix-prior-v2"


def build_manager(project_root: Path | None = None) -> PluginManager:
    return PluginManager(list(builtin_plugins()))


def build_workflow(project_root: Path) -> PredictionWorkflow:
    return PredictionWorkflow.from_project_root(project_root=project_root, manager=build_manager(project_root))


def print_predictions_table(run, *, locale: str | None = None) -> None:
    translations = load_translation_catalog(locale)
    if not run.predictions:
        print(translations.translate("workflow.no_open_predictions"))
        return
    tips_by_fixture_provider = {
        (tip.fixture_key, tip.ruleset.provider): tip
        for tip in run.optimized_tips
    }
    print(
        "| "
        f"{translations.translate('prediction.table.match')} | "
        f"{translations.translate('prediction.table.expected_goals')} | "
        f"{translations.translate('prediction.table.most_likely')} | "
        f"{translations.translate('prediction.table.srf_tip')} | "
        f"{translations.translate('prediction.table.20min_tip')} | "
        f"{translations.translate('prediction.table.hda')} | "
        f"{translations.translate('prediction.table.confidence')} | "
        f"{translations.translate('prediction.table.source')} |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    for prediction in run.predictions:
        match = f"{prediction.fixture.home_team} - {prediction.fixture.away_team}"
        confidence = f"{prediction.confidence_label} ({prediction.confidence_percent:.0%})"
        expected_goals = "-"
        if prediction.expected_home_goals is not None and prediction.expected_away_goals is not None:
            expected_goals = f"{prediction.expected_home_goals:.2f}:{prediction.expected_away_goals:.2f}"
        srf_tip = tips_by_fixture_provider.get((prediction.fixture.key, "srf.ch"))
        twenty_min_tip = tips_by_fixture_provider.get((prediction.fixture.key, "20min.ch"))
        print(
            "| "
            f"{match} | "
            f"{expected_goals} | "
            f"{prediction.most_likely.as_text()} | "
            f"{srf_tip.display_text() if srf_tip else '-'} | "
            f"{twenty_min_tip.display_text() if twenty_min_tip else '-'} | "
            f"{prediction.outcome_probabilities.as_percentages()} | "
            f"{confidence} | "
            f"{prediction.source} |"
        )


def command_plugins(_args: argparse.Namespace) -> int:
    manager = build_manager(Path(_args.project_root).resolve())
    for plugin in manager.list_plugins():
        events = ", ".join(plugin["events"])
        kind = plugin["metadata"]["kind"]
        print(f"{plugin['id']} {plugin['version']} kind={kind} priority={plugin['priority']} events={events}")
    return 0


def command_docs_plugins(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    manager = build_manager(project_root)
    docs_path = project_root / "docs" / "plugins.md"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(render_plugin_catalog(manager.plugins), encoding="utf-8")
    print(f"Wrote {docs_path}.")
    return 0


def command_data_update_hooks(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        raise RuntimeError("Structured storage is unavailable.")
    results = run_data_update_hooks(workflow.context.storage, run_id=workflow.context.run_id)
    for result in results:
        if result.get("status") == "success":
            print(f"{result['hook_id']}: applied ({result.get('rows_changed', 0)} row(s) changed).")
        elif result.get("status") == "skipped":
            print(f"{result['hook_id']}: skipped ({result.get('reason', 'already_applied')}).")
        else:
            print(f"{result.get('hook_id', 'unknown')}: {result.get('status', 'unknown')}.")
    return 0


def command_predict(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    run = workflow.next_predictions(limit=args.limit, include_closed=args.include_closed)
    if args.json:
        print(run.to_json())
    else:
        print_predictions_table(run, locale=args.locale)
        diagnostics = [diagnostic for diagnostic in run.diagnostics if diagnostic.level != "info"]
        if diagnostics:
            print()
            print("Diagnostics:")
            for diagnostic in diagnostics:
                print(f"- {diagnostic.level}: {diagnostic.message}")
    return 0


def command_workflow(args: argparse.Namespace) -> int:
    """Run the standard prediction workflow."""

    return command_predict(args)


def command_scheduled_update(args: argparse.Namespace) -> int:
    """Run the cron-friendly full update cycle."""

    project_root = Path(args.project_root).resolve()
    workflow = build_workflow(project_root)
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot run scheduled update.")
        return 1

    hook_results = run_data_update_hooks(workflow.context.storage, run_id=workflow.context.run_id)
    applied_hooks = [row for row in hook_results if row.get("status") == "success" and int(row.get("rows_changed") or 0) > 0]
    for row in applied_hooks:
        print(f"{row['hook_id']}: applied ({row.get('rows_changed', 0)} row(s) changed).")

    initial_state = load_tournament_state(workflow.context.storage)
    initial_open_count = len(initial_state.open_fixtures())
    run = workflow.next_predictions(limit=0, include_closed=False)
    snapshot_id = f"scheduled_{utc_label()}"
    snapshot_count = write_prediction_snapshot(
        workflow.context.storage,
        snapshot_id,
        run.predictions,
        run.optimized_tips,
        run_id=workflow.context.run_id,
    )

    refreshed_state = load_tournament_state(workflow.context.storage)
    open_count = len(refreshed_state.open_fixtures())
    historical_results = load_historical_results(workflow.context.storage)
    model_calibration_count = write_model_calibration(
        workflow.context.storage,
        historical_results,
        run_id=workflow.context.run_id,
    )
    backtest_rows = backtest_srf(refreshed_state, historical_results, signals=_workflow_signals(workflow))
    workflow.context.storage.write_records(
        PREDICTION_BACKTEST,
        backtest_rows,
        source="scheduled_update:backtest",
        run_id=workflow.context.run_id,
    )
    knockout_summary = knockout_backtest_summary(backtest_rows)
    knockout_audit_rows = write_provider_knockout_audit(
        workflow.context.storage,
        backtest_rows,
        run_id=workflow.context.run_id,
    )
    audit_rows = build_prediction_audit_rows(
        workflow.context.storage,
        refreshed_state,
        run_id=workflow.context.run_id,
    )
    learning_count, review_count, postmatch_summary = write_postmatch_outputs(
        workflow.context.storage,
        run_id=workflow.context.run_id,
    )

    ledger_count = write_prediction_ledger(workflow.context.storage, run_id=workflow.context.run_id)
    published_ledger_count = write_published_prediction_ledger(workflow.context.storage, run_id=workflow.context.run_id)

    provider_summary = {}
    for provider in ("srf.ch", "20min.ch"):
        point_rows = build_provider_points_rows(
            workflow.context.storage,
            refreshed_state,
            provider=provider,
            run_id=workflow.context.run_id,
        )
        bonus_rows = build_bonus_tracker_rows(
            workflow.context.storage,
            refreshed_state,
            provider=provider,
            run_id=workflow.context.run_id,
        )
        provider_summary[provider] = {
            "point_rows": len(point_rows),
            "points": sum(float(row.get("points") or 0.0) for row in point_rows),
            "bonus_rows": len(bonus_rows),
        }

    export_manifest = write_prediction_export(
        workflow.context.storage,
        project_root / "data" / "exports" / "predictions.json",
        export_id=f"{snapshot_id}:export",
        run_id=workflow.context.run_id,
    )
    simulation_refresh = _run_simulation_if_fixture_state_changed(workflow, refreshed_state, run)
    automation_hook_results, simulation_refresh = _run_scheduled_automation_hooks(
        workflow,
        refreshed_state,
        run,
        simulation_refresh=simulation_refresh,
    )
    for row in automation_hook_results:
        if row.get("status") != "success":
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        if result.get("simulation_id"):
            print(f"{row['hook_id']}: applied ({result['simulation_id']}).")
        else:
            print(f"{row['hook_id']}: applied.")
    site_build = build_site(
        project_root=project_root,
        storage=workflow.context.storage,
        gtm_container_id=gtm_container_id_from_env(project_root),
    )
    diagnostics_completeness_rows = write_diagnostics_completeness_audit(
        workflow.context.storage,
        workflow.manager.plugins,
        run_id=workflow.context.run_id,
    )
    reports = write_standard_reports(workflow.context.storage, project_root, run_id=workflow.context.run_id)
    source_ledger_path = ""
    source_ledger_summary = {}
    if hasattr(workflow.context.storage, "export_source_ledger"):
        source_ledger_path = str(workflow.context.storage.export_source_ledger())
    if hasattr(workflow.context.storage, "read_source_ledger"):
        source_ledger_rows = workflow.context.storage.read_source_ledger(run_id=workflow.context.run_id)
        source_ledger_summary = summarize_source_ledger_rows(source_ledger_rows)

    maintenance = {
        "initial_open_fixtures": initial_open_count,
        "open_fixtures": open_count,
        "backtest_rows": len(backtest_rows),
        "model_calibration_rows": model_calibration_count,
        "audit_rows": len(audit_rows),
        "postmatch_learning_rows": learning_count,
        "postmatch_review_rows": review_count,
        "postmatch_summary": postmatch_summary,
        "knockout_backtest_summary": knockout_summary,
        "provider_knockout_audit_rows": len(knockout_audit_rows),
        "providers": provider_summary,
        "prediction_ledger_rows": ledger_count,
        "published_prediction_ledger_rows": published_ledger_count,
        "prediction_export": export_manifest,
        "simulation_refresh": simulation_refresh,
        "automation_hooks": automation_hook_results,
        "site_build": site_build.to_dict(),
        "diagnostics_completeness_rows": len(diagnostics_completeness_rows),
        "reports": [report["path"] for report in reports],
        "source_ledger_path": source_ledger_path,
        "source_ledger": source_ledger_summary,
    }
    write_prediction_run_summary(
        workflow.context.storage,
        run,
        snapshot_id=snapshot_id,
        snapshot_rows=snapshot_count,
        maintenance=maintenance,
    )

    print(f"Scheduled update {snapshot_id}: {len(run.predictions)} prediction(s), {snapshot_count} snapshot row(s).")
    print(
        "Maintenance: {backtest_rows} backtest row(s), {audit_rows} audit row(s), "
        "{postmatch_learning_rows} postmatch-learning row(s), "
        "{prediction_ledger_rows} prediction-ledger row(s), "
        "{published_prediction_ledger_rows} published row(s).".format(**maintenance)
    )
    if simulation_refresh.get("ran"):
        print(
            "Simulation refresh: {iterations} run(s) ({simulation_id}, trigger: {trigger}).".format(
                **simulation_refresh
            )
        )
    return 0


def _workflow_signals(workflow) -> list:
    signals = []
    for result in workflow.context.event_results:
        signals.extend(result.signals)
    return signals


def _run_simulation_if_fixture_state_changed(workflow: PredictionWorkflow, state, run) -> dict:
    current = _simulation_fixture_metadata(state)
    previous_fingerprint = _latest_current_state_simulation_fixture_fingerprint(workflow.context.storage)
    if previous_fingerprint == current["active_fixture_fingerprint"]:
        return {
            "ran": False,
            "reason": "fixture_state_unchanged",
            "active_fixture_count": current["active_fixture_count"],
            "active_fixture_fingerprint": current["active_fixture_fingerprint"],
        }
    return _run_current_state_simulation(
        workflow,
        state,
        run,
        trigger="scheduled_fixture_state_change",
        previous_fingerprint=previous_fingerprint,
    )


def _run_scheduled_automation_hooks(
    workflow: PredictionWorkflow,
    state,
    run,
    *,
    simulation_refresh: dict,
) -> tuple[list[dict], dict]:
    """Run committed one-shot scheduled hooks and return the current simulation result."""

    current_simulation = dict(simulation_refresh)

    def trigger_current_state_simulation(_hook: AutomationHook) -> dict:
        nonlocal current_simulation
        if current_simulation.get("ran"):
            return {
                "satisfied_by": "scheduled_simulation_refresh",
                "simulation_id": current_simulation.get("simulation_id"),
                "trigger": current_simulation.get("trigger"),
            }
        current_simulation = _run_current_state_simulation(
            workflow,
            state,
            run,
            trigger="automation_hook:trigger_current_state_simulation",
        )
        return dict(current_simulation)

    results = run_automation_hooks(
        workflow.context.storage,
        handlers={ACTION_TRIGGER_CURRENT_STATE_SIMULATION: trigger_current_state_simulation},
        run_id=workflow.context.run_id,
    )
    return results, current_simulation


def _run_current_state_simulation(
    workflow: PredictionWorkflow,
    state,
    run,
    *,
    trigger: str,
    previous_fingerprint: str | None = None,
) -> dict:
    simulation_inputs = _simulation_inputs_from_state(
        workflow,
        state,
        run,
        known_results={result.fixture_key: result.score for result in state.results},
        include_current_results_in_ratings=True,
    )
    summary = TournamentSimulator(simulation_inputs).run()
    simulation_id = f"simulation_{utc_label()}"
    result = _write_simulation_outputs(
        workflow,
        summary,
        simulation_id=simulation_id,
        mode="current_state",
        state=state,
        trigger=trigger,
    )
    if previous_fingerprint is not None:
        result["previous_active_fixture_fingerprint"] = previous_fingerprint
    return result


def _latest_current_state_simulation_fixture_fingerprint(storage) -> str:
    latest = None
    for row in storage.read_records(SIMULATION_SUMMARY, latest_only=True):
        if str(row.get("mode") or "") != "current_state":
            continue
        observed = str((row.get("_record") or {}).get("observed_at_utc") or row.get("simulation_id") or "")
        if latest is None or observed > latest[0]:
            latest = (observed, row)
    if latest is None:
        return ""
    metadata = latest[1].get("metadata") if isinstance(latest[1].get("metadata"), dict) else {}
    if _simulation_summary_needs_forecast_refresh(metadata):
        return ""
    if str(metadata.get("simulation_logic_version") or "") != SIMULATION_LOGIC_VERSION:
        return ""
    return str(metadata.get("active_fixture_fingerprint") or "")


def _simulation_summary_needs_forecast_refresh(metadata: dict) -> bool:
    try:
        active_fixture_count = int(metadata.get("active_fixture_count") or 0)
    except (TypeError, ValueError):
        active_fixture_count = 0
    if active_fixture_count <= 0:
        return False
    return not isinstance(metadata.get("forecast_results"), list) or not isinstance(metadata.get("matrix_source_counts"), dict)


def _simulation_fixture_metadata(state) -> dict:
    entries = _simulation_fixture_state_entries(state)
    return {
        "active_fixture_count": len(entries),
        "active_fixture_fingerprint": stable_hash(entries),
        "simulation_logic_version": SIMULATION_LOGIC_VERSION,
    }


def _simulation_fixture_state_entries(state) -> list[dict]:
    rows = []
    for fixture in state.fixtures_without_results():
        rows.append(
            {
                "event_date": fixture.event_date,
                "source_id": fixture.source_id or "",
                "stage": fixture.stage or "",
                "status": fixture.status,
                "home": fixture.home_team.key,
                "away": fixture.away_team.key,
            }
        )
    return sorted(rows, key=lambda item: (item["event_date"], item["source_id"], item["home"], item["away"]))


def _write_simulation_outputs(
    workflow: PredictionWorkflow,
    summary,
    *,
    simulation_id: str,
    mode: str,
    state,
    trigger: str,
) -> dict:
    metadata = {
        **summary.metadata,
        **_simulation_fixture_metadata(state),
        "trigger": trigger,
    }
    summary_row = {
        "record_key": simulation_id,
        "simulation_id": simulation_id,
        "mode": mode,
        "iterations": summary.iterations,
        "seed": summary.seed,
        "distributions": summary.distributions,
        "metadata": metadata,
        "srf_bonus": evaluate_srf_bonus_questions(summary),
        "srf_best_answers": best_srf_bonus_answers(summary),
        "twenty_min_bonus": evaluate_twenty_min_bonus_questions(summary),
        "twenty_min_best_answers": best_twenty_min_bonus_answers(summary),
    }
    workflow.context.storage.write_records(
        SIMULATION_SUMMARY,
        [summary_row],
        source="simulate_tournament",
        run_id=workflow.context.run_id,
    )
    sample_rows = [
        {
            "record_key": f"{simulation_id}:{index}:{row.get('match_id')}",
            "simulation_id": simulation_id,
            **row,
        }
        for index, row in enumerate(summary.metadata.get("sample_results") or [])
    ]
    workflow.context.storage.write_records(
        SIMULATION_RUNS,
        sample_rows,
        source="simulate_tournament",
        run_id=workflow.context.run_id,
    )
    return {
        "ran": True,
        "simulation_id": simulation_id,
        "mode": mode,
        "iterations": summary.iterations,
        "active_fixture_count": metadata["active_fixture_count"],
        "active_fixture_fingerprint": metadata["active_fixture_fingerprint"],
        "trigger": trigger,
    }


def command_build_site(args: argparse.Namespace) -> int:
    """Build the static website from the published prediction ledger."""

    project_root = Path(args.project_root).resolve()
    workflow = build_workflow(project_root)
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot build site.")
        return 1
    _ensure_published_ledger(workflow.context.storage, run_id=workflow.context.run_id)
    result = build_site(
        project_root=project_root,
        storage=workflow.context.storage,
        gtm_container_id=gtm_container_id_from_env(project_root),
    )
    print(
        f"Built static site in {result.output_dir} with {result.row_count} row(s) "
        f"({result.future_count} future, {result.locked_count} locked, {result.final_count} final)."
    )
    return 0


def command_serve_site(args: argparse.Namespace) -> int:
    """Serve the generated static website locally."""

    project_root = Path(args.project_root).resolve()
    workflow = build_workflow(project_root)
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot serve site.")
        return 1
    _ensure_published_ledger(workflow.context.storage, run_id=workflow.context.run_id)
    output_dir = project_root / "public" / "current"
    if not (output_dir / "index.html").exists():
        build_site(
            project_root=project_root,
            storage=workflow.context.storage,
            gtm_container_id=gtm_container_id_from_env(project_root),
        )
    serve_site(directory=output_dir, host=args.host, port=args.port)
    return 0


def _ensure_published_ledger(storage, *, run_id: str | None = None) -> int:
    """Refresh the website-facing ledger from the latest prediction ledger."""

    if not storage.read_records(PREDICTION_LEDGER, latest_only=True):
        write_prediction_ledger(storage, run_id=run_id)
    return write_published_prediction_ledger(storage, run_id=run_id)


def command_export_predictions(args: argparse.Namespace) -> int:
    """Write the latest prediction state to one comparison-friendly JSON file."""

    project_root = Path(args.project_root).resolve()
    workflow = build_workflow(project_root)
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot export predictions.")
        return 1
    output = Path(args.output_file) if args.output_file else project_root / "data" / "exports" / "predictions.json"
    if not output.is_absolute():
        output = project_root / output
    export_id = f"prediction_export_{utc_label()}"
    manifest = write_prediction_export(
        workflow.context.storage,
        output,
        export_id=export_id,
        run_id=workflow.context.run_id,
    )
    print(f"Wrote prediction export {manifest['export_id']} with {manifest['prediction_count']} match row(s): {manifest['path']}")
    return 0


def command_baseline_bundle(args: argparse.Namespace) -> int:
    """Run the workflow and create a refactor-safety baseline bundle."""

    project_root = Path(args.project_root).resolve()
    workflow = build_workflow(project_root)
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot create baseline bundle.")
        return 1
    baseline_id = args.baseline_id or f"baseline_{utc_label()}"
    run = workflow.next_predictions(limit=0, include_closed=False)
    manifest = create_baseline_bundle(
        project_root=project_root,
        storage=workflow.context.storage,
        run=run,
        plugins=workflow.manager.plugins,
        baseline_id=baseline_id,
    )
    print(
        f"Created baseline bundle {manifest['baseline_id']} with "
        f"{manifest['prediction_count']} prediction(s): {manifest['path']}"
    )
    return 0


def command_backtest(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot run backtest.")
        return 1
    state = load_tournament_state(workflow.context.storage)
    historical_results = load_historical_results(workflow.context.storage)
    rows = backtest_srf(state, historical_results)
    count = workflow.context.storage.write_records(BACKTEST_DATASET, rows, source="backtest_srf", run_id=workflow.context.run_id)

    current = summarize_backtest_rows(rows)
    print(
        "Current tournament: {matches} finished fixture(s) | {points_per_match:.2f} pts/match "
        "(expected {expected_points_per_match:.2f}) | outcome {outcome_hit_rate:.1%} | "
        "exact {exact_hit_rate:.1%} | RPS {rps:.3f}.".format(**current)
    )
    for phase, summary in summarize_backtest_by(rows, "phase").items():
        if summary["matches"]:
            print(
                "  {phase}: {matches} match(es) | {points_per_match:.2f} pts/match | "
                "outcome {outcome_hit_rate:.1%} | RPS {rps:.3f}.".format(phase=phase or "unknown", **summary)
            )

    historical_rows = backtest_historical(historical_results)
    if historical_rows:
        print("Historical World Cup baseline (no live signals):")
        for year, summary in summarize_backtest_by(historical_rows, "year").items():
            print(
                "  {year}: {matches} match(es) | {points_per_match:.2f} pts/match | "
                "outcome {outcome_hit_rate:.1%} | exact {exact_hit_rate:.1%} | RPS {rps:.3f}.".format(year=year, **summary)
            )
        total = summarize_backtest_rows(historical_rows)
        print(
            "  TOTAL: {matches} match(es) | {points_per_match:.2f} pts/match "
            "(expected {expected_points_per_match:.2f}) | outcome {outcome_hit_rate:.1%} | "
            "exact {exact_hit_rate:.1%} | RPS {rps:.3f}.".format(**total)
        )
    return 0


def command_calibrate_model(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot calibrate model.")
        return 1
    historical_results = load_historical_results(workflow.context.storage)
    rows = calibrate_baseline_model(historical_results)
    count = workflow.context.storage.write_records(
        MODEL_CALIBRATION,
        rows,
        source="model_calibration",
        run_id=workflow.context.run_id,
    )
    selected = next((row for row in rows if row.get("selected")), None)
    if selected is None:
        print("No historical World Cup calibration sample is available.")
        return 0
    parameters = selected.get("parameters") or {}
    print(
        "Calibrated {count} candidate(s) over {sample_matches} historical World Cup match(es).".format(
            count=count,
            sample_matches=selected.get("sample_matches", 0),
        )
    )
    print(
        "Selected {calibration_id}: rho {rho}, overdispersion {overdispersion}, ml_weight {ml_weight} | "
        "{srf_points_per_match:.2f} pts/match (expected {expected_points_per_match:.2f}) | "
        "outcome {outcome_hit_rate:.1%} | RPS {rps:.3f}.".format(
            calibration_id=selected["calibration_id"],
            rho=parameters.get("dixon_coles_rho"),
            overdispersion=parameters.get("score_overdispersion"),
            ml_weight=parameters.get("ml_hda_max_weight"),
            **{key: selected[key] for key in ("srf_points_per_match", "expected_points_per_match", "outcome_hit_rate", "rps")},
        )
    )
    print("Market and expert weights are not tunable here (no historical odds/expert data); validate those forward on the live tournament.")
    return 0


def command_snapshot_predictions(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot snapshot predictions.")
        return 1
    snapshot_id = args.snapshot_id or utc_label()
    run = workflow.next_predictions(limit=args.limit, include_closed=args.include_closed)
    count = write_prediction_snapshot(workflow.context.storage, snapshot_id, run.predictions, run.optimized_tips, run_id=workflow.context.run_id)
    print(f"Created prediction snapshot {snapshot_id} with {count} fixture rows.")
    return 0


def command_compare_snapshots(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot compare prediction snapshots.")
        return 1
    rows = compare_snapshots(workflow.context.storage, args.baseline_snapshot, args.candidate_snapshot, run_id=workflow.context.run_id)
    summary = comparison_summary(rows)
    print(
        "Compared {rows} rows: {most_likely_changes} most-likely changes, {tip_changes} provider-tip changes, "
        "max H/D/A delta {max_hda_probability_delta:.2%}, max matrix TV {max_matrix_total_variation:.4f}.".format(**summary)
    )
    return 0


def command_audit_predictions(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot audit predictions.")
        return 1
    state = load_tournament_state(workflow.context.storage)
    rows = build_prediction_audit_rows(workflow.context.storage, state, run_id=workflow.context.run_id)
    total_by_provider: dict[str, float] = {}
    for row in rows:
        provider = str(row.get("provider") or "missing")
        total_by_provider[provider] = total_by_provider.get(provider, 0.0) + float(row.get("points") or 0.0)
    summary = ", ".join(f"{provider}: {points:.0f}" for provider, points in sorted(total_by_provider.items())) or "no rows"
    print(f"Audited {len(rows)} frozen prediction row(s): {summary}.")
    return 0


def command_validate_entities(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot validate entities.")
        return 1
    rows = build_entity_validation_rows(workflow.context.storage, run_id=workflow.context.run_id)
    unresolved = sum(1 for row in rows if row.get("status") == "unresolved")
    print(f"Validated {len(rows)} stored entity label(s): {unresolved} unresolved.")
    return 0


def command_generate_entity_aliases(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot generate aliases.")
        return 1
    rows = build_generated_alias_rows(workflow.context.storage, run_id=workflow.context.run_id)
    ambiguous = sum(1 for row in rows if row.get("ambiguous"))
    print(f"Generated {len(rows)} entity alias candidate row(s), {ambiguous} ambiguous.")
    return 0


def command_reports(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).resolve()
    workflow = build_workflow(project_root)
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot write reports.")
        return 1
    reports = write_standard_reports(workflow.context.storage, project_root, run_id=workflow.context.run_id)
    print("Wrote reports:")
    for report in reports:
        print(f"- {report['path']}")
    return 0


def command_simulate_tournament(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot run simulation.")
        return 1
    if args.from_day_one:
        simulation_inputs = _simulation_inputs_from_day_one(workflow)
        mode = "from_day_one"
    else:
        simulation_inputs = _simulation_inputs_from_current_state(workflow)
        mode = "current_state"
    summary = TournamentSimulator(simulation_inputs).run()
    state = load_tournament_state(workflow.context.storage)
    simulation_id = f"simulation_{utc_label()}"
    _write_simulation_outputs(
        workflow,
        summary,
        simulation_id=simulation_id,
        mode=mode,
        state=state,
        trigger="manual",
    )

    # Daily maintenance: regenerate entity-alias candidates and revalidate stored team
    # labels. These change rarely (squads are fixed during the tournament), so daily
    # cadence is sufficient and keeps the hourly prediction run lean. The regenerated
    # aliases are read by subsequent hourly predictions.
    alias_rows = build_generated_alias_rows(workflow.context.storage, run_id=workflow.context.run_id)
    validation_rows = build_entity_validation_rows(workflow.context.storage, run_id=workflow.context.run_id)
    unresolved = sum(1 for row in validation_rows if row.get("status") == "unresolved")

    print(f"Ran {summary.iterations} tournament simulations ({simulation_id}).")
    print(f"Simulation mode: {mode}.")
    print(
        f"Daily maintenance: {len(alias_rows)} alias candidate(s); "
        f"{len(validation_rows)} entity label(s) validated ({unresolved} unresolved)."
    )
    print(f"SRF best bonus answers: {best_srf_bonus_answers(summary)}")
    print(f"20min best bonus answers: {best_twenty_min_bonus_answers(summary)}")
    return 0


def _simulation_inputs_from_current_state(workflow: PredictionWorkflow) -> SimulationInputs:
    """Prepare simulation inputs that fix confirmed scores already in storage."""

    run = workflow.next_predictions(limit=0, include_closed=False)
    state = load_tournament_state(workflow.context.storage)
    return _simulation_inputs_from_state(
        workflow,
        state,
        run,
        known_results={result.fixture_key: result.score for result in state.results},
        include_current_results_in_ratings=True,
    )


def _simulation_inputs_from_day_one(workflow: PredictionWorkflow) -> SimulationInputs:
    """Prepare simulation inputs as if no tournament match had been played yet."""

    workflow.context.settings["ignore_tournament_results_for_model"] = True
    run = workflow.next_predictions(limit=0, include_closed=True, include_all_fixtures=True)
    state = load_tournament_state(workflow.context.storage)
    return _simulation_inputs_from_state(
        workflow,
        state,
        run,
        known_results={},
        include_current_results_in_ratings=False,
    )


def _simulation_inputs_from_state(
    workflow: PredictionWorkflow,
    state,
    run,
    *,
    known_results: dict[str, ScoreTip],
    include_current_results_in_ratings: bool,
) -> SimulationInputs:
    matrices = _simulation_score_matrices(run.predictions)
    team_strengths = team_strengths_from_outrights(workflow.context.storage.read_records(MARKET_OUTRIGHTS, latest_only=True))
    team_ratings = _team_ratings_for_simulation(
        workflow.context.storage,
        state,
        include_current_results=include_current_results_in_ratings,
    )
    return SimulationInputs(
        fixtures=[fixture.to_fixture() for fixture in state.fixtures],
        known_results=_simulation_known_results(state, known_results),
        known_winners=_simulation_known_winners(state, load_results(workflow.context.storage), known_results),
        score_matrices=matrices,
        score_matrix_provider=_simulation_score_matrix_provider(
            workflow,
            state,
            include_current_results=include_current_results_in_ratings,
        ),
        team_strengths=team_strengths,
        team_ratings=team_ratings,
    )


def _simulation_score_matrices(predictions) -> dict:
    """Score matrices keyed for simulator lookups.

    Group fixtures are looked up by fixture key. Knockout matches are simulated
    from bracket slots (M73..M104), so the simulator can only find their model
    matrices through "home|away" team-name pair keys.
    """

    matrices = {}
    for prediction in predictions:
        fixture = prediction.fixture
        matrices[fixture.key] = prediction.score_matrix
        if prediction.score_matrix and not fixture.group:
            matrices.setdefault(pair_key(fixture.home_team, fixture.away_team), prediction.score_matrix)
    return matrices


def _simulation_score_matrix_provider(
    workflow: PredictionWorkflow,
    state,
    *,
    include_current_results: bool,
):
    historical_results = _simulation_historical_results(
        workflow.context.storage,
        state,
        include_current_results=include_current_results,
    )
    model = BaselineModel(historical_results)
    signals = _workflow_signals(workflow)
    team_refs = _simulation_team_refs(state)
    fixture_templates = _simulation_fixture_templates(state)
    cache = {}

    def provide(fixture_key: str, home: str, away: str):
        cache_key = (fixture_key, home, away)
        if cache_key in cache:
            return cache[cache_key]
        template = fixture_templates.get(fixture_key)
        match_number = _simulation_match_number(template) if template is not None else _match_id_number(fixture_key)
        stage = (template.stage if template is not None else None) or ROUND_NAMES.get(f"M{match_number}", "Knockout stage")
        event_date = (
            template.event_date
            if template is not None
            else dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        fixture = FixtureRecord(
            event_date=event_date,
            home_team=team_refs.get(home, TeamRef(home)),
            away_team=team_refs.get(away, TeamRef(away)),
            stage=stage,
            source_id=str(match_number or fixture_key),
            metadata={
                "match_number": match_number,
                "neutral": True,
                "simulation_hypothetical": True,
            },
        )
        cache[cache_key] = model.predict_fixture(fixture, signals=signals).score_matrix
        return cache[cache_key]

    return provide


def _simulation_historical_results(storage, state, *, include_current_results: bool) -> list[HistoricalResult]:
    historical_results = load_historical_results(storage)
    if include_current_results:
        historical_results.extend(
            HistoricalResult(
                date=result.event_date[:10],
                home_team=result.home_team,
                away_team=result.away_team,
                score=result.score,
                tournament="FIFA World Cup",
                neutral=True,
                source=result.source,
            )
            for result in state.results
        )
    return historical_results


def _simulation_team_refs(state) -> dict[str, TeamRef]:
    refs: dict[str, TeamRef] = {}
    for fixture in state.fixtures:
        for team in (fixture.home_team, fixture.away_team):
            refs.setdefault(team.name, team)
            if team.fifa_code:
                refs.setdefault(team.fifa_code, team)
    for result in state.results:
        for team in (result.home_team, result.away_team):
            refs.setdefault(team.name, team)
            if team.fifa_code:
                refs.setdefault(team.fifa_code, team)
    return refs


def _simulation_fixture_templates(state) -> dict[str, FixtureRecord]:
    templates: dict[str, FixtureRecord] = {}
    for fixture in state.fixtures:
        templates.setdefault(fixture.key, fixture)
        match_number = _simulation_match_number(fixture)
        if match_number is not None:
            templates.setdefault(f"M{match_number}", fixture)
    return templates


def _simulation_match_number(fixture: FixtureRecord | None) -> int | None:
    if fixture is None:
        return None
    for value in (fixture.metadata.get("match_number"), fixture.source_id):
        number = _optional_int(value)
        if number is not None:
            return number
    return None


def _match_id_number(value: str) -> int | None:
    if value.startswith("M"):
        return _optional_int(value[1:])
    return None


def _optional_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _simulation_known_results(state, known_results: dict[str, ScoreTip]) -> dict[str, ScoreTip]:
    """Known results keyed for simulator lookups.

    Adds "home|away" pair keys for finished knockout matches so simulated
    bracket matches keep already-played results fixed instead of re-sampling
    them each iteration.
    """

    known = dict(known_results)
    group_fixture_keys = {fixture.key for fixture in state.fixtures if fixture.group}
    for result in state.results:
        score = known_results.get(result.fixture_key)
        if score is None or result.fixture_key in group_fixture_keys:
            continue
        known.setdefault(pair_key(result.home_team.name, result.away_team.name), score)
    return known


def _simulation_known_winners(state, results, known_results: dict[str, ScoreTip]) -> dict[str, str]:
    """Advancing team for finished knockout matches that ended level.

    A fixed knockout draw only pins the full-time score; without the real
    winner the simulator would re-sample the shootout each iteration. Winner
    evidence is side-based (penalty scores or an explicit home/away winner
    flag from source result rows) and only used when every source that
    reports a side agrees.
    """

    fixtures_by_key = {fixture.key: fixture for fixture in state.fixtures}
    sides_by_fixture: dict[str, set[str]] = {}
    for result in results:
        side = _result_winner_side(result)
        if side:
            sides_by_fixture.setdefault(result.fixture_key, set()).add(side)
    winners: dict[str, str] = {}
    for fixture_key, sides in sides_by_fixture.items():
        score = known_results.get(fixture_key)
        fixture = fixtures_by_key.get(fixture_key)
        if score is None or fixture is None or fixture.group:
            continue
        if score.home != score.away or len(sides) != 1:
            continue
        winner = fixture.home_team.name if "home" in sides else fixture.away_team.name
        winners[fixture_key] = winner
        winners.setdefault(pair_key(fixture.home_team.name, fixture.away_team.name), winner)
    return winners


def _result_winner_side(result) -> str | None:
    """Advancement side ("home"/"away") implied by one source result row."""

    metadata = result.metadata or {}
    try:
        home_penalties = int(metadata["home_penalty_score"])
        away_penalties = int(metadata["away_penalty_score"])
    except (KeyError, TypeError, ValueError):
        home_penalties = away_penalties = None
    if home_penalties is not None and home_penalties != away_penalties:
        return "home" if home_penalties > away_penalties else "away"
    winner = str(metadata.get("winner") or "")
    if winner == "HOME_TEAM":
        return "home"
    if winner == "AWAY_TEAM":
        return "away"
    return None


def command_postmatch_learning(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot build postmatch learning.")
        return 1
    learning_count, review_count, summary = write_postmatch_outputs(workflow.context.storage, run_id=workflow.context.run_id)
    print(
        "Built {learning_rows} postmatch-learning rows and {review_rows} review rows "
        "({high_priority} high, {medium_priority} medium).".format(**summary)
    )
    return 0


def command_provider_points(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot build provider points.")
        return 1
    state = load_tournament_state(workflow.context.storage)
    rows = build_provider_points_rows(
        workflow.context.storage,
        state,
        provider=args.provider,
        run_id=workflow.context.run_id,
    )
    total = sum(float(row.get("points") or 0.0) for row in rows)
    print(f"Built {len(rows)} {args.provider} point rows: {total:.0f} points.")
    return 0


def command_bonus_tracker(args: argparse.Namespace) -> int:
    workflow = build_workflow(Path(args.project_root).resolve())
    if workflow.context.storage is None:
        print("Structured storage is unavailable; cannot build bonus tracker.")
        return 1
    state = load_tournament_state(workflow.context.storage)
    rows = build_bonus_tracker_rows(workflow.context.storage, state, provider=args.provider, run_id=workflow.context.run_id)
    impossible = sum(1 for row in rows if row.get("status") == "impossible")
    virtual_points = next((row for row in rows if row.get("question_key") == "virtual_match_points"), None)
    suffix = ""
    if virtual_points is not None:
        suffix = f", virtual match points {float(virtual_points.get('current_value') or 0.0):.0f}"
    print(f"Built {len(rows)} {args.provider} bonus-tracker rows ({impossible} impossible{suffix}).")
    return 0


def _team_ratings_for_simulation(storage, state, *, include_current_results: bool = True) -> dict[str, float]:
    """Elo ratings keyed by team display name and FIFA code for shootout resolution.

    Includes already-finished tournament results so ratings reflect current form, and
    is keyed the way the simulator identifies teams (display names from ``to_fixture``).
    """

    config = BaselineModelConfig()
    historical_results = _simulation_historical_results(
        storage,
        state,
        include_current_results=include_current_results,
    )
    ratings_by_key = compute_elo(
        historical_results,
        cutoff=dt.datetime.now(dt.timezone.utc),
        config=config,
    )
    ratings: dict[str, float] = {}
    for fixture in state.fixtures:
        for team in (fixture.home_team, fixture.away_team):
            rating = ratings_by_key.get(team.key, config.base_rating)
            ratings[team.name] = rating
            if team.fifa_code:
                ratings[team.fifa_code] = rating
    return ratings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worldcup-predictions")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--project-root", default=".", help="Repository root. Defaults to current directory.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plugins_parser = subparsers.add_parser("plugins", help="List registered workflow plugins.")
    plugins_parser.set_defaults(func=command_plugins)

    docs_plugins_parser = subparsers.add_parser("docs-plugins", help="Regenerate docs/plugins.md from plugin metadata.")
    docs_plugins_parser.set_defaults(func=command_docs_plugins)

    data_hooks_parser = subparsers.add_parser("data-update-hooks", help="Run pending one-shot runtime data update hooks.")
    data_hooks_parser.set_defaults(func=command_data_update_hooks)

    predict_parser = subparsers.add_parser("predict", help="Render next predictions through the plugin workflow.")
    predict_parser.add_argument("--limit", type=int, default=4)
    predict_parser.add_argument("--include-closed", action="store_true")
    predict_parser.add_argument("--json", action="store_true", help="Print machine-readable workflow output.")
    predict_parser.add_argument("--locale", default=None, help="Output locale for user-facing labels. Defaults to en.")
    predict_parser.set_defaults(func=command_predict)

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Run the standard source-signal, prediction, provider-tip, and debug-report workflow.",
    )
    workflow_parser.add_argument("--limit", type=int, default=4)
    workflow_parser.add_argument("--include-closed", action="store_true")
    workflow_parser.add_argument("--json", action="store_true", help="Print machine-readable workflow output.")
    workflow_parser.add_argument("--locale", default=None, help="Output locale for user-facing labels. Defaults to en.")
    workflow_parser.set_defaults(func=command_workflow)

    scheduled_parser = subparsers.add_parser(
        "scheduled-update",
        help="Run the cron-friendly full update cycle and store a timestamped prediction snapshot.",
    )
    scheduled_parser.set_defaults(func=command_scheduled_update)

    site_build_parser = subparsers.add_parser(
        "site-build",
        help="Build the static public website from the published prediction ledger.",
    )
    site_build_parser.set_defaults(func=command_build_site)

    site_serve_parser = subparsers.add_parser(
        "site-serve",
        help="Serve the generated static website locally with production-like cache headers.",
    )
    site_serve_parser.add_argument("--host", default="127.0.0.1")
    site_serve_parser.add_argument("--port", type=int, default=8000)
    site_serve_parser.set_defaults(func=command_serve_site)

    export_parser = subparsers.add_parser(
        "export-predictions",
        help="Write latest predictions, tips, diagnostics, and score matrices to one JSON file.",
    )
    export_parser.add_argument("output_file", nargs="?", default="")
    export_parser.set_defaults(func=command_export_predictions)

    baseline_bundle_parser = subparsers.add_parser(
        "baseline-bundle",
        help="Run the workflow and create a refactor-safety baseline artifact folder.",
    )
    baseline_bundle_parser.add_argument("baseline_id", nargs="?", default="")
    baseline_bundle_parser.set_defaults(func=command_baseline_bundle)

    backtest_parser = subparsers.add_parser("backtest", help="Backtest current SRF predictions on finished fixtures.")
    backtest_parser.set_defaults(func=command_backtest)

    calibrate_parser = subparsers.add_parser("calibrate-model", help="Evaluate transparent model candidates on historical World Cups.")
    calibrate_parser.set_defaults(func=command_calibrate_model)

    snapshot_parser = subparsers.add_parser("snapshot-predictions", help="Run predictions and store a regression snapshot.")
    snapshot_parser.add_argument("snapshot_id", nargs="?", default="")
    snapshot_parser.add_argument("--limit", type=int, default=64)
    snapshot_parser.add_argument("--include-closed", action="store_true")
    snapshot_parser.set_defaults(func=command_snapshot_predictions)

    compare_parser = subparsers.add_parser("compare-snapshots", help="Compare two stored prediction snapshots.")
    compare_parser.add_argument("baseline_snapshot")
    compare_parser.add_argument("candidate_snapshot")
    compare_parser.set_defaults(func=command_compare_snapshots)

    audit_parser = subparsers.add_parser("audit-predictions", help="Audit frozen pre-match prediction snapshots against final scores.")
    audit_parser.set_defaults(func=command_audit_predictions)

    validate_entities_parser = subparsers.add_parser("validate-entities", help="Validate stored team labels against the canonical country registry.")
    validate_entities_parser.set_defaults(func=command_validate_entities)

    generate_aliases_parser = subparsers.add_parser("generate-entity-aliases", help="Generate structured entity alias candidates from stored data.")
    generate_aliases_parser.set_defaults(func=command_generate_entity_aliases)

    reports_parser = subparsers.add_parser("reports", help="Write standard Markdown reports under reports/.")
    reports_parser.set_defaults(func=command_reports)

    simulate_parser = subparsers.add_parser("simulate-tournament", help="Run the standard 20,000-iteration tournament simulation.")
    simulate_parser.add_argument(
        "--from-day-one",
        action="store_true",
        help="Simulate as if no tournament matches had been played yet, ignoring stored final scores.",
    )
    simulate_parser.set_defaults(func=command_simulate_tournament)

    postmatch_parser = subparsers.add_parser("postmatch-learning", help="Build postmatch learning and review queue rows.")
    postmatch_parser.set_defaults(func=command_postmatch_learning)

    provider_points_parser = subparsers.add_parser("provider-points", help="Score provider tips against confirmed results.")
    provider_points_parser.add_argument("provider", choices=["srf.ch", "20min.ch"])
    provider_points_parser.set_defaults(func=command_provider_points)

    bonus_tracker_parser = subparsers.add_parser("bonus-tracker", help="Track provider bonus answers and virtual match-tip points.")
    bonus_tracker_parser.add_argument("provider", choices=["srf.ch", "20min.ch"])
    bonus_tracker_parser.set_defaults(func=command_bonus_tracker)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
