"""Process timing and memory helpers for run observability.

The 2026-07-09 OOM incident was invisible in application logs: stdout had no
timestamps, the phases outside the plugin workflow had no durations, and
memory was never reported anywhere. These helpers give long-running commands
timestamped, grep-able phase lines plus structured rows for trending.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import resource
import sys
import time
from typing import Any


def current_rss_mb() -> float | None:
    """Resident set size in MB, or None when /proc is unavailable (macOS)."""

    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def peak_rss_mb() -> float:
    """Peak resident set size of this process in MB."""

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS reports bytes.
    if sys.platform.startswith("linux"):
        return round(peak / 1024, 1)
    return round(peak / (1024 * 1024), 1)


def log_line(message: str) -> None:
    """Print one timestamped log line so cron logs are correlatable."""

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {message}", flush=True)


@contextlib.contextmanager
def timed_phase(name: str, sink: list[dict[str, Any]] | None = None):
    """Time a run phase, log it with memory movement, and record it in sink."""

    started = time.perf_counter()
    rss_before = current_rss_mb()
    try:
        yield
    finally:
        duration_seconds = round(time.perf_counter() - started, 2)
        rss_after = current_rss_mb()
        rss_delta = (
            round(rss_after - rss_before, 1)
            if rss_before is not None and rss_after is not None
            else None
        )
        entry = {
            "phase": name,
            "duration_seconds": duration_seconds,
            "rss_mb_before": rss_before,
            "rss_mb_after": rss_after,
            "rss_mb_delta": rss_delta,
        }
        if sink is not None:
            sink.append(entry)
        memory_text = ""
        if rss_after is not None and rss_delta is not None:
            memory_text = f" rss={rss_after:.0f}MB ({rss_delta:+.0f}MB)"
        log_line(f"phase={name} duration={duration_seconds:.1f}s{memory_text}")
