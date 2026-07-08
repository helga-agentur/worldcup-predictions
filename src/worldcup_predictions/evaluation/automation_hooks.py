"""Versioned one-shot scheduled automation hooks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from worldcup_predictions.core.datasets import AUTOMATION_HOOKS
from worldcup_predictions.storage.ledger import normalize_datetime, utc_now


ACTION_TRIGGER_CURRENT_STATE_SIMULATION = "trigger_current_state_simulation"


@dataclass(frozen=True)
class AutomationHook:
    hook_id: str
    action: str
    reason: str


HOOK_TRIGGER_CURRENT_STATE_SIMULATION_001 = AutomationHook(
    hook_id="trigger_current_state_simulation_001",
    action=ACTION_TRIGGER_CURRENT_STATE_SIMULATION,
    reason="Refresh current-state simulation after the outright-prior market double-count guard.",
)


SCHEDULED_AUTOMATION_HOOKS: tuple[AutomationHook, ...] = (
    HOOK_TRIGGER_CURRENT_STATE_SIMULATION_001,
)


def run_automation_hooks(
    storage: Any,
    *,
    handlers: Mapping[str, Callable[[AutomationHook], Mapping[str, Any] | None]],
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Run pending scheduled automation hooks and record successful hook ids."""

    applied = {
        str(row.get("hook_id"))
        for row in storage.read_records(AUTOMATION_HOOKS, latest_only=True)
        if row.get("status") == "success" and row.get("hook_id")
    }
    results: list[dict[str, Any]] = []
    for hook in SCHEDULED_AUTOMATION_HOOKS:
        if hook.hook_id in applied:
            results.append(
                {
                    "hook_id": hook.hook_id,
                    "action": hook.action,
                    "status": "skipped",
                    "reason": "already_applied",
                }
            )
            continue
        handler = handlers.get(hook.action)
        if handler is None:
            raise RuntimeError(f"No automation hook handler registered for action {hook.action!r}.")
        handler_result = dict(handler(hook) or {})
        result = {
            "record_key": hook.hook_id,
            "hook_id": hook.hook_id,
            "action": hook.action,
            "status": "success",
            "reason": hook.reason,
            "ran_at_utc": normalize_datetime(utc_now()),
            "result": handler_result,
        }
        storage.write_records(AUTOMATION_HOOKS, [result], source="automation_hooks", run_id=run_id)
        results.append(result)
    return results
