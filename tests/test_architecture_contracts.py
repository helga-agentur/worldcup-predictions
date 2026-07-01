from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worldcup_predictions.core.config import load_project_config
from worldcup_predictions.core.contracts import Signal
from worldcup_predictions.core.events import EventName
from worldcup_predictions.core.i18n import load_translation_catalog
from worldcup_predictions.core.metadata import PluginKind, PluginMetadata
from worldcup_predictions.core.plugin import BasePlugin, PluginManager, PluginResult
from worldcup_predictions.documentation import render_plugin_catalog
from worldcup_predictions.plugins import builtin_plugins


class UnknownSignalPlugin(BasePlugin):
    id = "unknown_signal"
    subscribed_events = (EventName.FEATURE_SIGNALS_REQUESTED.value,)
    metadata = PluginMetadata(
        plugin_id=id,
        kind=PluginKind.SIGNAL,
        description="Test plugin.",
    )

    def handle(self, event, context, payload):
        return PluginResult(
            plugin_id=self.id,
            event=str(event),
            signals=[Signal(name="not_registered", source=self.id)],
        )


class DummyContext:
    def __init__(self) -> None:
        self.results = []

    def record_result(self, result):
        self.results.append(result)


class ArchitectureContractsTest(unittest.TestCase):
    def test_builtin_plugins_declare_metadata(self) -> None:
        manager = PluginManager(builtin_plugins())
        metadata = {plugin["id"]: plugin["metadata"] for plugin in manager.list_plugins()}

        self.assertEqual(metadata["market_odds"]["kind"], "source")
        self.assertIn("market_hda_probabilities", metadata["market_odds"]["signals_emitted"])
        self.assertEqual(metadata["provider_optimizer_srf_ch"]["kind"], "provider_optimizer")

    def test_unknown_signal_is_reported_as_diagnostic(self) -> None:
        manager = PluginManager([UnknownSignalPlugin()])

        results = manager.emit(EventName.FEATURE_SIGNALS_REQUESTED, DummyContext())

        self.assertEqual(results[0].diagnostics[0].level, "warning")
        self.assertIn("unknown signal", results[0].diagnostics[0].message)

    def test_translation_catalog_falls_back_to_english(self) -> None:
        catalog = load_translation_catalog("de-CH")
        unknown_locale = load_translation_catalog("es")

        self.assertEqual(catalog.translate("prediction.table.match"), "Partie")
        self.assertEqual(unknown_locale.translate("prediction.table.match"), "Match")

    def test_translation_catalogs_expose_same_keys(self) -> None:
        english = load_translation_catalog("en")
        german = load_translation_catalog("de")

        self.assertEqual(set(german.messages), set(english.messages))

    def test_project_config_loads_root_toml_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "worldcup_predictions.toml").write_text(
                "[project]\n"
                'default_locale = "de"\n'
                'supported_locales = ["en", "de", "fr"]\n'
                "\n[source_defaults]\n"
                "timeout_seconds = 12\n",
                encoding="utf-8",
            )

            config = load_project_config(root)

            self.assertEqual(config.default_locale, "de")
            self.assertEqual(config.supported_locales, ("en", "de", "fr"))
            self.assertEqual(config.source_defaults.timeout_seconds, 12)

    def test_plugin_catalog_renders_metadata(self) -> None:
        manager = PluginManager(builtin_plugins())

        catalog = render_plugin_catalog(manager.plugins)

        self.assertIn("# Plugin Catalog", catalog)
        self.assertIn("`market_odds`", catalog)
        self.assertIn("`provider_optimizer_srf_ch`", catalog)
        self.assertIn("Quota", catalog)


if __name__ == "__main__":
    unittest.main()
