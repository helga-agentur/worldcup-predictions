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
from worldcup_predictions.core.constants import ENV_GTM_CONTAINER_ID
from worldcup_predictions.core.datasets import PROVIDER_POINTS, PUBLISHED_PREDICTION_LEDGER, TOURNAMENT_FIXTURES
from worldcup_predictions.core.env import env_value
from worldcup_predictions.entities.countries import CountryRegistry, load_country_registry
from worldcup_predictions.entities.countries import normalize_entity_text
from worldcup_predictions.evaluation.provider_points import points_for_row
from worldcup_predictions.storage.ledger import normalize_datetime, utc_now
from worldcup_predictions.tournament import FixtureRecord, ResultRecord, TeamRef


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
) -> SiteBuildResult:
    """Build the static website from the latest published prediction ledger."""

    project_root = Path(project_root)
    target_dir = output_dir or project_root / "public" / "current"
    generated_at = normalize_datetime(utc_now()) or ""
    ledger_rows = [_strip_record(row) for row in storage.read_records(PUBLISHED_PREDICTION_LEDGER, latest_only=True)]
    ledger_rows.sort(key=lambda row: (str(row.get("event_date") or ""), str(row.get("fixture_key") or "")))
    country_registry = load_country_registry()
    placeholder_rows = _unpredicted_fixture_rows(storage, ledger_rows, country_registry=country_registry)
    rows = [_prepare_html_row(row, country_registry=country_registry) for row in [*ledger_rows, *placeholder_rows]]
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
        "predictions": rows,
    }

    css_content = _render_css()
    js_content = _render_js()
    css_hash = hashlib.sha256(css_content.encode("utf-8")).hexdigest()[:12]
    js_hash = hashlib.sha256(js_content.encode("utf-8")).hexdigest()[:12]
    asset_path = f"assets/site.{css_hash}.css"
    script_path = f"assets/theme.{js_hash}.js"

    context = {
        "title": "Helga Tippspiel Prognosen",
        "description": "Öffentliche FIFA-WM-2026-Prognosen, provider-neutrale Scores und optimierte Tippspiel-Empfehlungen.",
        "generated_at_utc": generated_at,
        "asset_css": f"/{asset_path}",
        "asset_js": f"/{script_path}",
        "gtm_container_id": (gtm_container_id or "").strip(),
        "rows": rows,
        "future_rows": future_rows,
        "locked_rows": locked_rows,
        "final_rows": final_rows,
        "tipped_rows": tipped_rows,
        "summary": data_payload["summary"],
        "json_feed_path": JSON_FEED_PATH,
    }

    temp_dir = target_dir.with_name(f".{target_dir.name}.tmp")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    _write_site_files(
        temp_dir,
        context=context,
        css_content=css_content,
        js_content=js_content,
        asset_path=asset_path,
        script_path=script_path,
        data_payload=data_payload,
    )
    if target_dir.exists():
        shutil.rmtree(target_dir)
    temp_dir.rename(target_dir)

    result = SiteBuildResult(
        output_dir=target_dir,
        generated_at_utc=generated_at,
        html_files=tuple(["index.html", *[f"{str(row['detail_path']).lstrip('/')}/index.html" for row in rows]]),
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


def _write_site_files(
    output_dir: Path,
    *,
    context: dict[str, Any],
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
    html = env.get_template("pages/predictions.html").render(**context)
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    detail_template = env.get_template("pages/match_detail.html")
    for row in context["rows"]:
        detail_dir = output_dir / str(row["detail_path"]).lstrip("/")
        detail_dir.mkdir(parents=True, exist_ok=True)
        detail_dir.joinpath("index.html").write_text(detail_template.render(**context, row=row), encoding="utf-8")
    (output_dir / asset_path).write_text(css_content, encoding="utf-8")
    (output_dir / script_path).write_text(js_content, encoding="utf-8")
    _write_static_assets(output_dir)
    (output_dir / "api" / "predictions").write_text(
        json.dumps(data_payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    (output_dir / "robots.txt").write_text("User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n", encoding="utf-8")
    (output_dir / "sitemap.xml").write_text(_sitemap_xml(context["generated_at_utc"], context["rows"]), encoding="utf-8")


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
    raw_fixtures = [_strip_record(row) for row in storage.read_records(TOURNAMENT_FIXTURES, latest_only=True)]
    candidate_rows = [
        row
        for row in raw_fixtures
        if str(row.get("fixture_key") or "") not in ledger_keys
        and str(row.get("status") or "").casefold() in {"open", "scheduled"}
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


def _prepare_html_row(row: dict[str, Any], *, country_registry: CountryRegistry) -> dict[str, Any]:
    prepared = {key: value for key, value in row.items() if key != "_record"}
    home_name = _country_display_name(prepared, side="home", country_registry=country_registry)
    away_name = _country_display_name(prepared, side="away", country_registry=country_registry)
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
    prepared["detail_path"] = f"/spiele/{_match_slug(prepared)}"
    prepared["status_label"] = _status_label(prepared.get("status"))
    prepared["actual_score_label"] = "Resultat" if prepared.get("actual_score") else ""
    prepared["srf_tip_label"] = _tip_display_text(prepared.get("srf_tip"), country_registry=country_registry)
    prepared["twenty_min_tip_label"] = _tip_display_text(prepared.get("twenty_min_tip"), country_registry=country_registry)
    prepared["expected_score_full"] = (
        f"{_float_text(prepared.get('predicted_home_goals'))}:{_float_text(prepared.get('predicted_away_goals'))}"
    )
    prepared["expected_score_display"] = (
        f"{_float_text(prepared.get('predicted_home_goals'), precision=2)}:"
        f"{_float_text(prepared.get('predicted_away_goals'), precision=2)}"
    )
    prepared["hda_parts"] = _hda_parts(prepared)
    prepared["confidence_text"] = _confidence_text(prepared.get("confidence_label"), prepared.get("confidence_percent"))
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


def _status_label(status: Any) -> str:
    return {
        "future": "Offen",
        "locked": "Getippt",
        "final": "Getippt",
    }.get(str(status or ""), str(status or "-"))


def _hda_parts(row: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"label": "Heimsieg", "value": _percent_text(row.get("prob_home"))},
        {"label": "Unentschieden", "value": _percent_text(row.get("prob_draw"))},
        {"label": "Auswärtssieg", "value": _percent_text(row.get("prob_away"))},
    ]


def _tip_display_text(value: Any, *, country_registry: CountryRegistry) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.casefold() == "draw":
        return "Unentschieden"
    if ":" in text:
        return text
    resolved = country_registry.resolve(text, locale="en") or country_registry.resolve(text, locale="de")
    if resolved and resolved.canonical_id:
        country = country_registry.countries.get(resolved.canonical_id)
        if country is not None:
            return country.names.get("de") or country.names.get("en") or text
    return text


def _country_display_name(row: dict[str, Any], *, side: str, country_registry: CountryRegistry) -> str:
    code = _country_code_from_fixture_key(str(row.get("fixture_key") or ""), side=side)
    if code:
        country = country_registry.countries.get(code)
        if country is not None:
            return country.names.get("de") or country.names.get("en") or code
    source_name = str(row.get(f"{side}_team") or "")
    resolved = country_registry.resolve(source_name, locale="en") or country_registry.resolve(source_name, locale="de")
    if resolved and resolved.canonical_id:
        country = country_registry.countries.get(resolved.canonical_id)
        if country is not None:
            return country.names.get("de") or country.names.get("en") or source_name
    return source_name


def _country_code_from_fixture_key(fixture_key: str, *, side: str) -> str:
    parts = fixture_key.split("|")
    if len(parts) != 3:
        return ""
    index = 1 if side == "home" else 2
    value = parts[index].upper()
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


def _confidence_text(label: Any, percent: Any) -> str:
    try:
        value = float(percent)
    except (TypeError, ValueError):
        value = None
    fallback_label = "-"
    if value is not None:
        if value >= 0.70:
            fallback_label = "Hoch"
        elif value >= 0.60:
            fallback_label = "Eher hoch"
        elif value >= 0.52:
            fallback_label = "Mittel"
        else:
            fallback_label = "Tief"
    resolved_label = _confidence_label_de(label) or fallback_label
    if value is None:
        return resolved_label
    return f"{resolved_label} ({value * 100:.1f}%)"


def _confidence_label_de(label: Any) -> str:
    normalized = str(label or "").strip().casefold()
    return {
        "high": "Hoch",
        "medium-high": "Eher hoch",
        "medium high": "Eher hoch",
        "medium": "Mittel",
        "medium-low": "Eher tief",
        "medium low": "Eher tief",
        "low": "Tief",
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


def _sitemap_xml(generated_at_utc: str, rows: list[dict[str, Any]]) -> str:
    urls = [("/", generated_at_utc), *[(str(row["detail_path"]) + "/", generated_at_utc) for row in rows]]
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


def _strip_record(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_record"}
