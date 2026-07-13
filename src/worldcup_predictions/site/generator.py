"""Generate a static, SEO-friendly prediction website."""

from __future__ import annotations

import datetime as dt
import hashlib
import html as html_lib
import json
import mimetypes
import shutil
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from worldcup_predictions.core.contracts import ScoreTip
from worldcup_predictions.core.constants import ENV_BASE_URL, ENV_GTM_CONTAINER_ID
from worldcup_predictions.core.datasets import (
    MARKET_OUTRIGHTS,
    PROVIDER_POINTS,
    PUBLISHED_PREDICTION_LEDGER,
    SIMULATION_SUMMARY,
    TOURNAMENT_FIXTURES,
    WEATHER_OBSERVATIONS,
)
from worldcup_predictions.core.env import env_value
from worldcup_predictions.core.i18n import SUPPORTED_LOCALES, TranslationCatalog, load_translation_catalog
from worldcup_predictions.entities.countries import CountryRegistry, load_country_registry
from worldcup_predictions.entities.countries import normalize_entity_text
from worldcup_predictions.evaluation.provider_points import points_for_row
from worldcup_predictions.plugins.providers.ch_20min.rules import twenty_min_points_for_fixture
from worldcup_predictions.plugins.providers.ch_srf.rules import srf_rules_for_fixture
from worldcup_predictions.simulations.worldcup_2026 import NEXT_ROUNDS, ROUND_NAMES, group_letter
from worldcup_predictions.storage.ledger import normalize_datetime, utc_now
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamRef
from worldcup_predictions.tournament.repository import load_tournament_state
from worldcup_predictions.tournament.slots import canonical_slot_code, slot_display_name


HELGA_FONT_CADIZ_WOFF2 = "/assets/fonts/CadizWeb-Regular.woff2"
HELGA_FONT_DEGULAR_WOFF2 = "/assets/fonts/Degular-Regular.woff2"
FONT_ASSET_FILES = (
    "assets/fonts/CadizWeb-Regular.woff2",
    "assets/fonts/Degular-Regular.woff2",
)
BRACKETRY_ASSET_FILE = "assets/vendor/bracketry-1.1.3.esm.js"
CONFETTI_ASSET_FILE = "assets/vendor/canvas-confetti-1.9.4.module.mjs"
OG_IMAGE_ASSET_FILE = "assets/world-cup-2026-predictions-og.png"
OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630
STATIC_ASSET_FILES = (
    "assets/favicon.svg",
    OG_IMAGE_ASSET_FILE,
    BRACKETRY_ASSET_FILE,
    CONFETTI_ASSET_FILE,
    *FONT_ASSET_FILES,
)

HTML_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=3600"
JSON_CACHE_CONTROL = "public, max-age=60, stale-while-revalidate=300"
ASSET_CACHE_CONTROL = "public, max-age=3600, immutable"
JSON_FEED_PATH = "/api/predictions"
SITE_BASE_URL = "https://tippspiel.helga.ch"
DEFAULT_SITE_LOCALE = "en"
LANGUAGE_COOKIE_NAME = "helga_language"
SITE_LOCALES = tuple(locale for locale in SUPPORTED_LOCALES if locale in {"de", "en"})
LOCALE_DETAIL_PREFIX = {
    "de": "spiele",
    "en": "matches",
}
LOCALE_MATCH_LIST_PATHS = {
    "de": {
        "future": "/de/spiele/kommende",
        "past": "/de/spiele/vergangene",
    },
    "en": {
        "future": "/en/matches/future",
        "past": "/en/matches/past",
    },
}
LOCALE_TOURNAMENT_PATHS = {
    "de": "/de/turnierprognose",
    "en": "/en/tournament-forecast",
}
LEGACY_TOURNAMENT_PATHS = {
    "de": "/de/turnier",
    "en": "/en/tournament",
}
HOMEPAGE_MATCH_PREVIEW_LIMIT = 5
HOMEPAGE_CHAMPION_PREVIEW_LIMIT = 5
HEATMAP_MAX_GOALS = 5
KNOCKOUT_BRACKET_ROUNDS = (
    "Round of 32",
    "Round of 16",
    "Quarter-final",
    "Semi-final",
    "Final",
)
KNOCKOUT_ROUND_STAGE_KEYS = {
    "Round of 32": "slot.round.round_of_32",
    "Round of 16": "slot.round.round_of_16",
    "Quarter-final": "slot.round.quarter_final",
    "Semi-final": "slot.round.semi_final",
    "Third-place match": "stage.third_place",
    "Final": "slot.round.final",
}
KNOCKOUT_ROUND_AFTER = {
    "Round of 32": "Round of 16",
    "Round of 16": "Quarter-final",
    "Quarter-final": "Semi-final",
    "Semi-final": "Final",
}
# UTC date boundaries for the fixed 2026 schedule, used when a knockout row
# carries neither a match number nor slot codes.
KNOCKOUT_ROUND_DATE_RANGES = (
    ("2026-06-28", "2026-07-04", "Round of 32"),
    ("2026-07-05", "2026-07-08", "Round of 16"),
    ("2026-07-09", "2026-07-12", "Quarter-final"),
    ("2026-07-13", "2026-07-16", "Semi-final"),
    ("2026-07-17", "2026-07-18", "Third-place match"),
    ("2026-07-19", "2026-07-31", "Final"),
)
API_PRESENTATION_KEYS = {
    "actual_score",
    "actual_score_label",
    "advancement",
    "alternate_links",
    "away_flag",
    "away_team_display",
    "away_team_label",
    "card_aria_label",
    "confidence_text",
    "current_url",
    "detail_path",
    "expected_score_display",
    "expected_score_full",
    "explain_text",
    "hda_aria",
    "hda_bar",
    "hda_title",
    "hda_parts",
    "heatmap",
    "hit_label",
    "home_flag",
    "home_team_display",
    "home_team_label",
    "kickoff_display",
    "language_switch_links",
    "match",
    "match_display",
    "match_title_text",
    "meta_description",
    "metadata",
    "most_likely_away_display",
    "most_likely_home_display",
    "most_likely_percent_text",
    "most_likely_score",
    "og_description",
    "page_title",
    "provider_tips",
    "record_key",
    "srf_account_display",
    "srf_expected_points_display",
    "srf_projected_points_display",
    "srf_tip_points_display",
    "srf_tip_points_title_key",
    "srf_tip_label",
    "stage_label",
    "status_label",
    "top_score_matrix",
    "twenty_min_account_display",
    "twenty_min_expected_points_display",
    "twenty_min_projected_points_display",
    "twenty_min_projected_points_title_key",
    "twenty_min_tip_points_display",
    "twenty_min_tip_points_title_key",
    "twenty_min_tip_plain_label",
    "twenty_min_tip_label",
}

FIFA_FLAG_EMOJIS = {
    "ALG": "🇩🇿",
    "ARG": "🇦🇷",
    "AUS": "🇦🇺",
    "AUT": "🇦🇹",
    "BEL": "🇧🇪",
    "BIH": "🇧🇦",
    "BRA": "🇧🇷",
    "CAN": "🇨🇦",
    "CIV": "🇨🇮",
    "COD": "🇨🇩",
    "COL": "🇨🇴",
    "CPV": "🇨🇻",
    "CRO": "🇭🇷",
    "CUW": "🇨🇼",
    "CZE": "🇨🇿",
    "ECU": "🇪🇨",
    "EGY": "🇪🇬",
    "ENG": "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "ESP": "🇪🇸",
    "FRA": "🇫🇷",
    "GER": "🇩🇪",
    "GHA": "🇬🇭",
    "HAI": "🇭🇹",
    "IRN": "🇮🇷",
    "IRQ": "🇮🇶",
    "JOR": "🇯🇴",
    "JPN": "🇯🇵",
    "KOR": "🇰🇷",
    "KSA": "🇸🇦",
    "MAR": "🇲🇦",
    "MEX": "🇲🇽",
    "NED": "🇳🇱",
    "NOR": "🇳🇴",
    "NZL": "🇳🇿",
    "PAN": "🇵🇦",
    "PAR": "🇵🇾",
    "POR": "🇵🇹",
    "QAT": "🇶🇦",
    "RSA": "🇿🇦",
    "SCO": "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "SEN": "🇸🇳",
    "SUI": "🇨🇭",
    "SWE": "🇸🇪",
    "TUN": "🇹🇳",
    "TUR": "🇹🇷",
    "URU": "🇺🇾",
    "USA": "🇺🇸",
    "UZB": "🇺🇿",
}


@dataclass(frozen=True)
class SiteBuildResult:
    """Manifest returned after a static site build."""

    output_dir: Path
    generated_at_utc: str
    html_files: tuple[str, ...]
    json_files: tuple[str, ...]
    asset_files: tuple[str, ...]
    row_count: int
    future_count: int
    locked_count: int
    final_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": str(self.output_dir),
            "generated_at_utc": self.generated_at_utc,
            "html_files": list(self.html_files),
            "json_files": list(self.json_files),
            "asset_files": list(self.asset_files),
            "row_count": self.row_count,
            "future_count": self.future_count,
            "locked_count": self.locked_count,
            "final_count": self.final_count,
            "cache_control": {
                "html": HTML_CACHE_CONTROL,
                "json": JSON_CACHE_CONTROL,
                "assets": ASSET_CACHE_CONTROL,
            },
        }


@dataclass(frozen=True)
class KnockoutBracketMatch:
    """One fixture/result pair for the public tournament bracket."""

    match_number: int
    fixture: FixtureRecord
    result: ResultRecord | None = None


@dataclass(frozen=True)
class BracketTeam:
    """Resolved team/slot identity for the forecast bracket."""

    id: str
    label: str
    ref: TeamRef


@dataclass(frozen=True)
class BracketProjection:
    """Displayed result and advancing team for one bracket match."""

    home: BracketTeam
    away: BracketTeam
    score: ScoreTip | None
    winner: BracketTeam | None
    status: str


def build_site(
    *,
    project_root: Path,
    storage,
    output_dir: Path | None = None,
    gtm_container_id: str | None = None,
    base_url: str | None = None,
) -> SiteBuildResult:
    """Build the static website from the latest published prediction ledger."""

    project_root = Path(project_root)
    target_dir = output_dir or project_root / "public" / "current"
    generated_at = normalize_datetime(utc_now()) or ""
    site_base_url = normalized_base_url(base_url or base_url_from_env(project_root))
    ledger_rows = [_strip_record(row) for row in storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)]
    ledger_rows.sort(key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")))
    country_registry = load_country_registry()
    placeholder_rows = _unpredicted_fixture_rows(storage, ledger_rows, country_registry=country_registry)
    source_rows = [*ledger_rows, *placeholder_rows]
    _attach_fixture_context(storage, source_rows)
    rows = [_prepare_html_row(row, country_registry=country_registry, locale="de") for row in source_rows]
    rows.sort(key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")))
    _apply_provider_point_accounts(rows, country_registry=country_registry)
    _add_alternate_links("de", rows)
    future_rows = [row for row in rows if row.get("status") == "future"]
    locked_rows = [row for row in rows if row.get("status") == "locked"]
    final_rows = [row for row in rows if row.get("status") == "final"]
    tipped_rows = sorted(
        [row for row in rows if row.get("status") in {"locked", "final"}],
        key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")),
        reverse=True,
    )
    provider_points = _site_provider_point_totals(storage, rows, country_registry=country_registry)
    provider_max_points = _site_provider_point_max_totals(rows, country_registry=country_registry)
    hit_counts = _hit_counts(rows)
    champion_odds = _champion_odds(storage, country_registry=country_registry)
    knockout_bracket_matches = _knockout_bracket_matches(storage)
    data_payload = {
        "generated_at_utc": generated_at,
        "summary": {
            "rows": len(rows),
            "future": len(future_rows),
            "locked": len(locked_rows),
            "final": len(final_rows),
            "tipped": len(locked_rows) + len(final_rows),
            "srf_points": provider_points["srf.ch"],
            "twenty_min_points": provider_points["20min.ch"],
            "srf_max_points": provider_max_points["srf.ch"],
            "twenty_min_max_points": provider_max_points["20min.ch"],
            "srf_points_display": _points_text(provider_points["srf.ch"]),
            "twenty_min_points_display": _points_text(provider_points["20min.ch"]),
            "srf_max_points_display": _points_text(provider_max_points["srf.ch"]),
            "twenty_min_max_points_display": _points_text(provider_max_points["20min.ch"]),
            **hit_counts,
        },
        "predictions": _api_rows(source_rows, country_registry=country_registry, base_url=site_base_url),
    }

    css_content = _render_css()
    js_content = _render_js()
    css_hash = hashlib.sha256(css_content.encode("utf-8")).hexdigest()[:12]
    js_hash = hashlib.sha256(js_content.encode("utf-8")).hexdigest()[:12]
    asset_path = f"assets/site.{css_hash}.css"
    script_path = f"assets/theme.{js_hash}.js"

    localized_contexts = {
        locale: _site_context(
            locale=locale,
            generated_at=generated_at,
            source_rows=source_rows,
            asset_path=asset_path,
            script_path=script_path,
            gtm_container_id=gtm_container_id,
            summary=data_payload["summary"],
            country_registry=country_registry,
            champion_odds=champion_odds,
            knockout_bracket_matches=knockout_bracket_matches,
            base_url=site_base_url,
        )
        for locale in SITE_LOCALES
    }

    temp_dir = target_dir.with_name(f".{target_dir.name}.tmp")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    _write_site_files(
        temp_dir,
        localized_contexts=localized_contexts,
        css_content=css_content,
        js_content=js_content,
        asset_path=asset_path,
        script_path=script_path,
        data_payload=_public_api_payload(data_payload),
    )
    if target_dir.exists():
        shutil.rmtree(target_dir)
    temp_dir.rename(target_dir)

    result = SiteBuildResult(
        output_dir=target_dir,
        generated_at_utc=generated_at,
        html_files=tuple(_html_files(localized_contexts)),
        json_files=("api/predictions", "site-manifest.json"),
        asset_files=(asset_path, script_path, *STATIC_ASSET_FILES),
        row_count=len(rows),
        future_count=len(future_rows),
        locked_count=len(locked_rows),
        final_count=len(final_rows),
    )
    (target_dir / "site-manifest.json").write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def serve_site(*, directory: Path, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve a generated static site with production-like cache headers."""

    directory = Path(directory).resolve()

    class Handler(_CacheAwareStaticHandler):
        pass

    Handler.directory = str(directory)  # type: ignore[attr-defined]
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving {directory} at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped static site server.")
    finally:
        server.server_close()


def _site_context(
    *,
    locale: str,
    generated_at: str,
    source_rows: list[dict[str, Any]],
    asset_path: str,
    script_path: str,
    gtm_container_id: str | None,
    summary: dict[str, Any],
    country_registry: CountryRegistry,
    champion_odds: dict[str, Any] | None,
    knockout_bracket_matches: list[KnockoutBracketMatch],
    base_url: str,
) -> dict[str, Any]:
    catalog = load_translation_catalog(locale)
    rows = [_prepare_html_row(row, country_registry=country_registry, locale=locale) for row in source_rows]
    rows.sort(key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")))
    _apply_provider_point_accounts(rows, country_registry=country_registry)
    future_rows = [row for row in rows if row.get("status") == "future"]
    locked_rows = [row for row in rows if row.get("status") == "locked"]
    final_rows = [row for row in rows if row.get("status") == "final"]
    tipped_rows = sorted(
        [row for row in rows if row.get("status") in {"locked", "final"}],
        key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")),
        reverse=True,
    )
    _add_alternate_links(locale, rows)
    future_list_path = LOCALE_MATCH_LIST_PATHS[locale]["future"]
    past_list_path = LOCALE_MATCH_LIST_PATHS[locale]["past"]
    context = {
        "locale": locale,
        "title": catalog.translate("seo.home.title"),
        "site_name": catalog.translate("site.name"),
        "og_locale": {"de": "de_CH", "en": "en_US"}.get(locale, locale),
        "og_alternate_locales": [
            {"de": "de_CH", "en": "en_US"}.get(site_locale, site_locale)
            for site_locale in SITE_LOCALES
            if site_locale != locale
        ],
        "description": catalog.translate("seo.home.description"),
        "generated_at_utc": generated_at,
        "generated_at_display": _date_time_text(generated_at),
        "asset_css": f"/{asset_path}",
        "asset_js": f"/{script_path}",
        "og_image_url": _absolute_site_url(f"/{OG_IMAGE_ASSET_FILE}", base_url=base_url),
        "og_image_width": OG_IMAGE_WIDTH,
        "og_image_height": OG_IMAGE_HEIGHT,
        "og_image_type": "image/png",
        "og_image_alt": catalog.translate("seo.og_image_alt"),
        "bracketry_asset": f"/{BRACKETRY_ASSET_FILE}",
        "confetti_asset": f"/{CONFETTI_ASSET_FILE}",
        "gtm_container_id": (gtm_container_id or "").strip(),
        "rows": rows,
        "future_rows": future_rows,
        "future_preview_rows": future_rows[:HOMEPAGE_MATCH_PREVIEW_LIMIT],
        "locked_rows": locked_rows,
        "final_rows": final_rows,
        "tipped_rows": tipped_rows,
        "tipped_preview_rows": tipped_rows[:HOMEPAGE_MATCH_PREVIEW_LIMIT],
        "preview_limit": HOMEPAGE_MATCH_PREVIEW_LIMIT,
        "future_list_path": future_list_path,
        "past_list_path": past_list_path,
        "summary": summary,
        "summary_extras": _summary_extras(summary, future_rows, catalog=catalog),
        "champion": _localized_champion_odds(champion_odds, country_registry=country_registry, locale=locale, catalog=catalog),
        "tournament_bracket": _localized_tournament_bracket(
            knockout_bracket_matches,
            prediction_rows=rows,
            simulation_forecast_results=(champion_odds or {}).get("forecast_results") or [],
            country_registry=country_registry,
            locale=locale,
            catalog=catalog,
        ),
        "tournament_path": LOCALE_TOURNAMENT_PATHS[locale],
        "json_feed_path": JSON_FEED_PATH,
        "language_cookie_name": LANGUAGE_COOKIE_NAME,
        "language_switch_links": _language_switch_links(locale, ""),
        "current_url": f"/{locale}/",
        "alternate_links": _alternate_links(""),
        "base_url": base_url,
        "t": catalog.translate,
    }
    context["jsonld_graph"] = _page_jsonld_graph(
        context,
        page_kind="home",
        item_rows=[*context["future_preview_rows"], *context["tipped_preview_rows"]],
    )
    return context


def _hit_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    exact = sum(1 for row in rows if row.get("hit_result") == "exact")
    trend = sum(1 for row in rows if row.get("hit_result") == "trend")
    miss = sum(1 for row in rows if row.get("hit_result") == "miss")
    scored = exact + trend + miss
    hit_rate = round(100 * (exact + trend) / scored) if scored else 0
    return {
        "hit_exact": exact,
        "hit_trend": trend,
        "hit_miss": miss,
        "hit_scored": scored,
        "hit_rate_percent": hit_rate,
    }


def _summary_extras(
    summary: dict[str, Any],
    future_rows: list[dict[str, Any]],
    *,
    catalog: TranslationCatalog,
) -> dict[str, Any]:
    scored = int(summary.get("hit_scored") or 0)
    exact = int(summary.get("hit_exact") or 0)
    trend = int(summary.get("hit_trend") or 0)
    miss = int(summary.get("hit_miss") or 0)
    played = int(summary.get("final") or 0)
    total = int(summary.get("rows") or 0)
    next_kickoff = _kickoff_compact_display(future_rows[0].get("event_date"), catalog=catalog) if future_rows else ""
    return {
        "srf_points_bar": _summary_progress_bar(
            _float_value(summary.get("srf_points")),
            _float_value(summary.get("srf_max_points")),
            label=catalog.translate(
                "summary.points_progress",
                current=_points_text(_float_value(summary.get("srf_points"))),
                max=_points_text(_float_value(summary.get("srf_max_points"))),
            ),
        ),
        "twenty_min_points_bar": _summary_progress_bar(
            _float_value(summary.get("twenty_min_points")),
            _float_value(summary.get("twenty_min_max_points")),
            label=catalog.translate(
                "summary.points_progress",
                current=_points_text(_float_value(summary.get("twenty_min_points"))),
                max=_points_text(_float_value(summary.get("twenty_min_max_points"))),
            ),
        ),
        "played_matches_display": str(played),
        "played_matches_bar": _summary_progress_bar(
            float(played),
            float(total),
            label=catalog.translate("summary.matches_progress", current=played, max=total),
        ),
        "hit_rate_text": f"{int(summary.get('hit_rate_percent') or 0)}%",
        "hit_breakdown_text": catalog.translate("summary.hit_breakdown", exact=exact, trend=trend, miss=miss) if scored else "",
        "hit_counts": {
            "exact": exact,
            "trend": trend,
            "miss": miss,
        },
        "hit_widths": {
            "exact": f"{100 * exact / scored:.2f}" if scored else "0",
            "trend": f"{100 * trend / scored:.2f}" if scored else "0",
            "miss": f"{100 * miss / scored:.2f}" if scored else "0",
        },
        "has_hits": scored > 0,
        "next_kickoff": next_kickoff,
    }


def _summary_progress_bar(current: float, maximum: float, *, label: str) -> dict[str, Any]:
    if maximum <= 0:
        return {"has_progress": False, "label": "", "width": "0", "remaining_width": "100"}
    capped = min(max(current, 0.0), maximum)
    width = 100 * capped / maximum
    return {
        "has_progress": True,
        "label": label,
        "width": f"{width:.2f}",
        "remaining_width": f"{100 - width:.2f}",
    }


def _champion_odds(storage, *, country_registry: CountryRegistry) -> dict[str, Any] | None:
    active_team_codes = _active_champion_team_codes(storage, country_registry=country_registry)
    simulation = _simulation_champion_odds(storage, country_registry=country_registry, active_team_codes=active_team_codes)
    if simulation is not None:
        return simulation
    return _market_champion_odds(storage, country_registry=country_registry, active_team_codes=active_team_codes)


def _knockout_bracket_matches(storage) -> list[KnockoutBracketMatch]:
    state = load_tournament_state(storage)
    fixtures_by_number: dict[int, FixtureRecord] = {}
    for fixture in state.fixtures:
        match_number = _knockout_match_number(fixture)
        if match_number is None:
            continue
        existing = fixtures_by_number.get(match_number)
        if existing is None or _bracket_fixture_source_priority(fixture) > _bracket_fixture_source_priority(existing):
            fixtures_by_number[match_number] = fixture

    results_by_number: dict[int, ResultRecord] = {}
    results_by_key = {result.fixture_key: result for result in state.results}
    for result in state.results:
        match_number = _knockout_match_number(result)
        if match_number is None:
            continue
        existing = results_by_number.get(match_number)
        if existing is None or _bracket_result_source_priority(result) > _bracket_result_source_priority(existing):
            results_by_number[match_number] = result

    matches = []
    for match_number, fixture in sorted(fixtures_by_number.items()):
        result = results_by_number.get(match_number) or results_by_key.get(fixture.key)
        matches.append(KnockoutBracketMatch(match_number=match_number, fixture=fixture, result=result))
    return matches


def _localized_tournament_bracket(
    matches: list[KnockoutBracketMatch],
    *,
    prediction_rows: list[dict[str, Any]],
    simulation_forecast_results: list[dict[str, Any]],
    country_registry: CountryRegistry,
    locale: str,
    catalog: TranslationCatalog,
) -> dict[str, Any] | None:
    if not matches:
        return None
    visible_rounds = _visible_knockout_bracket_rounds(matches)
    if not visible_rounds:
        return None
    round_index = {round_name: index for index, round_name in enumerate(visible_rounds)}
    round_match_numbers = _bracket_round_match_numbers(visible_rounds)
    current_matches = []
    contestants: dict[str, dict[str, Any]] = {}
    fallback_rows = []
    actual_winners: dict[str, BracketTeam] = {}
    projected_winners: dict[str, BracketTeam] = {}
    prediction_lookup = _bracket_prediction_lookup(prediction_rows)
    simulation_forecast_lookup = _bracket_simulation_forecast_lookup(simulation_forecast_results)
    team_ratings = _bracket_team_ratings(prediction_rows, country_registry=country_registry)
    matches_by_number = {match.match_number: match for match in matches}

    for bracket_match in matches:
        round_name = ROUND_NAMES.get(f"M{bracket_match.match_number}")
        if round_name not in KNOCKOUT_BRACKET_ROUNDS:
            continue
        fixture = bracket_match.fixture
        current_home = _bracket_team(fixture.home_team, actual_winners, country_registry=country_registry, locale=locale)
        current_away = _bracket_team(fixture.away_team, actual_winners, country_registry=country_registry, locale=locale)
        current_projection = _current_bracket_projection(
            bracket_match,
            home=current_home,
            away=current_away,
            catalog=catalog,
        )
        if current_projection.winner is not None:
            actual_winners[f"M{bracket_match.match_number}"] = current_projection.winner

        home = _bracket_team(fixture.home_team, projected_winners, country_registry=country_registry, locale=locale)
        away = _bracket_team(fixture.away_team, projected_winners, country_registry=country_registry, locale=locale)
        prediction_row = _bracket_prediction_row(fixture, home, away, prediction_lookup)
        forecast_row = simulation_forecast_lookup.get(bracket_match.match_number)
        projection = (
            _forecast_bracket_projection(
                forecast_row,
                country_registry=country_registry,
                locale=locale,
                catalog=catalog,
            )
            if bracket_match.result is None and _should_use_simulation_forecast(forecast_row, prediction_row, home, away)
            else _bracket_projection(
                bracket_match,
                home=home,
                away=away,
                prediction_row=prediction_row,
                team_ratings=team_ratings,
                catalog=catalog,
            )
        )
        if projection.winner is not None:
            projected_winners[f"M{bracket_match.match_number}"] = projection.winner
        if round_name not in round_index:
            continue
        _add_bracket_contestants(contestants, current_projection.home, current_projection.away, projection.home, projection.away)
        current_matches.append(
            _bracket_match_payload(
                bracket_match,
                round_name=round_name,
                projection=current_projection,
                round_index=round_index,
                round_match_numbers=round_match_numbers,
            )
        )
        fallback_rows.append(_bracket_fallback_row(projection, round_name=round_name, catalog=catalog))

    if not current_matches:
        return None
    timeline_steps = _bracket_timeline_steps(
        matches,
        matches_by_number=matches_by_number,
        visible_round_index=round_index,
        round_match_numbers=round_match_numbers,
        initial_winners=actual_winners,
        prediction_lookup=prediction_lookup,
        simulation_forecast_lookup=simulation_forecast_lookup,
        team_ratings=team_ratings,
        country_registry=country_registry,
        locale=locale,
        catalog=catalog,
        contestants=contestants,
    )
    rounds = [
        {"name": catalog.translate(KNOCKOUT_ROUND_STAGE_KEYS[round_name])}
        for round_name in visible_rounds
    ]
    data = {
        "rounds": rounds,
        "matches": current_matches,
        "contestants": contestants,
    }
    return {
        "data_json": json.dumps(data, ensure_ascii=False, sort_keys=True),
        "timeline_json": json.dumps({"steps": timeline_steps}, ensure_ascii=False, sort_keys=True),
        "fallback_rows": fallback_rows,
    }


def _visible_knockout_bracket_rounds(matches: list[KnockoutBracketMatch]) -> list[str]:
    matches_by_round: dict[str, list[KnockoutBracketMatch]] = {round_name: [] for round_name in KNOCKOUT_BRACKET_ROUNDS}
    for match in matches:
        round_name = ROUND_NAMES.get(f"M{match.match_number}")
        if round_name in matches_by_round:
            matches_by_round[round_name].append(match)
    return [
        round_name
        for round_name in KNOCKOUT_BRACKET_ROUNDS
        if matches_by_round[round_name]
        and (round_name == "Final" or any(match.result is None for match in matches_by_round[round_name]))
    ]


def _bracket_round_match_numbers(round_names: list[str]) -> dict[str, list[int]]:
    visual_order = _knockout_visual_match_order()
    return {round_name: visual_order.get(round_name, []) for round_name in round_names}


def _knockout_visual_match_order() -> dict[str, list[int]]:
    dependencies = {
        match_id: (home_source, away_source)
        for round_matches in NEXT_ROUNDS
        for match_id, home_source, away_source in round_matches
    }
    final_match_ids = sorted(match_id for match_id, round_name in ROUND_NAMES.items() if round_name == "Final")
    visual_order: dict[str, list[int]] = {}
    for round_name in KNOCKOUT_BRACKET_ROUNDS:
        ordered_ids: list[str] = []
        for final_match_id in final_match_ids:
            _collect_visual_round_match_ids(
                final_match_id,
                target_round=round_name,
                dependencies=dependencies,
                ordered_ids=ordered_ids,
            )
        fallback_ids = sorted(
            (match_id for match_id, name in ROUND_NAMES.items() if name == round_name),
            key=lambda value: int(value.removeprefix("M")),
        )
        for match_id in fallback_ids:
            if match_id not in ordered_ids:
                ordered_ids.append(match_id)
        visual_order[round_name] = [int(match_id.removeprefix("M")) for match_id in ordered_ids]
    return visual_order


def _collect_visual_round_match_ids(
    match_id: str,
    *,
    target_round: str,
    dependencies: dict[str, tuple[str, str]],
    ordered_ids: list[str],
) -> None:
    if ROUND_NAMES.get(match_id) == target_round:
        ordered_ids.append(match_id)
        return
    for source_match_id in dependencies.get(match_id, ()):
        _collect_visual_round_match_ids(
            source_match_id,
            target_round=target_round,
            dependencies=dependencies,
            ordered_ids=ordered_ids,
        )


def _bracket_timeline_steps(
    matches: list[KnockoutBracketMatch],
    *,
    matches_by_number: dict[int, KnockoutBracketMatch],
    visible_round_index: dict[str, int],
    round_match_numbers: dict[str, list[int]],
    initial_winners: dict[str, BracketTeam],
    prediction_lookup: dict[str, dict[str, Any]],
    simulation_forecast_lookup: dict[int, dict[str, Any]],
    team_ratings: dict[str, float],
    country_registry: CountryRegistry,
    locale: str,
    catalog: TranslationCatalog,
    contestants: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    winners = dict(initial_winners)
    simulated_matches = {match.match_number for match in matches if match.result is not None}
    current_round_index = 0
    steps = []
    for bracket_match in matches:
        round_name = ROUND_NAMES.get(f"M{bracket_match.match_number}")
        if bracket_match.result is not None or round_name not in visible_round_index:
            continue
        forecast_row = simulation_forecast_lookup.get(bracket_match.match_number)
        home = _bracket_team(bracket_match.fixture.home_team, winners, country_registry=country_registry, locale=locale)
        away = _bracket_team(bracket_match.fixture.away_team, winners, country_registry=country_registry, locale=locale)
        prediction_row = _bracket_prediction_row(bracket_match.fixture, home, away, prediction_lookup)
        projection = (
            _forecast_bracket_projection(
                forecast_row,
                country_registry=country_registry,
                locale=locale,
                catalog=catalog,
            )
            if _should_use_simulation_forecast(forecast_row, prediction_row, home, away)
            else _bracket_projection(
                bracket_match,
                home=home,
                away=away,
                prediction_row=prediction_row,
                team_ratings=team_ratings,
                catalog=catalog,
            )
        )
        if projection.winner is not None:
            winners[f"M{bracket_match.match_number}"] = projection.winner
        simulated_matches.add(bracket_match.match_number)
        updates = [
            _bracket_match_payload(
                bracket_match,
                round_name=round_name or "",
                projection=projection,
                round_index=visible_round_index,
                round_match_numbers=round_match_numbers,
            )
        ]
        _add_bracket_contestants(contestants, projection.home, projection.away)
        for dependent in _bracket_dependent_matches(matches_by_number, bracket_match.match_number):
            dependent_round = ROUND_NAMES.get(f"M{dependent.match_number}")
            if dependent_round not in visible_round_index or dependent.match_number in simulated_matches:
                continue
            dependent_projection = _current_bracket_projection(
                dependent,
                home=_bracket_team(dependent.fixture.home_team, winners, country_registry=country_registry, locale=locale),
                away=_bracket_team(dependent.fixture.away_team, winners, country_registry=country_registry, locale=locale),
                catalog=catalog,
            )
            _add_bracket_contestants(contestants, dependent_projection.home, dependent_projection.away)
            updates.append(
                _bracket_match_payload(
                    dependent,
                    round_name=dependent_round,
                    projection=dependent_projection,
                    round_index=visible_round_index,
                    round_match_numbers=round_match_numbers,
                )
            )
        round_index = visible_round_index[round_name]
        step = {
            "matchLabel": f"M{bracket_match.match_number}",
            "winner": projection.winner.id if projection.winner else "",
            "matches": updates,
        }
        if round_index > current_round_index:
            step["preAdvanceToRoundIndex"] = round_index
            current_round_index = round_index
        steps.append(step)
    return steps


def _bracket_simulation_forecast_lookup(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    lookup = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        match_number = _simulation_forecast_match_number(row)
        if match_number is None or ROUND_NAMES.get(f"M{match_number}") not in KNOCKOUT_BRACKET_ROUNDS:
            continue
        lookup.setdefault(match_number, row)
    return lookup


def _should_use_simulation_forecast(
    row: dict[str, Any] | None,
    prediction_row: dict[str, Any] | None,
    home: BracketTeam,
    away: BracketTeam,
) -> bool:
    if not row:
        return False
    if _most_likely_score_tip(prediction_row or {}) is not None:
        return False
    return _simulation_forecast_teams_match(row, home=home, away=away)


def _simulation_forecast_teams_match(row: dict[str, Any], *, home: BracketTeam, away: BracketTeam) -> bool:
    home_team = str(row.get("home_team") or "")
    away_team = str(row.get("away_team") or "")
    return bool(
        home_team
        and away_team
        and _bracket_team_name_matches(home, home_team)
        and _bracket_team_name_matches(away, away_team)
    )


def _simulation_forecast_match_number(row: dict[str, Any]) -> int | None:
    match_id = str(row.get("match_id") or "")
    if match_id.startswith("M"):
        return _optional_int(match_id[1:])
    return _optional_int(row.get("match_number"))


def _forecast_bracket_projection(
    row: dict[str, Any],
    *,
    country_registry: CountryRegistry,
    locale: str,
    catalog: TranslationCatalog,
) -> BracketProjection:
    home = _forecast_bracket_team(str(row.get("home_team") or ""), country_registry=country_registry, locale=locale)
    away = _forecast_bracket_team(str(row.get("away_team") or ""), country_registry=country_registry, locale=locale)
    home_score = _optional_int(row.get("home_score"))
    away_score = _optional_int(row.get("away_score"))
    if home_score is None or away_score is None:
        home_score, away_score = _parse_score_text(str(row.get("score") or ""))
    score = ScoreTip(home_score, away_score) if home_score is not None and away_score is not None else None
    winner = _forecast_bracket_winner(str(row.get("winner") or ""), home=home, away=away)
    if winner is None and score is not None:
        winner = home if score.home >= score.away else away
    return BracketProjection(
        home=home,
        away=away,
        score=score,
        winner=winner,
        status=_bracket_winner_status(winner, catalog=catalog, fallback_key="tournament.bracket.status_open"),
    )


def _forecast_bracket_team(
    name: str,
    *,
    country_registry: CountryRegistry,
    locale: str,
) -> BracketTeam:
    resolved = country_registry.resolve(name, locale="en") or country_registry.resolve(name, locale="de")
    if resolved and resolved.canonical_id in country_registry.countries:
        ref = TeamRef(name, resolved.canonical_id)
    else:
        ref = TeamRef(name)
    display = _bracket_team_display(ref, country_registry=country_registry, locale=locale)
    return BracketTeam(id=display["id"], label=display["label"], ref=ref)


def _forecast_bracket_winner(winner: str, *, home: BracketTeam, away: BracketTeam) -> BracketTeam | None:
    if _bracket_team_name_matches(home, winner):
        return home
    if _bracket_team_name_matches(away, winner):
        return away
    return None


def _bracket_team_name_matches(team: BracketTeam, value: str) -> bool:
    normalized = _rating_key(value)
    return normalized in {
        _rating_key(team.id),
        _rating_key(team.label),
        _rating_key(team.ref.name),
        _rating_key(team.ref.fifa_code or ""),
    }


def _parse_score_text(value: str) -> tuple[int | None, int | None]:
    if ":" not in value:
        return None, None
    home, away = value.split(":", 1)
    return _optional_int(home.strip()), _optional_int(away.strip())


def _current_bracket_projection(
    bracket_match: KnockoutBracketMatch,
    *,
    home: BracketTeam,
    away: BracketTeam,
    catalog: TranslationCatalog,
) -> BracketProjection:
    result = bracket_match.result
    if result is None:
        return BracketProjection(
            home=home,
            away=away,
            score=None,
            winner=None,
            status=catalog.translate("tournament.bracket.status_open"),
        )
    home_score = _result_score_for_team(result, bracket_match.fixture.home_team)
    away_score = _result_score_for_team(result, bracket_match.fixture.away_team)
    score = ScoreTip(home_score, away_score) if home_score is not None and away_score is not None else None
    winner = _result_winner(result, bracket_match.fixture, home=home, away=away)
    return BracketProjection(
        home=home,
        away=away,
        score=score,
        winner=winner,
        status=_bracket_winner_status(winner, catalog=catalog, fallback_key="tournament.bracket.status_final"),
    )


def _bracket_match_payload(
    bracket_match: KnockoutBracketMatch,
    *,
    round_name: str,
    projection: BracketProjection,
    round_index: dict[str, int],
    round_match_numbers: dict[str, list[int]],
) -> dict[str, Any]:
    return {
        "roundIndex": round_index[round_name],
        "order": round_match_numbers[round_name].index(bracket_match.match_number),
        "matchStatus": projection.status,
        "matchLabel": f"M{bracket_match.match_number}",
        "sides": [
            _bracket_side(
                projection.home.id,
                score=projection.score.home if projection.score else None,
                is_winner=projection.winner == projection.home,
            ),
            _bracket_side(
                projection.away.id,
                score=projection.score.away if projection.score else None,
                is_winner=projection.winner == projection.away,
            ),
        ],
    }


def _bracket_fallback_row(
    projection: BracketProjection,
    *,
    round_name: str,
    catalog: TranslationCatalog,
) -> dict[str, str]:
    return {
        "round": catalog.translate(KNOCKOUT_ROUND_STAGE_KEYS[round_name]),
        "match": f"{projection.home.label} - {projection.away.label}",
        "status": projection.status,
        "score": _score_text(
            projection.score.home if projection.score else None,
            projection.score.away if projection.score else None,
        ),
    }


def _bracket_dependent_matches(
    matches_by_number: dict[int, KnockoutBracketMatch],
    match_number: int,
) -> list[KnockoutBracketMatch]:
    dependencies = []
    for candidate in matches_by_number.values():
        home_source = _slot_source_match_number(candidate.fixture.home_team)
        away_source = _slot_source_match_number(candidate.fixture.away_team)
        if match_number in {home_source, away_source}:
            dependencies.append(candidate)
    return sorted(dependencies, key=lambda match: match.match_number)


def _slot_source_match_number(team: TeamRef) -> int | None:
    slot_code = canonical_slot_code(team.key) or canonical_slot_code(team.name)
    if slot_code and slot_code.startswith("W") and slot_code[1:].isdigit():
        return int(slot_code[1:])
    return None


def _add_bracket_contestants(contestants: dict[str, dict[str, Any]], *teams: BracketTeam) -> None:
    for team in teams:
        contestants[team.id] = {"players": [{"title": team.label}]}


def _bracket_team(
    team: TeamRef,
    projected_winners: dict[str, BracketTeam],
    *,
    country_registry: CountryRegistry,
    locale: str,
) -> BracketTeam:
    source_number = _slot_source_match_number(team)
    source_match = f"M{source_number}" if source_number is not None else ""
    if source_match and source_match in projected_winners:
        return projected_winners[source_match]
    display = _bracket_team_display(team, country_registry=country_registry, locale=locale)
    return BracketTeam(id=display["id"], label=display["label"], ref=team)


def _bracket_projection(
    bracket_match: KnockoutBracketMatch,
    *,
    home: BracketTeam,
    away: BracketTeam,
    prediction_row: dict[str, Any] | None,
    team_ratings: dict[str, float],
    catalog: TranslationCatalog,
) -> BracketProjection:
    result = bracket_match.result
    if result is not None:
        home_score = _result_score_for_team(result, bracket_match.fixture.home_team)
        away_score = _result_score_for_team(result, bracket_match.fixture.away_team)
        score = ScoreTip(home_score, away_score) if home_score is not None and away_score is not None else None
        winner = _result_winner(result, bracket_match.fixture, home=home, away=away)
        return BracketProjection(
            home=home,
            away=away,
            score=score,
            winner=winner,
            status=_bracket_winner_status(winner, catalog=catalog, fallback_key="tournament.bracket.status_final"),
        )

    score = _most_likely_score_tip(prediction_row or {})
    winner_side = _forecast_winner_side(prediction_row, score) if prediction_row and score else None
    if winner_side is None:
        score = score or _fallback_bracket_score(home, away, team_ratings)
        winner_side = _rating_winner_side(home, away, team_ratings)
    winner = _winner_from_side(winner_side, home=home, away=away)
    return BracketProjection(
        home=home,
        away=away,
        score=score,
        winner=winner,
        status=_bracket_winner_status(winner, catalog=catalog, fallback_key="tournament.bracket.status_open"),
    )


def _bracket_winner_status(
    winner: BracketTeam | None,
    *,
    catalog: TranslationCatalog,
    fallback_key: str,
) -> str:
    if winner is not None:
        return winner.label
    return catalog.translate(fallback_key)


def _winner_from_side(side: str | None, *, home: BracketTeam, away: BracketTeam) -> BracketTeam | None:
    if side == "home":
        return home
    if side == "away":
        return away
    return None


def _result_winner(
    result: ResultRecord | None,
    fixture: FixtureRecord,
    *,
    home: BracketTeam,
    away: BracketTeam,
) -> BracketTeam | None:
    winner_key = _result_winner_key(result)
    if not winner_key:
        return None
    if _team_key_matches(fixture.home_team, winner_key):
        return home
    if _team_key_matches(fixture.away_team, winner_key):
        return away
    return None


def _forecast_winner_side(row: dict[str, Any] | None, score: ScoreTip | None) -> str | None:
    if score is None:
        return None
    if score.home > score.away:
        return "home"
    if score.away > score.home:
        return "away"
    probabilities = _forecast_advancement_probabilities(row)
    home_probability = probabilities.get("home")
    away_probability = probabilities.get("away")
    if home_probability is None or away_probability is None:
        return None
    return "home" if home_probability >= away_probability else "away"


def _forecast_advancement_probabilities(row: dict[str, Any] | None) -> dict[str, float | None]:
    metadata = row.get("metadata") if row else {}
    if not isinstance(metadata, dict):
        metadata = {}
    prediction_metadata = metadata.get("prediction_metadata")
    if not isinstance(prediction_metadata, dict):
        current_metadata = metadata.get("current_prediction_ledger_metadata")
        prediction_metadata = current_metadata.get("prediction_metadata") if isinstance(current_metadata, dict) else {}
    advancement = prediction_metadata.get("advancement_probabilities") if isinstance(prediction_metadata, dict) else {}
    if not isinstance(advancement, dict):
        return {"home": None, "away": None}
    return {
        "home": _optional_float(advancement.get("home")),
        "away": _optional_float(advancement.get("away")),
    }


def _fallback_bracket_score(home: BracketTeam, away: BracketTeam, team_ratings: dict[str, float]) -> ScoreTip:
    home_rating = _bracket_team_rating(home, team_ratings)
    away_rating = _bracket_team_rating(away, team_ratings)
    if home_rating is None or away_rating is None:
        return ScoreTip(1, 1)
    difference = home_rating - away_rating
    if abs(difference) < 75:
        return ScoreTip(1, 1)
    if difference > 175:
        return ScoreTip(2, 0)
    if difference > 0:
        return ScoreTip(1, 0)
    if difference < -175:
        return ScoreTip(0, 2)
    return ScoreTip(0, 1)


def _rating_winner_side(home: BracketTeam, away: BracketTeam, team_ratings: dict[str, float]) -> str:
    home_rating = _bracket_team_rating(home, team_ratings)
    away_rating = _bracket_team_rating(away, team_ratings)
    if home_rating is None and away_rating is None:
        return "home" if home.id <= away.id else "away"
    if away_rating is None:
        return "home"
    if home_rating is None:
        return "away"
    return "home" if home_rating >= away_rating else "away"


def _bracket_prediction_lookup(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row in rows:
        if _most_likely_score_tip(row) is None:
            continue
        event_date = normalize_datetime(str(row.get("event_date") or "")) or str(row.get("event_date") or "")
        home_team = str(row.get("home_team") or "")
        away_team = str(row.get("away_team") or "")
        home_code = str(row.get("home_fifa_code") or "")
        away_code = str(row.get("away_fifa_code") or "")
        for key in (
            str(row.get("fixture_key") or ""),
            _bracket_prediction_key(event_date, home_team, away_team),
            _bracket_prediction_key(event_date, home_code, away_code),
        ):
            if key:
                lookup[key] = row
    return lookup


def _bracket_prediction_row(
    fixture: FixtureRecord,
    home: BracketTeam,
    away: BracketTeam,
    lookup: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    event_date = normalize_datetime(fixture.event_date) or fixture.event_date
    candidates = (
        fixture.key,
        _bracket_prediction_key(event_date, fixture.home_team.key, fixture.away_team.key),
        _bracket_prediction_key(event_date, fixture.home_team.name, fixture.away_team.name),
        _bracket_prediction_key(event_date, home.ref.key, away.ref.key),
        _bracket_prediction_key(event_date, home.ref.name, away.ref.name),
        _bracket_prediction_key(event_date, home.id, away.id),
    )
    return next((lookup[key] for key in candidates if key in lookup), None)


def _bracket_prediction_key(event_date: str, home: str, away: str) -> str:
    if not event_date or not home or not away:
        return ""
    return f"{event_date}|{home}|{away}"


def _bracket_team_ratings(rows: list[dict[str, Any]], *, country_registry: CountryRegistry) -> dict[str, float]:
    ratings: dict[str, float] = {}
    for row in rows:
        features = _forecast_features(row)
        for side in ("home", "away"):
            rating = _optional_float(features.get(f"{side}_rating"))
            if rating is None:
                continue
            name = str(row.get(f"{side}_team") or "")
            code = str(row.get(f"{side}_fifa_code") or "")
            resolved = country_registry.resolve(name, locale="en") or country_registry.resolve(name, locale="de")
            for key in (name, code, resolved.canonical_id if resolved else ""):
                if key:
                    ratings[_rating_key(key)] = rating
    return ratings


def _forecast_features(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    prediction_metadata = metadata.get("prediction_metadata")
    if not isinstance(prediction_metadata, dict):
        current_metadata = metadata.get("current_prediction_ledger_metadata")
        prediction_metadata = current_metadata.get("prediction_metadata") if isinstance(current_metadata, dict) else {}
    features = prediction_metadata.get("features") if isinstance(prediction_metadata, dict) else {}
    return features if isinstance(features, dict) else {}


def _bracket_team_rating(team: BracketTeam, ratings: dict[str, float]) -> float | None:
    for key in (team.id, team.ref.fifa_code or "", team.ref.key, team.ref.name, team.label):
        rating = ratings.get(_rating_key(key))
        if rating is not None:
            return rating
    return None


def _rating_key(value: str) -> str:
    return normalize_entity_text(str(value or "")).casefold()


def _bracket_side(contestant_id: str, *, score: int | None, is_winner: bool) -> dict[str, Any]:
    side: dict[str, Any] = {"contestantId": contestant_id}
    if score is not None:
        side["scores"] = [{"mainScore": score, "isWinner": is_winner}]
    if is_winner:
        side["isWinner"] = True
    return side


def _bracket_team_display(
    team: TeamRef,
    *,
    country_registry: CountryRegistry,
    locale: str,
) -> dict[str, str]:
    slot_label = slot_display_name(team.key, locale=locale) or slot_display_name(team.name, locale=locale)
    if slot_label:
        code = canonical_slot_code(team.key) or canonical_slot_code(team.name)
        return {"id": code or f"slot:{slot_label}", "label": code or slot_label}
    if team.fifa_code and team.fifa_code in country_registry.countries:
        country = country_registry.countries[team.fifa_code]
        label = country.names.get(locale) or country.names.get("en") or team.name
        return {"id": team.fifa_code, "label": _flagged_label(FIFA_FLAG_EMOJIS.get(team.fifa_code, ""), label)}
    resolved = country_registry.resolve(team.name, locale="en") or country_registry.resolve(team.name, locale="de")
    if resolved and resolved.canonical_id in country_registry.countries:
        country = country_registry.countries[resolved.canonical_id]
        label = country.names.get(locale) or country.names.get("en") or team.name
        return {"id": resolved.canonical_id, "label": _flagged_label(FIFA_FLAG_EMOJIS.get(resolved.canonical_id, ""), label)}
    return {"id": f"name:{team.name}", "label": team.name}


def _knockout_match_number(row: FixtureRecord | ResultRecord) -> int | None:
    match_number = _optional_int(row.metadata.get("match_number"))
    if match_number is None and isinstance(row, FixtureRecord):
        match_number = _optional_int(row.source_id)
    if match_number is None or ROUND_NAMES.get(f"M{match_number}") not in KNOCKOUT_BRACKET_ROUNDS:
        return None
    return match_number


def _bracket_fixture_source_priority(fixture: FixtureRecord) -> tuple[int, int, str]:
    metadata = fixture.metadata or {}
    source = str(metadata.get("source") or "")
    source_priority = 3 if source == "fifa_match_centre" else 2 if "football_data" in source else 1
    officiality = _optional_int(metadata.get("officiality_status")) or 0
    return (source_priority, officiality, normalize_datetime(fixture.event_date) or fixture.event_date)


def _bracket_result_source_priority(result: ResultRecord) -> tuple[int, str]:
    source = str(result.source or "")
    source_priority = 3 if source == "fifa_match_centre" else 2 if "football_data" in source else 1
    return (source_priority, normalize_datetime(result.event_date) or result.event_date)


def _result_score_for_team(result: ResultRecord | None, team: TeamRef) -> int | None:
    if result is None:
        return None
    if _team_key_matches(team, result.home_team.key) or _team_key_matches(team, result.home_team.name):
        return result.score.home
    if _team_key_matches(team, result.away_team.key) or _team_key_matches(team, result.away_team.name):
        return result.score.away
    return None


def _result_winner_key(result: ResultRecord | None) -> str | None:
    if result is None:
        return None
    if result.score.home > result.score.away:
        return result.home_team.key
    if result.score.away > result.score.home:
        return result.away_team.key
    home_penalty = _optional_int(result.metadata.get("home_penalty_score"))
    away_penalty = _optional_int(result.metadata.get("away_penalty_score"))
    if home_penalty is not None and away_penalty is not None:
        if home_penalty > away_penalty:
            return result.home_team.key
        if away_penalty > home_penalty:
            return result.away_team.key
    return None


def _team_key_matches(team: TeamRef, value: str) -> bool:
    candidates = {team.key, team.name}
    if team.fifa_code:
        candidates.add(team.fifa_code)
    return value in candidates


def _score_text(home_score: int | None, away_score: int | None) -> str:
    if home_score is None or away_score is None:
        return ""
    return f"{home_score}:{away_score}"


def _simulation_champion_odds(
    storage,
    *,
    country_registry: CountryRegistry,
    active_team_codes: set[str],
) -> dict[str, Any] | None:
    latest = None
    for row in storage.read_records(SIMULATION_SUMMARY, latest_only=True):
        observed = str((row.get("_record") or {}).get("observed_at_utc") or "")
        if latest is None or observed > latest[0]:
            latest = (observed, row)
    if latest is None:
        return None
    payload = latest[1]
    distributions = payload.get("distributions") if isinstance(payload.get("distributions"), dict) else {}
    champion = distributions.get("champion")
    if not isinstance(champion, list):
        return None
    entries = [
        {"name": str(entry.get("answer") or ""), "probability": _float_value(entry.get("probability"))}
        for entry in champion
        if isinstance(entry, dict) and _float_value(entry.get("probability")) > 0
    ]
    entries.sort(key=lambda entry: entry["probability"], reverse=True)
    entries = _active_champion_entries(entries, country_registry=country_registry, active_team_codes=active_team_codes)
    if len(entries) < 2:
        # A single-answer distribution means the simulator output is degenerate
        # (or the tournament is decided); market odds are more honest then.
        return None
    return {
        "source": "simulation",
        "iterations": _optional_int(payload.get("iterations")) or 0,
        "as_of": latest[0],
        "entries": entries,
        "forecast_results": _simulation_forecast_results(payload),
    }


def _market_champion_odds(
    storage,
    *,
    country_registry: CountryRegistry,
    active_team_codes: set[str],
) -> dict[str, Any] | None:
    latest_by_team: dict[str, tuple[str, float]] = {}
    for row in storage.read_records(MARKET_OUTRIGHTS, latest_only=True):
        team = str(row.get("team") or "")
        probability = _float_value(row.get("fair_probability") or row.get("avg_implied_probability"))
        observed = str((row.get("_record") or {}).get("observed_at_utc") or row.get("observed_at_utc") or "")
        if not team or probability <= 0:
            continue
        current = latest_by_team.get(team)
        if current is None or observed > current[0]:
            latest_by_team[team] = (observed, probability)
    if not latest_by_team:
        return None
    entries = [
        {"name": team, "probability": probability}
        for team, (_observed, probability) in latest_by_team.items()
    ]
    entries.sort(key=lambda entry: entry["probability"], reverse=True)
    entries = _active_champion_entries(entries, country_registry=country_registry, active_team_codes=active_team_codes)
    if not entries:
        return None
    as_of = max(observed for observed, _probability in latest_by_team.values())
    return {
        "source": "market",
        "iterations": 0,
        "as_of": as_of,
        "entries": entries,
    }


def _simulation_forecast_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return []
    rows = metadata.get("forecast_results")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _active_champion_team_codes(storage, *, country_registry: CountryRegistry) -> set[str]:
    state = load_tournament_state(storage)
    codes: set[str] = set()
    for fixture in state.fixtures_without_results():
        for team in (fixture.home_team, fixture.away_team):
            code = _team_country_code(team, country_registry=country_registry)
            if code:
                codes.add(code)
    return codes


def _active_champion_entries(
    entries: list[dict[str, Any]],
    *,
    country_registry: CountryRegistry,
    active_team_codes: set[str],
) -> list[dict[str, Any]]:
    if not active_team_codes:
        return entries
    return [
        entry
        for entry in entries
        if _champion_entry_country_code(entry, country_registry=country_registry) in active_team_codes
    ]


def _champion_entry_country_code(entry: dict[str, Any], *, country_registry: CountryRegistry) -> str | None:
    name = str(entry.get("name") or "")
    resolved = country_registry.resolve(name, locale="en") or country_registry.resolve(name, locale="de")
    if resolved and resolved.canonical_id in country_registry.countries:
        return resolved.canonical_id
    return None


def _team_country_code(team: TeamRef, *, country_registry: CountryRegistry) -> str | None:
    if canonical_slot_code(team.fifa_code) or canonical_slot_code(team.key) or canonical_slot_code(team.name):
        return None
    if team.fifa_code and team.fifa_code in country_registry.countries:
        return team.fifa_code
    resolved = country_registry.resolve(team.name, locale="en") or country_registry.resolve(team.name, locale="de")
    if resolved and resolved.canonical_id in country_registry.countries:
        return resolved.canonical_id
    return None


def _localized_champion_odds(
    champion_odds: dict[str, Any] | None,
    *,
    country_registry: CountryRegistry,
    locale: str,
    catalog: TranslationCatalog,
) -> dict[str, Any] | None:
    if not champion_odds or not champion_odds.get("entries"):
        return None
    entries = champion_odds["entries"]
    peak = max(_float_value(entry.get("probability")) for entry in entries)
    if peak <= 0:
        return None
    localized_entries = []
    for entry in entries:
        name = str(entry.get("name") or "")
        probability = _float_value(entry.get("probability"))
        code = None
        resolved = country_registry.resolve(name, locale="en") or country_registry.resolve(name, locale="de")
        if resolved and resolved.canonical_id in country_registry.countries:
            code = resolved.canonical_id
        country = country_registry.countries.get(code) if code else None
        label = (country.names.get(locale) or country.names.get("en")) if country else name
        localized_entries.append(
            {
                "label": label,
                "flag": FIFA_FLAG_EMOJIS.get(code or "", ""),
                "code": code or "",
                "probability": probability,
                "percent_text": _champion_percent_text(probability),
                "width": f"{100 * probability / peak:.2f}",
            }
        )
    as_of_display = _date_text(champion_odds.get("as_of"))
    if champion_odds.get("source") == "simulation":
        source_text = catalog.translate(
            "tournament.source_simulation",
            iterations=f"{int(champion_odds.get('iterations') or 0):,}".replace(",", "'"),
            date=as_of_display,
        )
    else:
        source_text = catalog.translate("tournament.source_market", date=as_of_display)
    return {
        "entries": localized_entries,
        "preview_entries": localized_entries[:HOMEPAGE_CHAMPION_PREVIEW_LIMIT],
        "source_text": source_text,
        "source": champion_odds.get("source"),
    }


def _add_alternate_links(locale: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        slug = _match_slug(row)
        row["detail_path"] = _detail_path(locale, slug)
        row["current_url"] = row["detail_path"] + "/"
        row["alternate_links"] = _alternate_links(f"/{LOCALE_DETAIL_PREFIX[DEFAULT_SITE_LOCALE]}/{slug}", slug=slug)
        row["language_switch_links"] = _language_switch_links(locale, slug)


# Venue names differ per source language: SRF publishes German stadium names,
# the FIFA match centre English ones.
_VENUE_SOURCE_PREFERENCE: dict[str, tuple[str, ...]] = {
    "de": ("srf_public", "fifa_match_centre", "openfootball"),
    "en": ("fifa_match_centre", "openfootball", "srf_public"),
}


def _attach_fixture_context(storage, rows: list[dict[str, Any]]) -> None:
    """Attach per-source venue names and match-window weather to raw rows."""

    venues: dict[str, dict[str, str]] = {}
    weather: dict[str, dict[str, Any]] = {}
    try:
        fixture_rows = storage.read_records(TOURNAMENT_FIXTURES, latest_only=True)
    except Exception:
        fixture_rows = []
    for fixture_row in fixture_rows:
        venue = str(fixture_row.get("venue") or "").strip()
        fixture_key = str(fixture_row.get("fixture_key") or "")
        if not venue or not fixture_key:
            continue
        record = fixture_row.get("_record") or {}
        source_family = str(record.get("source") or "").split(":", 1)[0].split("/", 1)[0]
        if source_family:
            venues.setdefault(fixture_key, {})[source_family] = venue
    try:
        weather_rows = storage.read_records(WEATHER_OBSERVATIONS, latest_only=True)
    except Exception:
        weather_rows = []
    for weather_row in weather_rows:
        fixture_key = str(weather_row.get("fixture_key") or "")
        if fixture_key:
            weather[fixture_key] = weather_row
    for row in rows:
        fixture_key = str(row.get("fixture_key") or "")
        if fixture_key in venues:
            row["venue_by_source"] = venues[fixture_key]
        if fixture_key in weather:
            row["weather"] = weather[fixture_key]


def _venue_display(prepared: dict[str, Any], *, locale: str) -> str:
    venues = prepared.get("venue_by_source") or {}
    if not isinstance(venues, dict) or not venues:
        return ""
    for source in _VENUE_SOURCE_PREFERENCE.get(locale, _VENUE_SOURCE_PREFERENCE["en"]):
        if venues.get(source):
            return str(venues[source])
    return str(next(iter(venues.values())))


def _weather_text(prepared: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    """Compact forecast for upcoming matches, e.g. "29 °C, Regen 45%"."""

    if prepared.get("status") != "future":
        return ""
    observation = prepared.get("weather") or {}
    if not isinstance(observation, dict):
        return ""
    temperature = observation.get("temperature_max_c")
    if temperature is None:
        return ""
    text = f"{round(float(temperature))} °C"
    rain_probability = observation.get("precipitation_probability_max_pct")
    if rain_probability is not None:
        text += f", {catalog.translate('label.rain')} {round(float(rain_probability))}%"
    return text


def _detail_path(locale: str, slug: str) -> str:
    return f"/{locale}/{LOCALE_DETAIL_PREFIX[locale]}/{slug}"


def _alternate_links(default_locale_path: str, *, slug: str | None = None) -> list[dict[str, str]]:
    links = []
    for locale in SITE_LOCALES:
        href = f"/{locale}/" if slug is None else _detail_path(locale, slug) + "/"
        links.append({"locale": locale, "href": href})
    x_default = f"/{DEFAULT_SITE_LOCALE}{default_locale_path}/" if default_locale_path else f"/{DEFAULT_SITE_LOCALE}/"
    links.append({"locale": "x-default", "href": x_default})
    return links


def _match_list_alternate_links(kind: str) -> list[dict[str, str]]:
    links = [{"locale": locale, "href": LOCALE_MATCH_LIST_PATHS[locale][kind]} for locale in SITE_LOCALES]
    links.append({"locale": "x-default", "href": LOCALE_MATCH_LIST_PATHS[DEFAULT_SITE_LOCALE][kind]})
    return links


def _language_switch_links(current_locale: str, slug: str | None) -> list[dict[str, Any]]:
    return [
        {
            "locale": locale,
            "label": locale.upper(),
            "href": f"/{locale}/" if slug in (None, "") else _detail_path(locale, slug) + "/",
            "current": locale == current_locale,
        }
        for locale in SITE_LOCALES
    ]


def _match_list_language_switch_links(current_locale: str, kind: str) -> list[dict[str, Any]]:
    return [
        {
            "locale": locale,
            "label": locale.upper(),
            "href": LOCALE_MATCH_LIST_PATHS[locale][kind],
            "current": locale == current_locale,
        }
        for locale in SITE_LOCALES
    ]


def _html_files(localized_contexts: dict[str, dict[str, Any]]) -> list[str]:
    html_files = ["index.html"]
    for locale, context in localized_contexts.items():
        html_files.append(f"{locale}/index.html")
        html_files.extend(f"{LOCALE_MATCH_LIST_PATHS[locale][kind].lstrip('/')}/index.html" for kind in ("future", "past"))
        html_files.append(f"{LOCALE_TOURNAMENT_PATHS[locale].lstrip('/')}/index.html")
        html_files.append(f"{LEGACY_TOURNAMENT_PATHS[locale].lstrip('/')}/index.html")
        html_files.extend(f"{str(row['detail_path']).lstrip('/')}/index.html" for row in context["rows"])
    return html_files


def _public_api_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at_utc": payload["generated_at_utc"],
        "summary": _public_api_value(payload["summary"]),
        "predictions": [_public_api_row(row) for row in payload["predictions"]],
    }


def _public_api_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        _public_api_key(key): _public_api_value(value)
        for key, value in row.items()
        if key not in API_PRESENTATION_KEYS
    }


def _public_api_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _public_api_key(str(key)): _public_api_value(item)
            for key, item in value.items()
            if str(key) not in API_PRESENTATION_KEYS
        }
    if isinstance(value, list):
        return [_public_api_value(item) for item in value]
    return value


def _public_api_key(key: str) -> str:
    if key.startswith("twenty_min_"):
        return "20min_" + key.removeprefix("twenty_min_")
    return key


def _api_rows(rows: list[dict[str, Any]], *, country_registry: CountryRegistry, base_url: str) -> list[dict[str, Any]]:
    prepared = [_prepare_api_row(row, country_registry=country_registry, base_url=base_url) for row in rows]
    prepared.sort(key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")))
    return prepared


def _prepare_api_row(row: dict[str, Any], *, country_registry: CountryRegistry, base_url: str) -> dict[str, Any]:
    prepared = {key: value for key, value in _strip_record(row).items() if key != "_record"}
    slug = _match_slug(prepared)
    prepared["home_team"] = _api_team_value(prepared, side="home", country_registry=country_registry)
    prepared["away_team"] = _api_team_value(prepared, side="away", country_registry=country_registry)
    prepared["home_team_id"] = _api_team_identifier(prepared, side="home", country_registry=country_registry)
    prepared["away_team_id"] = _api_team_identifier(prepared, side="away", country_registry=country_registry)
    prepared["home_fifa_code"] = _api_fifa_code(prepared, side="home", country_registry=country_registry)
    prepared["away_fifa_code"] = _api_fifa_code(prepared, side="away", country_registry=country_registry)
    prepared["detail_urls"] = {
        locale: _absolute_site_url(_detail_path(locale, slug) + "/", base_url=base_url)
        for locale in SITE_LOCALES
    }
    return prepared


def _api_team_value(row: dict[str, Any], *, side: str, country_registry: CountryRegistry) -> str:
    fixture_key_part = _fixture_key_part(str(row.get("fixture_key") or ""), side=side)
    slot_code = canonical_slot_code(fixture_key_part)
    if slot_code:
        return slot_code
    if fixture_key_part and len(fixture_key_part) == 3:
        country = country_registry.countries.get(fixture_key_part)
        if country is not None:
            return country.names.get("en") or fixture_key_part
    source_name = str(row.get(f"{side}_team") or "")
    source_slot_code = canonical_slot_code(source_name)
    if source_slot_code:
        return source_slot_code
    resolved = country_registry.resolve(source_name, locale="en") or country_registry.resolve(source_name, locale="de")
    if resolved and resolved.canonical_id:
        country = country_registry.countries.get(resolved.canonical_id)
        if country is not None:
            return country.names.get("en") or resolved.canonical_id
    return source_name


def _api_team_identifier(row: dict[str, Any], *, side: str, country_registry: CountryRegistry) -> str:
    fixture_key_part = _fixture_key_part(str(row.get("fixture_key") or ""), side=side)
    if fixture_key_part:
        return canonical_slot_code(fixture_key_part) or fixture_key_part
    return _api_fifa_code(row, side=side, country_registry=country_registry) or str(row.get(f"{side}_team") or "")


def _api_fifa_code(row: dict[str, Any], *, side: str, country_registry: CountryRegistry) -> str | None:
    raw_code = str(row.get(f"{side}_fifa_code") or "").strip().upper()
    if raw_code and raw_code in country_registry.countries:
        return raw_code
    fixture_key_part = _fixture_key_part(str(row.get("fixture_key") or ""), side=side)
    if fixture_key_part and fixture_key_part in country_registry.countries:
        return fixture_key_part
    resolved = country_registry.resolve(str(row.get(f"{side}_team") or ""), locale="en") or country_registry.resolve(
        str(row.get(f"{side}_team") or ""),
        locale="de",
    )
    if resolved and resolved.canonical_id in country_registry.countries:
        return resolved.canonical_id
    return None


def _absolute_site_url(path: str, *, base_url: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base_url}{normalized_path}"


def normalized_base_url(value: str | None) -> str:
    """Return the site origin without a trailing slash."""

    text = str(value or "").strip() or SITE_BASE_URL
    return text.rstrip("/")


def _write_site_files(
    output_dir: Path,
    *,
    localized_contexts: dict[str, dict[str, Any]],
    css_content: str,
    js_content: str,
    asset_path: str,
    script_path: str,
    data_payload: dict[str, Any],
) -> None:
    (output_dir / "assets").mkdir(parents=True, exist_ok=True)
    (output_dir / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    (output_dir / "api").mkdir(parents=True, exist_ok=True)

    env = _template_environment()
    (output_dir / "index.html").write_text(_language_redirect_html(), encoding="utf-8")
    predictions_template = env.get_template("pages/predictions.html")
    match_list_template = env.get_template("pages/match_list.html")
    detail_template = env.get_template("pages/match_detail.html")
    tournament_template = env.get_template("pages/tournament.html")
    for locale, context in localized_contexts.items():
        locale_dir = output_dir / locale
        locale_dir.mkdir(parents=True, exist_ok=True)
        locale_dir.joinpath("index.html").write_text(predictions_template.render(**context), encoding="utf-8")
        _write_match_list_page(
            output_dir,
            template=match_list_template,
            context=context,
            kind="future",
            rows=context["future_rows"],
            title_key="section.future.title",
            description_key="section.future.description",
            seo_title_key="seo.future.title",
            seo_description_key="seo.future.description",
            empty_key="empty.future",
        )
        _write_match_list_page(
            output_dir,
            template=match_list_template,
            context=context,
            kind="past",
            rows=context["tipped_rows"],
            title_key="section.tipped.title",
            description_key="section.tipped.description",
            seo_title_key="seo.past.title",
            seo_description_key="seo.past.description",
            empty_key="empty.tipped",
        )
        _write_tournament_page(output_dir, template=tournament_template, context=context)
        _write_redirect_page(
            output_dir,
            from_path=LEGACY_TOURNAMENT_PATHS[locale],
            to_path=LOCALE_TOURNAMENT_PATHS[locale],
            title=context["t"]("tournament.title"),
            locale=locale,
        )
        for row in context["rows"]:
            detail_dir = output_dir / str(row["detail_path"]).lstrip("/")
            detail_dir.mkdir(parents=True, exist_ok=True)
            detail_context = {
                **context,
                "title": row.get("page_title") or context["title"],
                "description": row.get("meta_description")
                or row.get("og_description")
                or context["description"],
                "current_url": row["current_url"],
                "alternate_links": row["alternate_links"],
                "language_switch_links": row["language_switch_links"],
            }
            detail_context["jsonld_graph"] = _page_jsonld_graph(detail_context, page_kind="match_detail", row=row)
            detail_dir.joinpath("index.html").write_text(detail_template.render(**detail_context, row=row), encoding="utf-8")
    (output_dir / asset_path).write_text(css_content, encoding="utf-8")
    (output_dir / script_path).write_text(js_content, encoding="utf-8")
    _write_static_assets(output_dir)
    (output_dir / "api" / "predictions").write_text(
        json.dumps(data_payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n", encoding="utf-8")
    (output_dir / "sitemap.xml").write_text(_sitemap_xml(localized_contexts), encoding="utf-8")
    (output_dir / "llms.txt").write_text(_llms_txt(localized_contexts), encoding="utf-8")


def _write_match_list_page(
    output_dir: Path,
    *,
    template,
    context: dict[str, Any],
    kind: str,
    rows: list[dict[str, Any]],
    title_key: str,
    description_key: str,
    seo_title_key: str,
    seo_description_key: str,
    empty_key: str,
) -> None:
    locale = str(context["locale"])
    page_path = LOCALE_MATCH_LIST_PATHS[locale][kind]
    page_dir = output_dir / page_path.lstrip("/")
    page_dir.mkdir(parents=True, exist_ok=True)
    page_context = {
        **context,
        "title": context["t"](seo_title_key),
        "description": context["t"](seo_description_key),
        "current_url": page_path,
        "alternate_links": _match_list_alternate_links(kind),
        "language_switch_links": _match_list_language_switch_links(locale, kind),
        "rows_to_show": rows,
        "list_kind": kind,
        "page_title": context["t"](title_key),
        "page_description": context["t"](description_key),
        "empty_text": context["t"](empty_key),
    }
    page_context["jsonld_graph"] = _page_jsonld_graph(page_context, page_kind="match_list", item_rows=rows)
    page_dir.joinpath("index.html").write_text(template.render(**page_context), encoding="utf-8")


def _write_tournament_page(output_dir: Path, *, template, context: dict[str, Any]) -> None:
    locale = str(context["locale"])
    page_path = LOCALE_TOURNAMENT_PATHS[locale]
    page_dir = output_dir / page_path.lstrip("/")
    page_dir.mkdir(parents=True, exist_ok=True)
    page_context = {
        **context,
        "title": context["t"]("seo.tournament.title"),
        "description": context["t"]("seo.tournament.description"),
        "current_url": page_path,
        "alternate_links": _tournament_alternate_links(),
        "language_switch_links": _tournament_language_switch_links(locale),
        "page_title": context["t"]("tournament.title"),
        "page_description": context["t"]("tournament.lead"),
    }
    page_context["jsonld_graph"] = _page_jsonld_graph(page_context, page_kind="tournament")
    page_dir.joinpath("index.html").write_text(template.render(**page_context), encoding="utf-8")


def _write_redirect_page(output_dir: Path, *, from_path: str, to_path: str, title: str, locale: str) -> None:
    page_dir = output_dir / from_path.lstrip("/")
    page_dir.mkdir(parents=True, exist_ok=True)
    page_dir.joinpath("index.html").write_text(_static_redirect_html(to_path, title=title, locale=locale), encoding="utf-8")


def _tournament_alternate_links() -> list[dict[str, str]]:
    links = [{"locale": locale, "href": LOCALE_TOURNAMENT_PATHS[locale]} for locale in SITE_LOCALES]
    links.append({"locale": "x-default", "href": LOCALE_TOURNAMENT_PATHS[DEFAULT_SITE_LOCALE]})
    return links


def _tournament_language_switch_links(current_locale: str) -> list[dict[str, Any]]:
    return [
        {
            "locale": locale,
            "label": locale.upper(),
            "href": LOCALE_TOURNAMENT_PATHS[locale],
            "current": locale == current_locale,
        }
        for locale in SITE_LOCALES
    ]


def _language_redirect_html() -> str:
    supported = json.dumps(list(SITE_LOCALES), ensure_ascii=True)
    fallback = DEFAULT_SITE_LOCALE
    cookie = LANGUAGE_COOKIE_NAME
    catalog = load_translation_catalog(fallback)
    return f"""<!doctype html>
<html lang="{fallback}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{catalog.translate("site.title")}</title>
  <meta name="robots" content="noindex">
  <meta http-equiv="refresh" content="0; url=/{fallback}/">
  <script>
    (function () {{
      var supported = {supported};
      var fallback = "{fallback}";
      var cookieName = "{cookie}";

      function cookieLanguage() {{
        var match = document.cookie.match(new RegExp("(?:^|; )" + cookieName + "=(de|en)"));
        return match ? match[1] : "";
      }}

      function browserLanguage() {{
        var languages = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""];
        for (var i = 0; i < languages.length; i += 1) {{
          var value = String(languages[i] || "").split("-")[0].toLowerCase();
          if (supported.indexOf(value) !== -1) {{
            return value;
          }}
        }}
        return fallback;
      }}

      var locale = cookieLanguage() || browserLanguage();
      document.cookie = cookieName + "=" + locale + "; Path=/; Max-Age=31536000; SameSite=Lax";
      window.location.replace("/" + locale + "/");
    }})();
  </script>
  <link rel="alternate" hreflang="de" href="/de/">
  <link rel="alternate" hreflang="en" href="/en/">
  <link rel="alternate" hreflang="x-default" href="/en/">
</head>
<body>
  <p><a href="/{fallback}/">{catalog.translate("redirect.continue")}</a></p>
</body>
</html>
"""


def _static_redirect_html(target: str, *, title: str, locale: str) -> str:
    escaped_target = html_lib.escape(target, quote=True)
    escaped_title = html_lib.escape(title, quote=True)
    escaped_locale = html_lib.escape(locale, quote=True)
    return f"""<!doctype html>
<html lang="{escaped_locale}">
<head>
  <meta charset="utf-8">
  <meta name="robots" content="noindex">
  <meta http-equiv="refresh" content="0; url={escaped_target}">
  <title>{escaped_title}</title>
  <script>window.location.replace({json.dumps(target)});</script>
</head>
<body>
  <p><a href="{escaped_target}">{escaped_title}</a></p>
</body>
</html>
"""


def _template_environment() -> Environment:
    template_dir = files("worldcup_predictions.site").joinpath("templates")
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["score"] = _score_text
    env.filters["float_text"] = _float_text
    env.filters["percent"] = _percent_text
    env.filters["date_text"] = _date_text
    return env


def _render_css() -> str:
    css_path = files("worldcup_predictions.site").joinpath("static", "site.css")
    return (
        css_path.read_text(encoding="utf-8")
        .replace("__HELGA_FONT_CADIZ_WOFF2__", HELGA_FONT_CADIZ_WOFF2)
        .replace("__HELGA_FONT_DEGULAR_WOFF2__", HELGA_FONT_DEGULAR_WOFF2)
    )


def _render_js() -> str:
    js_path = files("worldcup_predictions.site").joinpath("static", "theme.js")
    return js_path.read_text(encoding="utf-8")


def _write_static_assets(output_dir: Path) -> None:
    static_dir = files("worldcup_predictions.site").joinpath("static")
    for asset_file in STATIC_ASSET_FILES:
        relative_path = Path(asset_file).relative_to("assets")
        target = output_dir.joinpath(asset_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(static_dir.joinpath(*relative_path.parts).read_bytes())


def _provider_point_totals(storage) -> dict[str, float]:
    totals = {"srf.ch": 0.0, "20min.ch": 0.0}
    for row in storage.read_records(PROVIDER_POINTS, latest_only=True):
        provider = str(row.get("provider") or "")
        if provider in totals:
            totals[provider] += _float_value(row.get("points"))
    return totals


def _site_provider_point_totals(
    storage,
    rows: list[dict[str, Any]],
    *,
    country_registry: CountryRegistry,
) -> dict[str, float]:
    published_totals, published_counts = _published_provider_point_totals(rows, country_registry=country_registry)
    stored_totals = _provider_point_totals(storage)
    return {
        provider: published_totals[provider] if published_counts[provider] > 0 else stored_totals[provider]
        for provider in published_totals
    }


def _site_provider_point_max_totals(
    rows: list[dict[str, Any]],
    *,
    country_registry: CountryRegistry,
) -> dict[str, float]:
    totals = {"srf.ch": 0.0, "20min.ch": 0.0}
    for row in rows:
        if row.get("status") != "final":
            continue
        fixture = _fixture_record_from_site_row(row)
        if _result_record_from_site_row(row, fixture) is None:
            continue
        if _srf_source_row(row) is not None:
            totals["srf.ch"] += _float_value(_srf_expected_points_max(row))
        if _twenty_min_source_row(row, fixture, country_registry=country_registry) is not None:
            totals["20min.ch"] += _float_value(_twenty_min_expected_points_max(row))
    return totals


def _published_provider_point_totals(
    rows: list[dict[str, Any]],
    *,
    country_registry: CountryRegistry,
) -> tuple[dict[str, float], dict[str, int]]:
    totals = {"srf.ch": 0.0, "20min.ch": 0.0}
    counts = {"srf.ch": 0, "20min.ch": 0}
    for row in rows:
        if row.get("status") != "final":
            continue
        fixture = _fixture_record_from_site_row(row)
        result = _result_record_from_site_row(row, fixture)
        if result is None:
            continue
        srf_source = _srf_source_row(row)
        if srf_source is not None:
            points, _tip_text, _source = points_for_row("srf.ch", fixture, result, srf_source)
            totals["srf.ch"] += points
            counts["srf.ch"] += 1
        twenty_min_source = _twenty_min_source_row(row, fixture, country_registry=country_registry)
        if twenty_min_source is not None:
            points, _tip_text, _source = points_for_row("20min.ch", fixture, result, twenty_min_source)
            totals["20min.ch"] += points
            counts["20min.ch"] += 1
    return totals, counts


def _apply_provider_point_accounts(rows: list[dict[str, Any]], *, country_registry: CountryRegistry) -> None:
    totals = {"srf.ch": 0.0, "20min.ch": 0.0}
    ordered_rows = sorted(rows, key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")))
    for row in ordered_rows:
        row["srf_account_points"] = None
        row["twenty_min_account_points"] = None
        row["srf_match_points"] = None
        row["twenty_min_match_points"] = None
        row["srf_account_display"] = ""
        row["twenty_min_account_display"] = ""
        if row.get("status") != "final":
            continue
        fixture = _fixture_record_from_site_row(row)
        result = _result_record_from_site_row(row, fixture)
        if result is None:
            continue
        srf_source = _srf_source_row(row)
        srf_points = 0.0
        if srf_source is not None:
            srf_points, _tip_text, _source = points_for_row("srf.ch", fixture, result, srf_source)
            totals["srf.ch"] += srf_points
        twenty_min_source = _twenty_min_source_row(row, fixture, country_registry=country_registry)
        twenty_min_points = 0.0
        if twenty_min_source is not None:
            twenty_min_points, _tip_text, _source = points_for_row("20min.ch", fixture, result, twenty_min_source)
            totals["20min.ch"] += twenty_min_points
        row["srf_account_points"] = totals["srf.ch"]
        row["twenty_min_account_points"] = totals["20min.ch"]
        row["srf_match_points"] = srf_points
        row["twenty_min_match_points"] = twenty_min_points
        row["srf_account_display"] = _account_points_text(totals["srf.ch"], srf_points)
        row["twenty_min_account_display"] = _account_points_text(totals["20min.ch"], twenty_min_points)


def _fixture_record_from_site_row(row: dict[str, Any]) -> FixtureRecord:
    fixture_key = str(row.get("fixture_key") or "")
    home_code = str(row.get("home_fifa_code") or _country_code_from_fixture_key(fixture_key, side="home") or "")
    away_code = str(row.get("away_fifa_code") or _country_code_from_fixture_key(fixture_key, side="away") or "")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return FixtureRecord(
        event_date=str(row.get("event_date") or ""),
        home_team=TeamRef(str(row.get("home_team") or ""), home_code or None),
        away_team=TeamRef(str(row.get("away_team") or ""), away_code or None),
        stage=_fixture_stage_from_site_row(row, metadata),
        group=row.get("group") or metadata.get("group"),
        matchday=_optional_int(row.get("matchday")),
        source_id=row.get("source_id"),
        venue=row.get("venue"),
        status=str(row.get("status") or "final"),
        metadata=dict(metadata),
    )


def _fixture_stage_from_site_row(row: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    stage = row.get("stage") or metadata.get("stage") or metadata.get("phase")
    current_metadata = metadata.get("current_prediction_ledger_metadata")
    if not stage and isinstance(current_metadata, dict):
        stage = current_metadata.get("stage") or current_metadata.get("phase")
    if not stage:
        stage = _knockout_round_name(row)
    return str(stage) if stage not in (None, "") else None


def _result_record_from_site_row(row: dict[str, Any], fixture: FixtureRecord) -> ResultRecord | None:
    actual = _actual_score_from_row(row)
    if actual is None:
        return None
    return ResultRecord(
        event_date=fixture.event_date,
        home_team=fixture.home_team,
        away_team=fixture.away_team,
        score=actual,
        source="published_prediction_ledger",
        status="final",
    )


def _actual_score_from_row(row: dict[str, Any]) -> ScoreTip | None:
    home = _optional_int(row.get("actual_home"))
    away = _optional_int(row.get("actual_away"))
    if home is None or away is None:
        raw_score = str(row.get("actual_score") or "")
        if ":" not in raw_score:
            return None
        raw_home, raw_away = raw_score.split(":", 1)
        home = _optional_int(raw_home)
        away = _optional_int(raw_away)
    if home is None or away is None:
        return None
    return ScoreTip(home, away)


def _srf_source_row(row: dict[str, Any]) -> dict[str, Any] | None:
    tip = str(row.get("srf_tip") or "").strip()
    if not tip:
        return None
    return {
        "tip": tip,
        "source": "published_prediction_ledger",
    }


def _twenty_min_source_row(
    row: dict[str, Any],
    fixture: FixtureRecord,
    *,
    country_registry: CountryRegistry,
) -> dict[str, Any] | None:
    tip = str(row.get("twenty_min_tip") or row.get("twenty_min_selection") or "").strip()
    if not tip:
        return None
    selection = _twenty_min_selection(tip, fixture, country_registry=country_registry)
    return {
        "selection": selection,
        "selection_type": "outcome",
        "source": "published_prediction_ledger",
    }


def _twenty_min_selection(
    tip: str,
    fixture: FixtureRecord,
    *,
    country_registry: CountryRegistry,
) -> str:
    normalized = tip.casefold()
    if normalized in {"draw", "unentschieden", "remis"}:
        return "Draw"
    if _same_team_tip(tip, fixture.home_team, country_registry=country_registry):
        return fixture.home_team.name
    if _same_team_tip(tip, fixture.away_team, country_registry=country_registry):
        return fixture.away_team.name
    return tip


def _same_team_tip(tip: str, team: TeamRef, *, country_registry: CountryRegistry) -> bool:
    if tip.casefold() == team.name.casefold():
        return True
    if team.fifa_code and tip.casefold() == team.fifa_code.casefold():
        return True
    resolved = country_registry.resolve(tip, locale="en") or country_registry.resolve(tip, locale="de")
    return bool(resolved and team.fifa_code and resolved.canonical_id == team.fifa_code)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _points_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _account_points_text(total: float, delta: float) -> str:
    return f"{_points_text(total)} (+{_points_text(delta)})"


def _unpredicted_fixture_rows(storage, ledger_rows: list[dict[str, Any]], *, country_registry: CountryRegistry) -> list[dict[str, Any]]:
    ledger_keys = {str(row.get("fixture_key") or "") for row in ledger_rows}
    raw_fixtures = [fixture.to_record() for fixture in load_tournament_state(storage).fixtures]
    covered_slots = _covered_fixture_slots(raw_fixtures, ledger_rows, ledger_keys, country_registry=country_registry)
    candidate_rows = [
        row
        for row in raw_fixtures
        if str(row.get("fixture_key") or "") not in ledger_keys
        and str(row.get("status") or "").casefold() in {"open", "scheduled"}
        and not (_fixture_slot_keys(row, country_registry=country_registry) & covered_slots)
    ]
    preferred_sources_by_date = {
        str(row.get("event_date") or "")
        for row in candidate_rows
        if _fixture_source(row) == "srf_public"
    }
    selected = [
        row
        for row in candidate_rows
        if _fixture_source(row) == "srf_public" or str(row.get("event_date") or "") not in preferred_sources_by_date
    ]
    rows = []
    seen_keys: set[str] = set()
    for row in sorted(selected, key=lambda item: (str(item.get("event_date") or ""), _fixture_source_priority(item), str(item.get("fixture_key") or ""))):
        key = str(row.get("fixture_key") or "")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        rows.append(_fixture_placeholder_row(row, country_registry=country_registry))
    return rows


def _covered_fixture_slots(
    raw_fixtures: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    ledger_keys: set[str],
    *,
    country_registry: CountryRegistry,
) -> set[str]:
    slots: set[str] = set()
    for row in ledger_rows:
        slots.update(_fixture_slot_keys(row, country_registry=country_registry))
    for row in raw_fixtures:
        if str(row.get("fixture_key") or "") in ledger_keys:
            slots.update(_fixture_slot_keys(row, country_registry=country_registry))
    return slots


def _fixture_slot_keys(row: dict[str, Any], *, country_registry: CountryRegistry) -> set[str]:
    event_date = str(row.get("event_date") or "")
    if not event_date:
        return set()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    slots = set()
    match_number = str(metadata.get("match_number") or "").strip()
    if match_number:
        slots.add(f"match-number:{match_number}")
    for side in ("home", "away"):
        team_key = _known_team_slot_key(row, side=side, country_registry=country_registry)
        if team_key:
            slots.add(f"event-team:{event_date}:{team_key}")
    return slots


def _known_team_slot_key(row: dict[str, Any], *, side: str, country_registry: CountryRegistry) -> str:
    code = str(row.get(f"{side}_fifa_code") or "").strip().upper()
    if code and code in country_registry.countries:
        return code
    source_name = str(row.get(f"{side}_team") or "")
    resolved = country_registry.resolve(source_name, locale="en") or country_registry.resolve(source_name, locale="de")
    if resolved and resolved.canonical_id in country_registry.countries:
        return resolved.canonical_id
    return ""


def _fixture_placeholder_row(row: dict[str, Any], *, country_registry: CountryRegistry) -> dict[str, Any]:
    fixture_key = str(row.get("fixture_key") or "")
    home_name = _country_display_name(row, side="home", country_registry=country_registry)
    away_name = _country_display_name(row, side="away", country_registry=country_registry)
    return {
        "record_key": fixture_key,
        "fixture_key": fixture_key,
        "event_date": row.get("event_date"),
        "home_team": home_name,
        "away_team": away_name,
        "home_fifa_code": row.get("home_fifa_code"),
        "away_fifa_code": row.get("away_fifa_code"),
        "stage": row.get("stage"),
        "status": "future",
        "prediction_context": "fixture_known_without_prediction",
        "prediction_available": False,
        "fixture_status": row.get("status"),
        "fixture_source": _fixture_source(row),
        "venue": row.get("venue"),
        "metadata": row.get("metadata") or {},
    }


def _fixture_source(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        return str(metadata.get("source") or "")
    return ""


def _fixture_source_priority(row: dict[str, Any]) -> int:
    return 0 if _fixture_source(row) == "srf_public" else 1


def _prepare_html_row(row: dict[str, Any], *, country_registry: CountryRegistry, locale: str = "de") -> dict[str, Any]:
    catalog = load_translation_catalog(locale)
    prepared = {key: value for key, value in row.items() if key != "_record"}
    home_name = _country_display_name(prepared, side="home", country_registry=country_registry, locale=locale)
    away_name = _country_display_name(prepared, side="away", country_registry=country_registry, locale=locale)
    home_code = _country_code_from_fixture_key(str(prepared.get("fixture_key") or ""), side="home")
    away_code = _country_code_from_fixture_key(str(prepared.get("fixture_key") or ""), side="away")
    home_flag = FIFA_FLAG_EMOJIS.get(home_code, "")
    away_flag = FIFA_FLAG_EMOJIS.get(away_code, "")
    home_display = _country_name_display(home_name, home_flag)
    away_display = _country_name_display(away_name, away_flag)
    prepared["match"] = f"{home_name} - {away_name}".strip(" -")
    prepared["match_title_text"] = _match_title_text(home_name, away_name, locale=locale)
    prepared["home_team_label"] = home_name
    prepared["away_team_label"] = away_name
    prepared["home_flag"] = home_flag
    prepared["away_flag"] = away_flag
    prepared["home_team_display"] = home_display
    prepared["away_team_display"] = away_display
    prepared["match_display"] = _match_display(home_display, away_display)
    prepared["status_label"] = _status_label(prepared.get("status"), catalog=catalog)
    prepared["venue_display"] = _venue_display(prepared, locale=locale)
    prepared["weather_text"] = _weather_text(prepared, catalog=catalog)
    prepared["actual_score_label"] = catalog.translate("label.result") if prepared.get("actual_score") else ""
    prepared["srf_tip_label"] = _tip_display_text(prepared.get("srf_tip"), country_registry=country_registry, locale=locale)
    prepared["twenty_min_tip_label"] = _tip_display_text(
        prepared.get("twenty_min_tip"),
        country_registry=country_registry,
        locale=locale,
        show_flag=True,
    )
    prepared["twenty_min_tip_plain_label"] = _tip_display_text(
        prepared.get("twenty_min_tip"),
        country_registry=country_registry,
        locale=locale,
    )
    prepared["expected_score_full"] = (
        f"{_float_text(prepared.get('predicted_home_goals'))}:{_float_text(prepared.get('predicted_away_goals'))}"
    )
    prepared["expected_score_display"] = (
        f"{_float_text(prepared.get('predicted_home_goals'), precision=2)}:"
        f"{_float_text(prepared.get('predicted_away_goals'), precision=2)}"
    )
    prepared["hda_parts"] = _hda_parts(prepared, catalog=catalog)
    prepared["hda_title"] = _hda_title(prepared, catalog=catalog)
    prepared["confidence_text"] = _confidence_text(prepared.get("confidence_label"), prepared.get("confidence_percent"), catalog=catalog)
    prepared["top_score_matrix"] = _top_scores(prepared.get("score_matrix"), limit=10)
    prepared["stage_label"] = _stage_label(prepared, catalog=catalog)
    prepared["kickoff_display"] = _kickoff_display(prepared.get("event_date"), catalog=catalog)
    most_likely_home = _optional_int(prepared.get("most_likely_home"))
    most_likely_away = _optional_int(prepared.get("most_likely_away"))
    prepared["most_likely_home_display"] = "" if most_likely_home is None else str(most_likely_home)
    prepared["most_likely_away_display"] = "" if most_likely_away is None else str(most_likely_away)
    prepared["hda_bar"] = _hda_bar(prepared, catalog=catalog)
    prepared["hda_aria"] = _hda_aria(prepared, catalog=catalog)
    srf_expected_points = prepared.get("srf_expected_points")
    srf_projected_points = _srf_points_for_most_likely_score(prepared)
    srf_max_points = _srf_expected_points_max(prepared)
    prepared["srf_expected_points_display"] = _expected_points_display(srf_expected_points, max_points=srf_max_points)
    prepared["srf_projected_points_display"] = _projected_points_display(
        srf_projected_points,
        max_points=srf_max_points,
    )
    prepared["srf_tip_points_display"] = (
        prepared["srf_projected_points_display"] or prepared["srf_expected_points_display"]
    )
    prepared["srf_tip_points_title_key"] = _tip_points_title_key(
        projected_display=prepared["srf_projected_points_display"],
        expected_display=prepared["srf_expected_points_display"],
        projected_title_key="label.projected_points",
    )
    twenty_min_expected_points = prepared.get("twenty_min_expected_points")
    twenty_min_max_points = _twenty_min_expected_points_max(prepared)
    prepared["twenty_min_expected_points_display"] = _expected_points_display(
        twenty_min_expected_points,
        max_points=twenty_min_max_points,
    )
    twenty_min_projected_points, twenty_min_projected_title_key = _twenty_min_points_for_card(
        prepared,
        country_registry=country_registry,
    )
    prepared["twenty_min_projected_points_display"] = _projected_points_display(
        twenty_min_projected_points,
        max_points=twenty_min_max_points,
    )
    prepared["twenty_min_projected_points_title_key"] = twenty_min_projected_title_key
    prepared["twenty_min_tip_points_display"] = (
        prepared["twenty_min_projected_points_display"] or prepared["twenty_min_expected_points_display"]
    )
    prepared["twenty_min_tip_points_title_key"] = _tip_points_title_key(
        projected_display=prepared["twenty_min_projected_points_display"],
        expected_display=prepared["twenty_min_expected_points_display"],
        projected_title_key=twenty_min_projected_title_key,
    )
    prepared["hit_result"] = _hit_category(prepared)
    prepared["hit_label"] = _hit_label(prepared.get("hit_result"), catalog=catalog)
    prepared["most_likely_percent_text"] = _most_likely_percent_text(prepared)
    prepared["explain_text"] = _explain_text(prepared, catalog=catalog)
    prepared["advancement"] = _advancement_display(prepared, country_registry=country_registry, locale=locale)
    prepared["heatmap"] = _heatmap(prepared, catalog=catalog)
    prepared["page_title"] = _match_page_title(prepared, catalog=catalog)
    prepared["meta_description"] = _match_meta_description(prepared, catalog=catalog)
    prepared["og_description"] = prepared["meta_description"]
    prepared["card_aria_label"] = _match_card_aria_label(prepared, catalog=catalog)
    return prepared


def _stage_label(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    stage = str(row.get("stage") or metadata.get("stage") or metadata.get("phase") or "").casefold()
    if stage == "group_stage" or (not stage and _event_date_text(row) <= "2026-06-27"):
        letter = group_letter(str(metadata.get("group") or "") or None)
        if letter and len(letter) == 1:
            return catalog.translate("stage.group_letter", letter=letter)
        return catalog.translate("stage.group")
    round_name = _knockout_round_name(row)
    if round_name:
        key = KNOCKOUT_ROUND_STAGE_KEYS.get(round_name)
        if key:
            return catalog.translate(key)
    if stage == "knockout_stage":
        return catalog.translate("stage.knockout")
    return ""


def _knockout_round_name(row: dict[str, Any]) -> str | None:
    fixture_key = str(row.get("fixture_key") or "")
    for side in ("home", "away"):
        part = _fixture_key_part(fixture_key, side=side)
        feeder_round = _slot_feeder_round(part)
        if feeder_round == "Semi-final" and part.startswith("RU"):
            return "Third-place match"
        if feeder_round:
            return KNOCKOUT_ROUND_AFTER.get(feeder_round)
    event_date = _event_date_text(row)
    for start, end, round_name in KNOCKOUT_ROUND_DATE_RANGES:
        if start <= event_date <= end:
            return round_name
    return None


def _slot_feeder_round(part: str) -> str | None:
    code = canonical_slot_code(part)
    if not code:
        return None
    number = code.removeprefix("RU").removeprefix("W")
    return ROUND_NAMES.get(f"M{number}")


def _event_date_text(row: dict[str, Any]) -> str:
    return str(row.get("event_date") or "")[:10]


def _kickoff_display(value: Any, *, catalog: TranslationCatalog) -> str:
    if not value:
        return "-"
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    zurich = parsed.astimezone(ZoneInfo("Europe/Zurich"))
    weekday = catalog.translate(f"weekday.{zurich.weekday()}")
    return f"{weekday}, {zurich.strftime('%d.%m.%Y, %H:%M')}"


def _kickoff_compact_display(value: Any, *, catalog: TranslationCatalog) -> str:
    if not value:
        return "-"
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    zurich = parsed.astimezone(ZoneInfo("Europe/Zurich"))
    weekday = catalog.translate(f"weekday.{zurich.weekday()}")
    return f"{weekday}, {zurich.strftime('%d.%m., %H:%M')}"


def _probability_values(row: dict[str, Any]) -> tuple[float, float, float] | None:
    values = []
    for key in ("prob_home", "prob_draw", "prob_away"):
        try:
            values.append(float(row.get(key)))
        except (TypeError, ValueError):
            return None
    total = sum(values)
    if total <= 0:
        return None
    return values[0], values[1], values[2]


def _hda_bar(row: dict[str, Any], *, catalog: TranslationCatalog) -> dict[str, str] | None:
    values = _probability_values(row)
    if values is None:
        return None
    home, draw, away = values
    labels = _hda_compact_labels(row, catalog=catalog)
    return {
        "home_width": f"{home * 100:.2f}",
        "draw_width": f"{draw * 100:.2f}",
        "home_label": labels["home"],
        "draw_label": labels["draw"],
        "away_label": labels["away"],
        "home_text": _round_percent_text(home),
        "draw_text": _round_percent_text(draw),
        "away_text": _round_percent_text(away),
    }


def _hda_aria(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    values = _probability_values(row)
    if values is None:
        return ""
    parts = _hda_parts(row, catalog=catalog)
    return ", ".join(f"{part['label']} {_round_percent_text(value)}" for part, value in zip(parts, values))


def _round_percent_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if 0 < number < 0.005:
        return "<1%"
    return f"{round(number * 100)}%"


def _champion_percent_text(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def _expected_points_display(value: Any, *, max_points: Any = None) -> str:
    try:
        value_text = f"{float(value):.1f}"
    except (TypeError, ValueError):
        return ""
    max_text = _expected_points_max_display(max_points)
    if max_text:
        return f"{value_text}/{max_text}"
    return value_text


def _projected_points_display(value: Any, *, max_points: Any = None) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number < 0:
        return ""
    value_text = str(int(number)) if number.is_integer() else f"{number:g}"
    max_text = _expected_points_max_display(max_points)
    if max_text:
        return f"{value_text}/{max_text}"
    return value_text


def _tip_points_title_key(*, projected_display: str, expected_display: str, projected_title_key: str) -> str:
    if projected_display:
        return projected_title_key or "label.projected_points"
    if expected_display:
        return "label.expected_points"
    return ""


def _expected_points_max_display(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _srf_expected_points_max(row: dict[str, Any]) -> float | None:
    try:
        fixture = _fixture_record_from_site_row(row).to_fixture()
        return float(srf_rules_for_fixture(fixture).ruleset().metadata["exact_score_points"])
    except (KeyError, TypeError, ValueError):
        return None


def _twenty_min_expected_points_max(row: dict[str, Any]) -> float | None:
    try:
        fixture = _fixture_record_from_site_row(row).to_fixture()
        _phase, points = twenty_min_points_for_fixture(fixture)
        return float(points)
    except (TypeError, ValueError):
        return None


def _twenty_min_points_for_card(
    row: dict[str, Any],
    *,
    country_registry: CountryRegistry,
) -> tuple[float | None, str]:
    tip = str(row.get("twenty_min_tip") or row.get("twenty_min_selection") or "").strip()
    if not tip:
        return None, ""
    try:
        fixture = _fixture_record_from_site_row(row)
        phase, points = twenty_min_points_for_fixture(fixture.to_fixture())
    except (TypeError, ValueError):
        return None, ""

    selection = _twenty_min_selection(tip, fixture, country_registry=country_registry)
    most_likely = _most_likely_score_tip(row)
    if phase == "group_stage":
        if most_likely is None:
            return float(points), "label.selection_points"
        outcome_selection = _twenty_min_group_selection(fixture, most_likely)
        return _twenty_min_points_for_selection(selection, outcome_selection, points), "label.projected_points"

    if most_likely is None or most_likely.home == most_likely.away:
        return float(points), "label.selection_points"
    outcome_selection = fixture.home_team.name if most_likely.home > most_likely.away else fixture.away_team.name
    return _twenty_min_points_for_selection(selection, outcome_selection, points), "label.projected_points"


def _twenty_min_group_selection(fixture: FixtureRecord, score: ScoreTip) -> str:
    if score.home > score.away:
        return fixture.home_team.name
    if score.away > score.home:
        return fixture.away_team.name
    return "Draw"


def _twenty_min_points_for_selection(selection: str, outcome_selection: str, points: int) -> float:
    if selection.casefold() == outcome_selection.casefold():
        return float(points)
    return 0.0


def _srf_points_for_most_likely_score(row: dict[str, Any]) -> float | None:
    tip = _score_tip_from_text(row.get("srf_tip"))
    most_likely = _most_likely_score_tip(row)
    if tip is None or most_likely is None:
        return None
    try:
        fixture = _fixture_record_from_site_row(row).to_fixture()
        return float(srf_rules_for_fixture(fixture).points_for_tip(tip, most_likely))
    except (TypeError, ValueError):
        return None


def _most_likely_score_tip(row: dict[str, Any]) -> ScoreTip | None:
    home = _optional_int(row.get("most_likely_home"))
    away = _optional_int(row.get("most_likely_away"))
    if home is None or away is None:
        return _score_tip_from_text(row.get("most_likely_score"))
    return ScoreTip(home, away)


def _score_tip_from_text(value: Any) -> ScoreTip | None:
    text = str(value or "")
    if ":" not in text:
        return None
    home, away = text.split(":", 1)
    home_goals = _optional_int(home)
    away_goals = _optional_int(away)
    if home_goals is None or away_goals is None:
        return None
    return ScoreTip(home_goals, away_goals)


def _hit_category(row: dict[str, Any]) -> str | None:
    if str(row.get("status") or "") != "final":
        return None
    prediction = _most_likely_score_tip(row)
    actual = _actual_score_from_row(row)
    if prediction is None or actual is None:
        return None
    if prediction == actual:
        return "exact"
    predicted_outcome = _hda_prediction_outcome(row)
    if predicted_outcome is None:
        predicted_outcome = _score_outcome(prediction)
    if predicted_outcome != _score_outcome(actual):
        return "miss"
    return "trend"


def _hda_prediction_outcome(row: dict[str, Any]) -> int | None:
    values = _probability_values(row)
    if values is None:
        return None
    home, draw, away = values
    probability, outcome = max((home, 1), (draw, 0), (away, -1), key=lambda item: item[0])
    if probability <= 0:
        return None
    return outcome


def _score_outcome(score: ScoreTip) -> int:
    return (score.home > score.away) - (score.home < score.away)


def _hit_label(category: Any, *, catalog: TranslationCatalog) -> str:
    return {
        "exact": catalog.translate("hit.exact"),
        "trend": catalog.translate("hit.trend"),
        "miss": catalog.translate("hit.miss"),
    }.get(str(category or ""), "")


def _score_matrix_map(row: dict[str, Any]) -> dict[tuple[int, int], float]:
    matrix = row.get("score_matrix")
    if not isinstance(matrix, list):
        return {}
    cells: dict[tuple[int, int], float] = {}
    for entry in matrix:
        if not isinstance(entry, dict):
            continue
        home = _optional_int(entry.get("home"))
        away = _optional_int(entry.get("away"))
        if home is None or away is None:
            continue
        cells[(home, away)] = _float_value(entry.get("probability"))
    return cells


def _most_likely_percent_text(row: dict[str, Any]) -> str:
    home = _optional_int(row.get("most_likely_home"))
    away = _optional_int(row.get("most_likely_away"))
    if home is None or away is None:
        return ""
    probability = _score_matrix_map(row).get((home, away))
    if probability is None:
        return ""
    return _score_matrix_percent_text(probability)


def _score_matrix_percent_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if 0 < number < 0.001:
        return "<0.1%"
    return f"{number * 100:.1f}%"


def _explain_text(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    tip = str(row.get("srf_tip") or "")
    home = _optional_int(row.get("most_likely_home"))
    away = _optional_int(row.get("most_likely_away"))
    percent = str(row.get("most_likely_percent_text") or "")
    if ":" not in tip or home is None or away is None or not percent:
        return ""
    most_likely = f"{home}:{away}"
    if tip == most_likely:
        return catalog.translate("detail.explain_same", score=most_likely, percent=percent)
    return catalog.translate("detail.explain_diff", score=most_likely, percent=percent, tip=tip)


def _advancement_display(
    row: dict[str, Any],
    *,
    country_registry: CountryRegistry,
    locale: str,
) -> dict[str, str] | None:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    prediction_metadata = metadata.get("prediction_metadata")
    if not isinstance(prediction_metadata, dict):
        return None
    advancement = prediction_metadata.get("advancement_probabilities")
    if not isinstance(advancement, dict):
        return None
    home = _float_value(advancement.get("home"))
    away = _float_value(advancement.get("away"))
    if home <= 0 and away <= 0:
        return None
    side = "home" if home >= away else "away"
    return {
        "team_label": str(row.get(f"{side}_team_label") or ""),
        "flag": str(row.get(f"{side}_flag") or ""),
        "percent_text": _round_percent_text(home if side == "home" else away),
    }


def _heatmap(row: dict[str, Any], *, catalog: TranslationCatalog) -> dict[str, Any] | None:
    cells = _score_matrix_map(row)
    if not cells:
        return None
    peak = max(cells.values())
    if peak <= 0:
        return None
    most_likely = (_optional_int(row.get("most_likely_home")), _optional_int(row.get("most_likely_away")))
    actual = (_optional_int(row.get("actual_home")), _optional_int(row.get("actual_away")))
    mark_actual = str(row.get("status") or "") == "final" and None not in actual
    shown_mass = 0.0
    rows = []
    for home in range(HEATMAP_MAX_GOALS + 1):
        cell_row = []
        for away in range(HEATMAP_MAX_GOALS + 1):
            probability = cells.get((home, away), 0.0)
            shown_mass += probability
            weight = round(100 * probability / peak)
            cell_row.append(
                {
                    "home": home,
                    "away": away,
                    "weight": weight,
                    "hot": weight > 45,
                    "most_likely": (home, away) == most_likely,
                    "actual": mark_actual and (home, away) == actual,
                    "label": f"{probability * 100:.1f}" if probability >= 0.01 else "",
                    "title": f"{home}:{away} — {probability * 100:.1f}%",
                }
            )
        rows.append({"home": home, "cells": cell_row})
    hidden_mass = max(0.0, 1.0 - shown_mass)
    legend = [
        {
            "marker": "most-likely",
            "text": catalog.translate(
                "matrix.legend_most_likely",
                score=_score_text(most_likely[0], most_likely[1]),
                percent=str(row.get("most_likely_percent_text") or _round_percent_text(peak)),
            ),
        }
    ]
    if mark_actual and None not in actual and max(actual) <= HEATMAP_MAX_GOALS:
        actual_probability = cells.get((actual[0], actual[1]), 0.0)
        legend.append(
            {
                "marker": "actual",
                "text": catalog.translate(
                    "matrix.legend_actual",
                    score=_score_text(actual[0], actual[1]),
                    percent=_score_matrix_percent_text(actual_probability),
                ),
            }
        )
    if hidden_mass >= 0.001:
        legend.append({"marker": "", "text": catalog.translate("matrix.hidden_note", percent=f"{hidden_mass * 100:.1f}%")})
    return {
        "columns": list(range(HEATMAP_MAX_GOALS + 1)),
        "rows": rows,
        "axis_text": catalog.translate(
            "matrix.axis",
            home=str(row.get("home_team_label") or ""),
            away=str(row.get("away_team_label") or ""),
        ),
        "legend": legend,
    }


def _match_title_text(home: str, away: str, *, locale: str) -> str:
    separator = " vs " if locale == "en" else " - "
    if home and away:
        return f"{home}{separator}{away}"
    return (home or away).strip()


def _match_page_title(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    match = str(row.get("match_title_text") or row.get("match") or "").strip()
    actual_score = str(row.get("actual_score") or "").strip()
    if actual_score:
        return catalog.translate("seo.match.title_final", match=match, score=actual_score)
    score = _match_score_text(row)
    if score:
        return catalog.translate("seo.match.title_with_score", match=match, score=score)
    return catalog.translate("seo.match.title", match=match)


def _match_meta_description(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    match = str(row.get("match_title_text") or row.get("match") or "").strip()
    kickoff = str(row.get("kickoff_display") or "").strip()
    actual_score = str(row.get("actual_score") or "").strip()
    if actual_score:
        hit_label = str(row.get("hit_label") or "").strip()
        return catalog.translate(
            "seo.match.description_final",
            match=match,
            score=actual_score,
            hit_label=hit_label or catalog.translate("label.result"),
        )
    score = _match_score_text(row)
    percent = str(row.get("most_likely_percent_text") or "").strip()
    hda = _match_hda_meta_text(row, catalog=catalog)
    srf_tip = str(row.get("srf_tip_label") or "").strip() or catalog.translate("card.no_prediction")
    twenty_min_tip = (
        str(row.get("twenty_min_tip_plain_label") or "").strip()
        or catalog.translate("card.no_prediction")
    )
    if score and hda:
        return catalog.translate(
            "seo.match.description",
            match=match,
            kickoff=kickoff,
            score=score,
            percent=percent,
            hda=hda,
            srf_tip=srf_tip,
            twenty_min_tip=twenty_min_tip,
        )
    return catalog.translate("seo.match.description_pending", match=match, kickoff=kickoff)


def _match_card_aria_label(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    match = str(row.get("match_title_text") or row.get("match") or "").strip()
    kickoff = str(row.get("kickoff_display") or "").strip()
    actual_score = str(row.get("actual_score") or "").strip()
    if actual_score:
        return catalog.translate(
            "card.aria_past",
            match=match,
            kickoff=kickoff,
            score=actual_score,
            quality=str(row.get("hit_label") or "").strip() or catalog.translate("label.result"),
        )
    if str(row.get("status") or "") == "locked":
        return catalog.translate(
            "card.aria_locked",
            match=match,
            kickoff=kickoff,
            srf_tip=str(row.get("srf_tip_label") or "").strip() or catalog.translate("card.no_prediction"),
            twenty_min_tip=(
                str(row.get("twenty_min_tip_plain_label") or "").strip()
                or catalog.translate("card.no_prediction")
            ),
        )
    return catalog.translate(
        "card.aria_future",
        match=match,
        kickoff=kickoff,
        score=_match_score_text(row) or catalog.translate("card.no_prediction"),
        srf_tip=str(row.get("srf_tip_label") or "").strip() or catalog.translate("card.no_prediction"),
        twenty_min_tip=str(row.get("twenty_min_tip_plain_label") or "").strip() or catalog.translate("card.no_prediction"),
    )


def _match_score_text(row: dict[str, Any]) -> str:
    score = str(row.get("most_likely_score") or "").strip()
    if score and ":" in score and "-" not in score:
        return score
    home = str(row.get("most_likely_home_display") or "").strip()
    away = str(row.get("most_likely_away_display") or "").strip()
    if home and away:
        return f"{home}:{away}"
    return ""


def _match_hda_meta_text(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    values = _probability_values(row)
    if values is None:
        return ""
    labels = _hda_compact_labels(row, catalog=catalog)
    return ", ".join(
        (
            f"{labels['home']} {_round_percent_text(values[0])}",
            f"{labels['draw']} {_round_percent_text(values[1])}",
            f"{labels['away']} {_round_percent_text(values[2])}",
        )
    )


def _page_jsonld_graph(
    context: dict[str, Any],
    *,
    page_kind: str,
    item_rows: list[dict[str, Any]] | None = None,
    row: dict[str, Any] | None = None,
) -> str:
    base_url = str(context["base_url"])
    page_url = _absolute_site_url(str(context["current_url"]), base_url=base_url)
    graph: list[dict[str, Any]] = [
        _organization_jsonld(base_url),
        _website_jsonld(context),
        _predictions_dataset_jsonld(context),
    ]

    main_entity_id = _page_main_entity_id(page_url, page_kind=page_kind, has_items=bool(item_rows))
    graph.append(_webpage_jsonld(context, page_url=page_url, page_kind=page_kind, main_entity_id=main_entity_id))
    graph.append(_breadcrumb_jsonld(context, page_url=page_url, row=row))

    if page_kind == "match_detail" and row is not None:
        graph.append(_sports_event_jsonld(context, row=row, page_url=page_url))
    elif page_kind in {"home", "match_list"} and item_rows:
        graph.append(
            _match_item_list_jsonld(
                context,
                rows=item_rows,
                list_id=main_entity_id or f"{page_url}#matches",
                name=str(context.get("page_title") or context.get("title") or ""),
            )
        )
    elif page_kind == "tournament":
        champion_list = _champion_item_list_jsonld(context, list_id=main_entity_id or f"{page_url}#tournament-probabilities")
        if champion_list:
            graph.append(champion_list)

    return json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False)


def _organization_jsonld(base_url: str) -> dict[str, Any]:
    return {
        "@type": "Organization",
        "@id": f"{base_url}/#organization",
        "name": "Helga",
        "url": "https://www.helga.ch/",
        "sameAs": [
            "https://github.com/helga-agentur",
        ],
    }


def _website_jsonld(context: dict[str, Any]) -> dict[str, Any]:
    base_url = str(context["base_url"])
    payload: dict[str, Any] = {
        "@type": "WebSite",
        "@id": f"{base_url}/#website",
        "url": f"{base_url}/",
        "name": str(context.get("site_name") or "Helga World Cup Predictions"),
        "inLanguage": list(SITE_LOCALES),
        "publisher": {"@id": f"{base_url}/#organization"},
    }
    generated_at = str(context.get("generated_at_utc") or "").strip()
    if generated_at:
        payload["dateModified"] = generated_at
    return payload


def _predictions_dataset_jsonld(context: dict[str, Any]) -> dict[str, Any]:
    base_url = str(context["base_url"])
    api_url = _absolute_site_url(JSON_FEED_PATH, base_url=base_url)
    generated_at = str(context.get("generated_at_utc") or "").strip()
    payload: dict[str, Any] = {
        "@type": "Dataset",
        "@id": f"{api_url}#dataset",
        "name": "Helga World Cup 2026 Predictions API",
        "description": (
            "Machine-readable FIFA World Cup 2026 prediction feed with fixture keys, team identifiers, "
            "score probabilities, SRF and 20min tip recommendations, result state, and localized detail URLs."
        ),
        "url": api_url,
        "isAccessibleForFree": True,
        "inLanguage": list(SITE_LOCALES),
        "creator": {"@id": f"{base_url}/#organization"},
        "license": "https://github.com/helga-agentur/worldcup-predictions/blob/main/LICENSE",
        "sameAs": "https://github.com/helga-agentur/worldcup-predictions",
        "distribution": {
            "@type": "DataDownload",
            "@id": f"{api_url}#download",
            "contentUrl": api_url,
            "encodingFormat": "application/json",
            "name": "Predictions JSON API",
        },
    }
    if generated_at:
        payload["dateModified"] = generated_at
    return payload


def _page_main_entity_id(page_url: str, *, page_kind: str, has_items: bool) -> str | None:
    if page_kind == "match_detail":
        return f"{page_url}#sportsevent"
    if page_kind == "tournament":
        return f"{page_url}#tournament-probabilities"
    if page_kind == "match_list":
        return f"{page_url}#matches"
    if page_kind == "home" and has_items:
        return f"{page_url}#match-preview"
    return None


def _webpage_jsonld(
    context: dict[str, Any],
    *,
    page_url: str,
    page_kind: str,
    main_entity_id: str | None,
) -> dict[str, Any]:
    base_url = str(context["base_url"])
    page_type: str | list[str] = "WebPage"
    if page_kind in {"home", "match_list", "tournament"}:
        page_type = ["WebPage", "CollectionPage"]
    payload: dict[str, Any] = {
        "@type": page_type,
        "@id": f"{page_url}#webpage",
        "url": page_url,
        "name": str(context.get("title") or context.get("page_title") or ""),
        "description": str(context.get("description") or context.get("page_description") or ""),
        "inLanguage": str(context.get("locale") or DEFAULT_SITE_LOCALE),
        "isPartOf": {"@id": f"{base_url}/#website"},
        "publisher": {"@id": f"{base_url}/#organization"},
        "about": {"@id": f"{_absolute_site_url(JSON_FEED_PATH, base_url=base_url)}#dataset"},
    }
    generated_at = str(context.get("generated_at_utc") or "").strip()
    if generated_at:
        payload["dateModified"] = generated_at
    if main_entity_id:
        payload["mainEntity"] = {"@id": main_entity_id}
    return payload


def _breadcrumb_jsonld(context: dict[str, Any], *, page_url: str, row: dict[str, Any] | None) -> dict[str, Any]:
    locale = str(context.get("locale") or DEFAULT_SITE_LOCALE)
    base_url = str(context["base_url"])
    home_url = _absolute_site_url(f"/{locale}/", base_url=base_url)
    items = [
        {
            "@type": "ListItem",
            "position": 1,
            "name": str(context["t"]("nav.home")),
            "item": home_url,
        }
    ]
    if page_url != home_url:
        items.append(
            {
                "@type": "ListItem",
                "position": 2,
                "name": _breadcrumb_current_name(context, row=row),
                "item": page_url,
            }
        )
    return {
        "@type": "BreadcrumbList",
        "@id": f"{page_url}#breadcrumbs",
        "itemListElement": items,
    }


def _breadcrumb_current_name(context: dict[str, Any], *, row: dict[str, Any] | None) -> str:
    if row is not None:
        return str(row.get("match_title_text") or row.get("match") or context.get("title") or "")
    return str(context.get("page_title") or context.get("title") or "")


def _match_item_list_jsonld(
    context: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    list_id: str,
    name: str,
) -> dict[str, Any]:
    base_url = str(context["base_url"])
    return {
        "@type": "ItemList",
        "@id": list_id,
        "name": name,
        "numberOfItems": len(rows),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "item": _match_list_item_jsonld(row, base_url=base_url),
            }
            for index, row in enumerate(rows, start=1)
        ],
    }


def _match_list_item_jsonld(row: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    path = str(row.get("current_url") or f"{row.get('detail_path', '')}/")
    page_url = _absolute_site_url(path, base_url=base_url)
    payload = {
        "@type": "SportsEvent",
        "@id": f"{page_url}#sportsevent",
        "url": page_url,
        "name": str(row.get("match_title_text") or row.get("match") or ""),
        "startDate": str(row.get("event_date") or ""),
        "eventStatus": _schema_event_status(row),
    }
    return {key: value for key, value in payload.items() if value not in ("", None)}


def _champion_item_list_jsonld(context: dict[str, Any], *, list_id: str) -> dict[str, Any] | None:
    champion = context.get("champion")
    if not isinstance(champion, dict):
        return None
    entries = champion.get("entries")
    if not isinstance(entries, list) or not entries:
        return None
    return {
        "@type": "ItemList",
        "@id": list_id,
        "name": str(context.get("page_title") or context.get("title") or ""),
        "numberOfItems": len(entries),
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "item": _champion_team_jsonld(entry),
            }
            for index, entry in enumerate(entries, start=1)
        ],
    }


def _champion_team_jsonld(entry: dict[str, Any]) -> dict[str, Any]:
    code = str(entry.get("code") or "").strip()
    team: dict[str, Any] = {
        "@type": "SportsTeam",
        "name": str(entry.get("label") or ""),
        "additionalProperty": [
            {
                "@type": "PropertyValue",
                "name": "Title probability",
                "value": str(entry.get("percent_text") or ""),
            }
        ],
    }
    if code:
        team["identifier"] = code
    return team


def _sports_event_jsonld(context: dict[str, Any], *, row: dict[str, Any], page_url: str) -> dict[str, Any]:
    base_url = str(context["base_url"])
    home_team = _sports_team_jsonld(row, side="home", base_url=base_url)
    away_team = _sports_team_jsonld(row, side="away", base_url=base_url)
    payload: dict[str, Any] = {
        "@type": "SportsEvent",
        "@id": f"{page_url}#sportsevent",
        "url": page_url,
        "name": str(row.get("match_title_text") or row.get("match") or ""),
        "description": str(row.get("meta_description") or row.get("og_description") or context.get("description") or ""),
        "sport": "Football",
        "startDate": str(row.get("event_date") or ""),
        "eventStatus": _schema_event_status(row),
        "inLanguage": str(context.get("locale") or DEFAULT_SITE_LOCALE),
        "superEvent": {
            "@type": "SportsEvent",
            "@id": f"{base_url}/#fifa-world-cup-2026",
            "name": "FIFA World Cup 2026",
        },
        "homeTeam": home_team,
        "awayTeam": away_team,
        "competitor": [home_team, away_team],
        "isAccessibleForFree": True,
    }
    venue = _row_venue(row)
    if venue:
        payload["location"] = {"@type": "Place", "name": venue}
    return {key: value for key, value in payload.items() if value not in ("", None)}


def _sports_team_jsonld(row: dict[str, Any], *, side: str, base_url: str) -> dict[str, Any]:
    label = str(row.get(f"{side}_team_label") or row.get(f"{side}_team") or "").strip()
    identifier = _team_identifier(row, side=side)
    payload: dict[str, Any] = {
        "@type": "SportsTeam",
        "name": label,
    }
    if identifier:
        payload["@id"] = f"{base_url}/#team-{normalize_entity_text(identifier)}"
        payload["identifier"] = identifier
    return payload


def _team_identifier(row: dict[str, Any], *, side: str) -> str:
    code = str(row.get(f"{side}_fifa_code") or "").strip().upper()
    if code:
        return canonical_slot_code(code) or code
    part = _fixture_key_part(str(row.get("fixture_key") or ""), side=side)
    return canonical_slot_code(part) or part


def _schema_event_status(row: dict[str, Any]) -> str:
    return (
        "https://schema.org/EventCompleted"
        if str(row.get("status") or "") == "final"
        else "https://schema.org/EventScheduled"
    )


def _row_venue(row: dict[str, Any]) -> str:
    venue = str(row.get("venue") or "").strip()
    if venue:
        return venue
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return str(metadata.get("venue") or "").strip()


def _match_slug(row: dict[str, Any]) -> str:
    fixture_key = str(row.get("fixture_key") or "")
    parts = fixture_key.split("|")
    if len(parts) == 3:
        date = parts[0][:10]
        home = normalize_entity_text(parts[1]).replace(" ", "-")
        away = normalize_entity_text(parts[2]).replace(" ", "-")
        return f"{date}-{home}-{away}"
    return normalize_entity_text(str(row.get("match") or row.get("record_key") or "match")).replace(" ", "-")


def _status_label(status: Any, *, catalog: TranslationCatalog) -> str:
    return {
        "future": catalog.translate("status.future"),
        "locked": catalog.translate("status.tipped"),
        "final": catalog.translate("status.tipped"),
    }.get(str(status or ""), str(status or "-"))


def _hda_parts(row: dict[str, Any], *, catalog: TranslationCatalog) -> list[dict[str, str]]:
    return [
        {"label": _team_win_label(row, side="home", catalog=catalog), "value": _percent_text(row.get("prob_home"))},
        {"label": catalog.translate("prob.draw"), "value": _percent_text(row.get("prob_draw"))},
        {"label": _team_win_label(row, side="away", catalog=catalog), "value": _percent_text(row.get("prob_away"))},
    ]


def _hda_compact_labels(row: dict[str, Any], *, catalog: TranslationCatalog) -> dict[str, str]:
    home = str(row.get("home_team_label") or "").strip() or catalog.translate("prob.home")
    away = str(row.get("away_team_label") or "").strip() or catalog.translate("prob.away")
    return {
        "home": home,
        "draw": catalog.translate("prob.draw_short"),
        "away": away,
    }


def _hda_title(row: dict[str, Any], *, catalog: TranslationCatalog) -> str:
    return " / ".join(
        (
            _team_win_label(row, side="home", catalog=catalog),
            catalog.translate("prob.draw"),
            _team_win_label(row, side="away", catalog=catalog),
        )
    )


def _team_win_label(row: dict[str, Any], *, side: str, catalog: TranslationCatalog) -> str:
    flag = str(row.get(f"{side}_flag") or "").strip()
    team = str(row.get(f"{side}_team_label") or "").strip()
    if flag and team:
        return catalog.translate("prob.team_win", team=team, flag=flag).strip()
    fallback_key = "prob.home" if side == "home" else "prob.away"
    return catalog.translate(fallback_key)


def _flagged_label(flag: Any, label: str) -> str:
    text = str(label or "")
    emoji = str(flag or "").strip()
    return f"{emoji} {text}" if emoji else text


def _tip_display_text(
    value: Any,
    *,
    country_registry: CountryRegistry,
    locale: str = "de",
    show_flag: bool = False,
) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.casefold() == "draw":
        return load_translation_catalog(locale).translate("prob.draw")
    if ":" in text:
        return text
    resolved = country_registry.resolve(text, locale="en") or country_registry.resolve(text, locale="de")
    if resolved and resolved.canonical_id:
        country = country_registry.countries.get(resolved.canonical_id)
        if country is not None:
            label = country.names.get(locale) or country.names.get("en") or text
            return _flagged_label(FIFA_FLAG_EMOJIS.get(resolved.canonical_id, "") if show_flag else "", label)
    return text


def _country_display_name(row: dict[str, Any], *, side: str, country_registry: CountryRegistry, locale: str = "de") -> str:
    fixture_key_part = _fixture_key_part(str(row.get("fixture_key") or ""), side=side)
    slot_label = slot_display_name(fixture_key_part, locale=locale)
    if slot_label:
        return slot_label
    code = fixture_key_part if len(fixture_key_part) == 3 else ""
    if code:
        country = country_registry.countries.get(code)
        if country is not None:
            return country.names.get(locale) or country.names.get("en") or code
    source_name = str(row.get(f"{side}_team") or "")
    slot_label = slot_display_name(source_name, locale=locale)
    if slot_label:
        return slot_label
    resolved = country_registry.resolve(source_name, locale="en") or country_registry.resolve(source_name, locale="de")
    if resolved and resolved.canonical_id:
        country = country_registry.countries.get(resolved.canonical_id)
        if country is not None:
            return country.names.get(locale) or country.names.get("en") or source_name
    return source_name


def _fixture_key_part(fixture_key: str, *, side: str) -> str:
    parts = fixture_key.split("|")
    if len(parts) != 3:
        return ""
    index = 1 if side == "home" else 2
    return parts[index].upper()


def _country_code_from_fixture_key(fixture_key: str, *, side: str) -> str:
    value = _fixture_key_part(fixture_key, side=side)
    return value if len(value) == 3 else ""


def _country_name_display(label: str, flag: Any) -> dict[str, str]:
    text = str(label or "").strip()
    emoji = str(flag or "").strip()
    return {
        "flag": emoji,
        "label": text,
        "text": _flagged_label(emoji, text),
    }


def _match_display(home: dict[str, str], away: dict[str, str]) -> str:
    return f"{home['text']} - {away['text']}".strip(" -")


def _top_scores(score_matrix: Any, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(score_matrix, list):
        return []
    entries = [entry for entry in score_matrix if isinstance(entry, dict)]
    entries.sort(key=lambda entry: float(entry.get("probability") or 0.0), reverse=True)
    return entries[:limit]


def _score_text(home: Any, away: Any) -> str:
    home_goals = _optional_int(home)
    away_goals = _optional_int(away)
    if home_goals is None or away_goals is None:
        return "-"
    return f"{home_goals}:{away_goals}"


def _float_text(value: Any, *, precision: int | None = None, decimal_separator: str = ".") -> str:
    if value in (None, ""):
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if precision is None:
        text = repr(number)
    else:
        text = f"{number:.{precision}f}"
    return text.replace(".", decimal_separator) if decimal_separator != "." else text


def _percent_text(value: Any) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _confidence_text(label: Any, percent: Any, *, catalog: TranslationCatalog) -> str:
    try:
        value = float(percent)
    except (TypeError, ValueError):
        value = None
    if value is not None and value > 1.0:
        # Ledger rows frozen between the market-prior unification and its
        # scale fix stored confidence_percent as 0-100; normalize so past
        # match pages don't render "4415.7%" forever.
        value = value / 100.0
    fallback_label = "-"
    if value is not None:
        if value >= 0.70:
            fallback_label = catalog.translate("confidence.high")
        elif value >= 0.60:
            fallback_label = catalog.translate("confidence.medium_high")
        elif value >= 0.52:
            fallback_label = catalog.translate("confidence.medium")
        else:
            fallback_label = catalog.translate("confidence.low")
    resolved_label = _confidence_label(label, catalog=catalog) or fallback_label
    if value is None:
        return resolved_label
    return f"{resolved_label} ({value * 100:.1f}%)"


def _confidence_label(label: Any, *, catalog: TranslationCatalog) -> str:
    normalized = str(label or "").strip().casefold()
    return {
        "high": catalog.translate("confidence.high"),
        "medium-high": catalog.translate("confidence.medium_high"),
        "medium high": catalog.translate("confidence.medium_high"),
        "medium": catalog.translate("confidence.medium"),
        "medium-low": catalog.translate("confidence.medium_low"),
        "medium low": catalog.translate("confidence.medium_low"),
        "low": catalog.translate("confidence.low"),
    }.get(normalized, str(label or ""))


def _date_text(value: Any) -> str:
    if not value:
        return "-"
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    zurich = parsed.astimezone(ZoneInfo("Europe/Zurich"))
    return zurich.strftime("%d.%m.%Y, %H:%M")


def _date_time_text(value: Any) -> str:
    if not value:
        return "-"
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    zurich = parsed.astimezone(ZoneInfo("Europe/Zurich"))
    return zurich.strftime("%d.%m.%Y, %H:%M:%S")


def _sitemap_xml(localized_contexts: dict[str, dict[str, Any]]) -> str:
    urls = []
    for locale, context in localized_contexts.items():
        generated_at_utc = str(context["generated_at_utc"])
        base_url = str(context["base_url"])
        urls.append((_absolute_site_url(f"/{locale}/", base_url=base_url), generated_at_utc))
        urls.extend(
            (_absolute_site_url(LOCALE_MATCH_LIST_PATHS[locale][kind], base_url=base_url), generated_at_utc)
            for kind in ("future", "past")
        )
        urls.append((_absolute_site_url(LOCALE_TOURNAMENT_PATHS[locale], base_url=base_url), generated_at_utc))
        urls.extend((_absolute_site_url(str(row["detail_path"]) + "/", base_url=base_url), generated_at_utc) for row in context["rows"])
    url_nodes = "\n".join(
        f"""  <url>
    <loc>{html_lib.escape(loc)}</loc>
    <lastmod>{html_lib.escape(lastmod)}</lastmod>
  </url>"""
        for loc, lastmod in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{url_nodes}
</urlset>
"""


def _llms_txt(localized_contexts: dict[str, dict[str, Any]]) -> str:
    context = localized_contexts.get(DEFAULT_SITE_LOCALE) or next(iter(localized_contexts.values()))
    base_url = str(context["base_url"])
    return "\n".join(
        (
            "# Helga World Cup Predictions",
            "",
            "> Data-driven FIFA World Cup 2026 forecasts from Helga: match score probabilities, provider-neutral predictions, SRF and 20min tip recommendations, result history, tournament probabilities, and a machine-readable JSON API.",
            "",
            "This static site publishes localized HTML pages for humans and a JSON feed for agents. Treat the JSON API as the freshest structured source. HTML pages provide canonical URLs, hreflang alternates, OpenGraph/Twitter metadata, and SportsEvent JSON-LD on match detail pages.",
            "",
            "Display times are formatted for Europe/Zurich. API timestamps and fixture dates use UTC where available. Predictions and provider-specific tips can change until kickoff; final rows include confirmed results and scoring outcomes.",
            "",
            "## Primary pages",
            "",
            f"- [English overview]({_absolute_site_url('/en/', base_url=base_url)}): Current prediction dashboard with upcoming matches, past matches, and tournament forecast preview.",
            f"- [German overview]({_absolute_site_url('/de/', base_url=base_url)}): Localized German prediction dashboard.",
            f"- [Upcoming matches]({_absolute_site_url(LOCALE_MATCH_LIST_PATHS['en']['future'], base_url=base_url)}): Open fixtures with score probabilities and SRF/20min recommendations.",
            f"- [Past matches]({_absolute_site_url(LOCALE_MATCH_LIST_PATHS['en']['past'], base_url=base_url)}): Locked or played fixtures with published tips, results, points, and hit quality where available.",
            f"- [Tournament forecast]({_absolute_site_url(LOCALE_TOURNAMENT_PATHS['en'], base_url=base_url)}): Remaining-team title probabilities and knockout forecast simulation.",
            "",
            "## Structured data",
            "",
            f"- [Predictions JSON API]({_absolute_site_url(JSON_FEED_PATH, base_url=base_url)}): Canonical machine-readable prediction feed. Includes summary totals, fixture keys, team ids, probabilities, provider tips, result state, and localized detail URLs.",
            f"- [Sitemap]({_absolute_site_url('/sitemap.xml', base_url=base_url)}): Indexable localized HTML pages.",
            "",
            "## Source and methodology",
            "",
            "- [GitHub repository](https://github.com/helga-agentur/worldcup-predictions): Full source code for ingestion, modeling, provider optimization, tournament simulation, static export, and deployment automation.",
            "- [Data vs gut feeling](https://blog.helga.ch/wer-tippt-besser-bauchgef%C3%BChl-oder-daten-97f7cf1bbdc8): Background article explaining the motivation for the forecasts.",
            "",
            "## Notes for LLMs",
            "",
            "- The core forecast is provider-neutral; SRF and 20min tips are optimization layers on top of the neutral prediction data.",
            "- Prefer fixture keys, FIFA team codes, and JSON fields over localized display labels when comparing rows.",
            "- Use localized HTML pages for human-readable summaries and the JSON API for exact current values.",
            "- Do not infer that predictions are final before kickoff; open and locked matches can still differ from confirmed results.",
            "",
        )
    )


class _CacheAwareStaticHandler(SimpleHTTPRequestHandler):
    directory: str

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=self.directory, **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", _cache_control_for_path(self.path))
        if _is_json_path(self.path):
            self.send_header("Content-Disposition", "inline")
            self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def guess_type(self, path: str) -> str:
        if _is_json_path(path):
            return "application/json; charset=utf-8"
        if path.endswith(".xml"):
            return "application/xml"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"


def _cache_control_for_path(path: str) -> str:
    normalized = path.split("?", 1)[0]
    if normalized.startswith("/assets/"):
        return ASSET_CACHE_CONTROL
    if _is_json_path(normalized):
        return JSON_CACHE_CONTROL
    return HTML_CACHE_CONTROL


def _is_json_path(path: str) -> bool:
    normalized = path.split("?", 1)[0]
    return normalized.startswith("/api/") or normalized.endswith(".json") or "/api/" in normalized


def gtm_container_id_from_env(project_root: Path) -> str:
    """Read the optional GTM container id from process env or project `.env`."""

    return env_value(Path(project_root), ENV_GTM_CONTAINER_ID) or ""


def base_url_from_env(project_root: Path) -> str:
    """Read the public site base URL from process env or project `.env`."""

    return env_value(Path(project_root), ENV_BASE_URL) or SITE_BASE_URL


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_record"}
