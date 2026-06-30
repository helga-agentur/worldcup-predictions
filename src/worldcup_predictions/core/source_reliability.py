"""Shared source reliability profiles for public article extraction."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceReliability:
    """Reliability assessment for one public source mention."""

    score: float
    bucket: str
    tier: str
    strength: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceProfile:
    """Known publisher profile."""

    domain: str
    score: float
    tier: str
    strength: str
    aliases: tuple[str, ...] = ()


SOURCE_PROFILES: tuple[SourceProfile, ...] = (
    SourceProfile("fifa.com", 1.00, "official", "very_high", ("fifa",)),
    SourceProfile("uefa.com", 0.96, "official", "very_high", ("uefa",)),
    SourceProfile("reuters.com", 0.92, "wire", "very_high", ("reuters",)),
    SourceProfile("apnews.com", 0.92, "wire", "very_high", ("associated press", "ap news")),
    SourceProfile("bbc.com", 0.90, "major_media", "high", ("bbc sport", "bbc")),
    SourceProfile("bbc.co.uk", 0.90, "major_media", "high", ("bbc sport", "bbc")),
    SourceProfile("theathletic.com", 0.86, "specialist_media", "high", ("the athletic",)),
    SourceProfile("theanalyst.com", 0.84, "specialist_media", "high", ("the analyst", "opta analyst")),
    SourceProfile("espn.com", 0.82, "major_media", "high", ("espn",)),
    SourceProfile("skysports.com", 0.80, "major_media", "high", ("sky sports",)),
    SourceProfile("fotmob.com", 0.78, "data_app", "usable", ("fotmob",)),
    SourceProfile("theguardian.com", 0.78, "major_media", "usable", ("the guardian",)),
    SourceProfile("cbssports.com", 0.76, "major_media", "usable", ("cbs sports",)),
    SourceProfile("srf.ch", 0.74, "local_media", "usable", ("srf",)),
    SourceProfile("20min.ch", 0.66, "local_media", "weak", ("20 minuten", "20min")),
)


def assess_source_reliability(
    url: str | None,
    source_name: str | None = None,
    *,
    published_at: str | None = None,
    kickoff_at: dt.datetime | None = None,
) -> SourceReliability:
    """Return a deterministic reliability profile for a public article.

    Timing is intentionally a small nudge: source quality matters most, while
    stale pregame articles or suspiciously late "pregame" rows are discounted.
    """

    domain = _domain(url)
    source = (source_name or "").casefold()
    profile = _profile_for(domain, source)
    reasons: list[str] = []
    if profile is None:
        score = 0.50
        tier = "unknown"
        strength = "untrusted"
        reasons.append("unknown_publisher")
    else:
        score = profile.score
        tier = profile.tier
        strength = profile.strength
        reasons.append(f"profile:{profile.domain}")

    timing_adjustment = _timing_adjustment(published_at, kickoff_at)
    if timing_adjustment:
        score += timing_adjustment
        reasons.append("freshness_boost" if timing_adjustment > 0 else "freshness_penalty")

    score = _clamp(score, 0.0, 1.0)
    return SourceReliability(
        score=score,
        bucket=source_reliability_bucket(score),
        tier=tier,
        strength=strength,
        reasons=tuple(reasons),
    )


def source_reliability_bucket(score: float) -> str:
    if score >= 0.90:
        return "official_or_wire"
    if score >= 0.80:
        return "high"
    if score >= 0.70:
        return "usable"
    if score >= 0.60:
        return "weak"
    return "untrusted"


def _profile_for(domain: str, source_name: str) -> SourceProfile | None:
    for profile in SOURCE_PROFILES:
        if domain.endswith(profile.domain):
            return profile
    for profile in SOURCE_PROFILES:
        if any(alias in source_name for alias in profile.aliases):
            return profile
    return None


def _timing_adjustment(published_at: str | None, kickoff_at: dt.datetime | None) -> float:
    if not published_at or kickoff_at is None:
        return 0.0
    published = _parse_datetime(published_at)
    if published is None:
        return 0.0
    hours_before = (kickoff_at - published).total_seconds() / 3600
    if 0 <= hours_before <= 36:
        return 0.03
    if hours_before > 120:
        return -0.06
    return 0.0


def _parse_datetime(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _domain(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.casefold().removeprefix("www.")


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
