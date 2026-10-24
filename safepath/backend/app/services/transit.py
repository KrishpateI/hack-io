"""Optional CTA transit alerts (Req 5.2, 5.3, 5.4).

Endpoint: the official CTA Customer Alerts API
``https://lapi.transitchicago.com/api/1.0/alerts.aspx?outputType=JSON&activeonly=true``.
Verified against the CTA developer pages: this API is *keyless* (only the Train/Bus Tracker APIs
need a key). The feature is therefore an explicit opt-in: it is active only when
``SAFEPATH_CTA_ALERTS_KEY`` (any non-empty value; never sent to CTA) or
``SAFEPATH_CTA_ALERTS_ENABLED=true`` is set. Otherwise the service reports ``available: false``.

Safety rule: alerts are route/service level data. Nothing here infers station entrance safety or
physical accessibility; entrance safety is ``Unknown`` unless an Official_Notice or an
Officially_Verified report states otherwise (passed in explicitly by the caller).
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

CTA_ALERTS_URL = "https://lapi.transitchicago.com/api/1.0/alerts.aspx?outputType=JSON&activeonly=true"
TIMEOUT_SECONDS = 10.0
UNAVAILABLE_MESSAGE = "Live transit alerts are unavailable"
SOURCE_ID = "CTA-CUSTOMER-ALERTS-API"
CACHE_TABLE = "transit_alerts_cache"
CACHE_KEY = {"pk": "cta_alerts", "sk": "latest"}
KEY_ENV = "SAFEPATH_CTA_ALERTS_KEY"
ENABLE_ENV = "SAFEPATH_CTA_ALERTS_ENABLED"
ENTRANCE_UNKNOWN = "Unknown"

Fetcher = Callable[[str, float], "str | bytes"]


class AlertsParseError(ValueError):
    """The CTA response was not the documented shape."""


def is_configured(env: Mapping[str, str] | None = None) -> bool:
    e = os.environ if env is None else env
    if (e.get(KEY_ENV) or "").strip():
        return True
    return (e.get(ENABLE_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def default_fetcher(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "SafePath-prototype"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        return resp.read()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _text(v: Any) -> str | None:
    if isinstance(v, dict):
        v = v.get("#cdata-section", v.get("#text"))
    if v is None:
        return None
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(v)))
    return re.sub(r"\s+", " ", s).strip() or None


def parse_alerts(raw: str | bytes | Mapping[str, Any], retrieved_at: str) -> list[dict[str, Any]]:
    """Parse a CTA Customer Alerts JSON body; each alert is labeled Current at ``retrieved_at``."""
    try:
        doc = raw if isinstance(raw, Mapping) else json.loads(raw)
        body = doc["CTAAlerts"]
        code = str(body.get("ErrorCode", "0"))
        if code != "0":
            raise AlertsParseError(f"CTA error {code}: {body.get('ErrorMessage')}")
        out = []
        for a in _as_list(body.get("Alert")):
            routes, stations = [], []
            for s in _as_list((a.get("ImpactedService") or {}).get("Service")):
                kind = s.get("ServiceType")
                item = {"id": str(s.get("ServiceId")), "name": s.get("ServiceName")}
                if kind in ("R", "B"):
                    routes.append({**item, "mode": "train" if kind == "R" else "bus"})
                elif kind == "T":
                    stations.append(item)
            out.append({
                "alert_id": str(a["AlertId"]),
                "headline": _text(a.get("Headline")),
                "short_description": _text(a.get("ShortDescription")),
                "full_description": _text(a.get("FullDescription")),
                "impact": a.get("Impact"),
                "severity_score": int(a["SeverityScore"]) if str(a.get("SeverityScore", "")).isdigit() else None,
                "major_alert": str(a.get("MajorAlert")) == "1",
                "event_start": a.get("EventStart"),
                "event_end": a.get("EventEnd"),
                "url": _text(a.get("AlertURL")),
                "routes": routes,
                "stations": stations,
                "data_classification": "Current",
                "retrieved_at": retrieved_at,
                "source_id": SOURCE_ID,
            })
        return out
    except AlertsParseError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AlertsParseError(f"unexpected CTA alerts response: {exc}") from exc


def join_stations(
    alerts: Iterable[Mapping[str, Any]],
    stations: Iterable[Mapping[str, Any]],
    official_entrance_statements: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Alerts by route -> stations served (graph ``cta_stations`` features), plus alerts naming a station.

    Pure association; no safety or accessibility claim. ``entrance_safety_status`` is ``Unknown``
    unless ``official_entrance_statements`` (from an Official_Notice / Officially_Verified report)
    has an entry for that station id.
    """
    official = official_entrance_statements or {}
    result: dict[str, dict[str, Any]] = {}
    alerts = list(alerts)
    for feat in stations:
        p = feat.get("properties", feat)
        sid, name = str(p.get("id")), p.get("name")
        served = {str(r).casefold() for r in p.get("routes") or []}
        ids = []
        for a in alerts:
            by_route = any(str(r.get("name", "")).casefold() in served for r in a.get("routes", []))
            by_name = any(str(s.get("id")) == sid or str(s.get("name", "")).casefold() == str(name).casefold()
                          for s in a.get("stations", []))
            if by_route or by_name:
                ids.append(a["alert_id"])
        if ids:
            result[sid] = {
                "station_id": sid,
                "name": name,
                "alert_ids": ids,
                "entrance_safety_status": official.get(sid, ENTRANCE_UNKNOWN),
                "note": "Route-level alerts only; entrance safety and accessibility are not inferred.",
            }
    return result


class TransitAlertsService:
    def __init__(
        self,
        repo: Any = None,
        fetcher: Fetcher | None = None,
        configured: Callable[[], bool] = is_configured,
        clock: Callable[[], str] = _now,
        url: str = CTA_ALERTS_URL,
    ) -> None:
        self.repo, self.fetcher, self.configured, self.clock, self.url = repo, fetcher or default_fetcher, configured, clock, url
        self._last: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def _load_cached(self) -> dict[str, Any] | None:
        if self._last:
            return self._last
        if self.repo is not None:
            try:
                row = self.repo.get(CACHE_TABLE, dict(CACHE_KEY))
                if row and row.get("alerts") is not None:
                    return {"retrieved_at": row["retrieved_at"], "alerts": row["alerts"]}
            except Exception:  # cache is best effort
                return None
        return None

    def _store(self, snap: dict[str, Any]) -> None:
        self._last = snap
        if self.repo is not None:
            try:
                self.repo.put(CACHE_TABLE, {**CACHE_KEY, **snap})
            except Exception:
                pass

    def get_alerts(self) -> dict[str, Any]:
        if not self.configured():
            return self._unavailable(configured=False)
        with self._lock:
            try:
                retrieved_at = self.clock()
                alerts = parse_alerts(self.fetcher(self.url, TIMEOUT_SECONDS), retrieved_at)
            except Exception as exc:  # timeout, network, HTTP, parse
                cached = self._load_cached()
                if cached:
                    return {
                        "available": True, "configured": True, "stale": True,
                        "data_classification": "Current", "retrieved_at": cached["retrieved_at"],
                        "alerts": cached["alerts"], "message": "Showing last retrieved alerts; live refresh failed",
                        "error": type(exc).__name__,
                    }
                return self._unavailable(configured=True, error=type(exc).__name__)
            snap = {"retrieved_at": retrieved_at, "alerts": alerts}
            self._store(snap)
            return {
                "available": True, "configured": True, "stale": False,
                "data_classification": "Current", "retrieved_at": retrieved_at,
                "alerts": alerts, "message": None, "error": None,
            }

    @staticmethod
    def _unavailable(configured: bool, error: str | None = None) -> dict[str, Any]:
        return {
            "available": False, "configured": configured, "stale": False, "data_classification": None,
            "retrieved_at": None, "alerts": [], "message": UNAVAILABLE_MESSAGE, "error": error,
        }
