"""Documentation generators for the plugin architecture."""

from __future__ import annotations

from worldcup_predictions.core.plugin import Plugin, plugin_metadata


def render_plugin_catalog(plugins: list[Plugin] | tuple[Plugin, ...]) -> str:
    """Render a deterministic Markdown catalog for installed plugins."""

    lines = [
        "# Plugin Catalog",
        "",
        "Generated from plugin metadata declared in the codebase.",
        "",
        "| Plugin | Kind | Priority | Events | Writes | Emits | Quota-limited |",
        "| --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for plugin in sorted(plugins, key=lambda item: (item.priority, item.id)):
        metadata = plugin_metadata(plugin)
        quota = "yes" if metadata.quota_policy.quota_limited else "no"
        lines.append(
            "| "
            f"`{plugin.id}` | "
            f"{metadata.kind.value} | "
            f"{plugin.priority} | "
            f"{_join(plugin.subscribed_events)} | "
            f"{_join(metadata.datasets_written)} | "
            f"{_join(metadata.signals_emitted)} | "
            f"{quota} |"
        )

    lines.extend(["", "## Details", ""])
    for plugin in sorted(plugins, key=lambda item: (item.priority, item.id)):
        metadata = plugin_metadata(plugin)
        lines.extend(
            [
                f"### `{plugin.id}`",
                "",
                metadata.description,
                "",
                f"- Version: `{plugin.version}`",
                f"- Kind: `{metadata.kind.value}`",
                f"- Priority: `{plugin.priority}`",
                f"- Events: {_join(plugin.subscribed_events)}",
                f"- Reads: {_join(metadata.datasets_read)}",
                f"- Writes: {_join(metadata.datasets_written)}",
                f"- Signals: {_join(metadata.signals_emitted)}",
                f"- Locales: {_join(metadata.i18n_locales)}",
            ]
        )
        if metadata.env_vars:
            lines.append("- Environment:")
            for env_var in metadata.env_vars:
                required = "required" if env_var.required else "optional"
                description = f": {env_var.description}" if env_var.description else ""
                lines.append(f"  - `{env_var.name}` ({required}){description}")
        if metadata.quota_policy.quota_limited or metadata.quota_policy.description:
            lines.append(
                "- Quota: "
                f"{'limited' if metadata.quota_policy.quota_limited else 'not limited'}"
                f"{' and ledger-required' if metadata.quota_policy.ledger_required else ''}"
                f"{' - ' + metadata.quota_policy.description if metadata.quota_policy.description else ''}"
            )
        if metadata.confidence_policy:
            lines.append(f"- Confidence policy: {metadata.confidence_policy}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _join(values: tuple[str, ...] | list[str]) -> str:
    if not values:
        return "-"
    return ", ".join(f"`{value}`" for value in values)
