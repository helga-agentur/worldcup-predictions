from __future__ import annotations

import unittest

from worldcup_predictions.core.contracts import Diagnostic
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.payloads import WorkflowStartedPayload
from worldcup_predictions.core.plugin import BasePlugin, PluginManager, PluginResult


class RecordingPlugin(BasePlugin):
    subscribed_events = (EventName.WORKFLOW_STARTED.value,)

    def __init__(self, plugin_id: str, priority: int, calls: list[str]) -> None:
        self.id = plugin_id
        self.priority = priority
        self.calls = calls

    def handle(self, event, context, payload):
        self.calls.append(self.id)
        return PluginResult(
            plugin_id=self.id,
            event=str(event),
            diagnostics=[Diagnostic(level="info", message=self.id, source=self.id)],
        )


class FailingPlugin(BasePlugin):
    id = "failing"
    subscribed_events = (EventName.WORKFLOW_STARTED.value,)

    def handle(self, event, context, payload):
        raise RuntimeError("boom")


class DummyContext:
    def __init__(self) -> None:
        self.results = []

    def record_result(self, result):
        self.results.append(result)


class PluginManagerTest(unittest.TestCase):
    def test_plugins_run_in_priority_order(self) -> None:
        calls: list[str] = []
        manager = PluginManager(
            [
                RecordingPlugin("late", 100, calls),
                RecordingPlugin("early", 10, calls),
                RecordingPlugin("middle", 50, calls),
            ]
        )

        manager.emit(EventName.WORKFLOW_STARTED, DummyContext())

        self.assertEqual(calls, ["early", "middle", "late"])

    def test_plugin_failure_is_returned_as_diagnostic_by_default(self) -> None:
        manager = PluginManager([FailingPlugin()])

        results = manager.emit(EventName.WORKFLOW_STARTED, DummyContext())

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].diagnostics[0].level, "error")
        self.assertIn("boom", results[0].diagnostics[0].message)

    def test_plugin_failure_can_be_fail_fast(self) -> None:
        manager = PluginManager([FailingPlugin()], fail_fast=True)

        with self.assertRaises(RuntimeError):
            manager.emit(EventName.WORKFLOW_STARTED, DummyContext())

    def test_typed_payloads_keep_dict_get_compatibility(self) -> None:
        payload = WorkflowStartedPayload(limit=4)

        self.assertEqual(payload.limit, 4)
        self.assertEqual(payload.get("limit"), 4)
        self.assertEqual(payload.to_dict(), {"limit": 4})


if __name__ == "__main__":
    unittest.main()
