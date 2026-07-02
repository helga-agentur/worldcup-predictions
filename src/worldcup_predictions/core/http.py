"""Small HTTP helper used by source plugins."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from worldcup_predictions.core.config import DEFAULT_CONFIG
from worldcup_predictions.core.constants import PROJECT_USER_AGENT


@dataclass(frozen=True)
class HttpResponse:
    """HTTP response body plus normalized headers."""

    body: str
    headers: dict[str, str]
    status_code: int = 200

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass(frozen=True)
class HttpClient:
    """Thin urllib wrapper with consistent defaults."""

    timeout_seconds: int = DEFAULT_CONFIG.source_defaults.timeout_seconds
    user_agent: str = PROJECT_USER_AGENT

    def get_text(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> HttpResponse:
        url = endpoint
        if params:
            query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
            url = f"{endpoint}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "*/*",
                "User-Agent": self.user_agent,
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds or self.timeout_seconds) as response:  # noqa: S310
                body = response.read().decode("utf-8", errors="replace")
                response_headers = dict(response.headers.items())
                status_code = int(getattr(response, "status", 200) or 200)
        except urllib.error.HTTPError as exc:
            if exc.code != 304:
                raise
            body = ""
            response_headers = dict(exc.headers.items()) if exc.headers is not None else {}
            status_code = 304
        return HttpResponse(body=body, headers=response_headers, status_code=status_code)

    def get_json(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> tuple[Any, dict[str, str]]:
        response = self.get_text(
            endpoint,
            params,
            headers={"Accept": "application/json", **(headers or {})},
            timeout_seconds=timeout_seconds,
        )
        return response.json(), response.headers
