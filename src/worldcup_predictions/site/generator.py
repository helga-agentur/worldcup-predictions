"""Generate a static, SEO-friendly prediction website."""

from __future__ import annotations

import datetime as dt
import hashlib
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
from worldcup_predictions.core.datasets import PROVIDER_POINTS, PUBLISHED_PREDICTION_LEDGER
from worldcup_predictions.core.env import env_value
from worldcup_predictions.core.i18n import SUPPORTED_LOCALES, TranslationCatalog, load_translation_catalog
from worldcup_predictions.entities.countries import CountryRegistry, load_country_registry
from worldcup_predictions.entities.countries import normalize_entity_text
from worldcup_predictions.evaluation.provider_points import points_for_row
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
STATIC_ASSET_FILES = (
    "assets/favicon.svg",
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
API_PRESENTATION_KEYS = {
    "actual_score",
    "actual_score_label",
    "alternate_links",
    "away_flag",
    "away_team_label",
    "confidence_text",
    "current_url",
    "detail_path",
    "expected_score_display",
    "expected_score_full",
    "hda_title",
    "hda_parts",
    "home_flag",
    "home_team_label",
    "language_switch_links",
    "match",
    "match_display",
    "metadata",
    "most_likely_score",
    "provider_tips",
    "record_key",
    "srf_account_display",
    "srf_tip_label",
    "status_label",
    "top_score_matrix",
    "twenty_min_account_display",
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
            "srf_points_display": _points_text(provider_points["srf.ch"]),
            "twenty_min_points_display": _points_text(provider_points["20min.ch"]),
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
    return {
        "locale": locale,
        "title": catalog.translate("site.title"),
        "description": catalog.translate("site.description"),
        "generated_at_utc": generated_at,
        "generated_at_display": _date_time_text(generated_at),
        "asset_css": f"/{asset_path}",
        "asset_js": f"/{script_path}",
        "gtm_container_id": (gtm_container_id or "").strip(),
        "rows": rows,
        "future_rows": future_rows,
        "locked_rows": locked_rows,
        "final_rows": final_rows,
        "tipped_rows": tipped_rows,
        "summary": summary,
        "json_feed_path": JSON_FEED_PATH,
        "language_cookie_name": LANGUAGE_COOKIE_NAME,
        "language_switch_links": _language_switch_links(locale, ""),
        "current_url": f"/{locale}/",
        "alternate_links": _alternate_links(""),
        "t": catalog.translate,
    }


def _add_alternate_links(locale: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        slug = _match_slug(row)
        row["detail_path"] = _detail_path(locale, slug)
        row["current_url"] = row["detail_path"] + "/"
        row["alternate_links"] = _alternate_links(f"/{LOCALE_DETAIL_PREFIX[DEFAULT_SITE_LOCALE]}/{slug}", slug=slug)
        row["language_switch_links"] = _language_switch_links(locale, slug)


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


def _html_files(localized_contexts: dict[str, dict[str, Any]]) -> list[str]:
    html_files = ["index.html"]
    for locale, context in localized_contexts.items():
        html_files.append(f"{locale}/index.html")
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
    detail_template = env.get_template("pages/match_detail.html")
    for locale, context in localized_contexts.items():
        locale_dir = output_dir / locale
        locale_dir.mkdir(parents=True, exist_ok=True)
        locale_dir.joinpath("index.html").write_text(predictions_template.render(**context), encoding="utf-8")
        for row in context["rows"]:
            detail_dir = output_dir / str(row["detail_path"]).lstrip("/")
            detail_dir.mkdir(parents=True, exist_ok=True)
            detail_context = {
                **context,
                "current_url": row["current_url"],
                "alternate_links": row["alternate_links"],
                "language_switch_links": row["language_switch_links"],
            }
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
        output_dir.joinpath(asset_file).write_bytes(static_dir.joinpath(*relative_path.parts).read_bytes())


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
        group=row.get("group"),
        matchday=_optional_int(row.get("matchday")),
        source_id=row.get("source_id"),
        venue=row.get("venue"),
        status=str(row.get("status") or "final"),
        metadata=dict(metadata),
    )


def _fixture_stage_from_site_row(row: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    stage = row.get("stage") or metadata.get("phase")
    current_metadata = metadata.get("current_prediction_ledger_metadata")
    if not stage and isinstance(current_metadata, dict):
        stage = current_metadata.get("phase")
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
    prepared["match"] = f"{home_name} - {away_name}".strip(" -")
    prepared["home_team_label"] = home_name
    prepared["away_team_label"] = away_name
    prepared["home_flag"] = home_flag
    prepared["away_flag"] = away_flag
    prepared["match_display"] = _match_display(home_name, home_code, away_name, away_code)
    prepared["status_label"] = _status_label(prepared.get("status"), catalog=catalog)
    prepared["actual_score_label"] = catalog.translate("label.result") if prepared.get("actual_score") else ""
    prepared["srf_tip_label"] = _tip_display_text(prepared.get("srf_tip"), country_registry=country_registry, locale=locale)
    prepared["twenty_min_tip_label"] = _tip_display_text(
        prepared.get("twenty_min_tip"),
        country_registry=country_registry,
        locale=locale,
        show_flag=True,
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
    return prepared


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


def _match_display(home_name: str, home_code: str, away_name: str, away_code: str) -> str:
    home_flag = FIFA_FLAG_EMOJIS.get(home_code)
    away_flag = FIFA_FLAG_EMOJIS.get(away_code)
    home_display = f"{home_flag} {home_name}" if home_flag else home_name
    away_display = f"{away_name} {away_flag}" if away_flag else away_name
    return f"{home_display} - {away_display}".strip(" -")


def _top_scores(score_matrix: Any, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(score_matrix, list):
        return []
    entries = [entry for entry in score_matrix if isinstance(entry, dict)]
    entries.sort(key=lambda entry: float(entry.get("probability") or 0.0), reverse=True)
    return entries[:limit]


def _score_text(home: Any, away: Any) -> str:
    if home in (None, "") or away in (None, ""):
        return "-"
    return f"{home}:{away}"


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
        urls.append((f"/{locale}/", generated_at_utc))
        urls.extend((str(row["detail_path"]) + "/", generated_at_utc) for row in context["rows"])
    url_nodes = "\n".join(
        f"""  <url>
    <loc>{loc}</loc>
    <lastmod>{lastmod}</lastmod>
  </url>"""
        for loc, lastmod in urls
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{url_nodes}
</urlset>
"""


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
