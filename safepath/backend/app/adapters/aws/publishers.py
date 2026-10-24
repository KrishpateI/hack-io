"""Realtime publishers for AWS (Req 25.4, 29.3, 29.5).

* ``AppSyncEventsPublisher``: HTTP publish to an AppSync Events API (``POST <endpoint>/event``)
  on channel ``/<namespace>/<incident_id>``, authorised with an API key (``x-api-key``) or SigV4
  (IAM, service ``appsync``).
* ``ApiGwWebSocketPublisher``: labeled substitute used when AppSync Events is unavailable
  (``realtime=apigw-ws``). Connections are stored in the ``connections`` table by the WebSocket
  Lambda; events go out with ``post_to_connection`` and gone connections are pruned.

``publish`` is best effort: it never raises and never blocks a request beyond a short timeout
(clients recover missed events through ``GET /api/events?since=``).
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from app.adapters.ports import AdapterNotAvailable, Event, Repository

log = logging.getLogger(__name__)

PUBLISH_TIMEOUT_SECONDS = 3.0
CONNECTIONS_TABLE = "connections"
CONNECTION_TTL_SECONDS = 2 * 60 * 60  # API Gateway idle timeout is 10 min, hard limit 2 h
_SEGMENT = re.compile(r"[^A-Za-z0-9_-]")

Transport = Callable[[str, bytes, Mapping[str, str], float], int]


def event_envelope(event: Event) -> dict[str, Any]:
    """Same JSON envelope as the local WebSocket/SSE channel and the catch-up endpoint."""
    return {"event_id": event.id, "incident_id": event.incident_id, "type": event.type,
            "payload": dict(event.payload), "created_at": event.created_at}


def _urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> int:
    req = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https only
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


class AppSyncEventsPublisher:
    def __init__(self, endpoint: str, *, namespace: str = "incidents", api_key: str | None = None,
                 region: str | None = None, credentials: Callable[[], Any] | None = None,
                 transport: Transport | None = None, timeout: float = PUBLISH_TIMEOUT_SECONDS) -> None:
        url = endpoint.strip().rstrip("/")
        parts = urlsplit(url if "://" in url else f"https://{url}")
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError("AppSync Events endpoint must be an https URL")
        path = parts.path.rstrip("/")
        self.url = f"https://{parts.netloc}{path if path.endswith('/event') else path + '/event'}"
        self.namespace = _SEGMENT.sub("", namespace) or "incidents"
        self._api_key = api_key or None
        self._region = region
        self._credentials = credentials
        self._transport = transport or _urllib_transport
        self._timeout = timeout
        if not self._api_key and not (region and credentials):
            raise ValueError("AppSync Events needs an API key or (region + IAM credentials)")
        self.failures = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **kw: Any) -> "AppSyncEventsPublisher":
        from app.adapters.aws.session import env_of, region_of

        e = env_of(env)
        endpoint = (e.get("SAFEPATH_APPSYNC_EVENTS_ENDPOINT") or "").strip()
        if not endpoint:
            raise AdapterNotAvailable(
                "AppSync Events needs SAFEPATH_APPSYNC_EVENTS_ENDPOINT; if AppSync Events is "
                "unavailable use the labeled substitute SAFEPATH_PUBLISHER=apigw")
        api_key = (e.get("SAFEPATH_APPSYNC_API_KEY") or "").strip() or None
        region = region_of(e)
        credentials = kw.pop("credentials", None)
        if not api_key:
            if not region:
                raise AdapterNotAvailable("AppSync Events with IAM needs AWS_REGION")
            credentials = credentials or _default_credentials
        try:
            return cls(endpoint, namespace=e.get("SAFEPATH_APPSYNC_NAMESPACE", "incidents"),
                       api_key=api_key, region=region, credentials=credentials, **kw)
        except ValueError as exc:
            raise AdapterNotAvailable(str(exc)) from exc

    def channel(self, incident_id: str) -> str:
        return f"/{self.namespace}/{_SEGMENT.sub('_', incident_id)[:64]}"

    def _headers(self, body: bytes) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
            return headers
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        creds = self._credentials() if self._credentials else None
        if creds is None:
            raise RuntimeError("no AWS credentials available to sign the AppSync request")
        req = AWSRequest(method="POST", url=self.url, data=body, headers=headers)
        SigV4Auth(creds, "appsync", self._region).add_auth(req)
        return {k: str(v) for k, v in req.headers.items()}

    def publish(self, incident_id: str, event: Event) -> None:
        try:
            body = json.dumps({
                "channel": self.channel(incident_id),
                "events": [json.dumps(event_envelope(event), separators=(",", ":"), default=str)],
            }).encode("utf-8")
            status = self._transport(self.url, body, self._headers(body), self._timeout)
            if status >= 300:
                self.failures += 1
                log.warning("AppSync Events publish returned HTTP %s for event %s", status, event.id)
        except Exception:  # best effort: never fail the request that produced the event
            self.failures += 1
            log.exception("AppSync Events publish failed for event %s", event.id)


def _default_credentials() -> Any:
    import boto3

    creds = boto3.Session().get_credentials()
    return creds.get_frozen_credentials() if creds is not None else None


class ApiGwWebSocketPublisher:
    """Fan out to the WebSocket connections registered for an incident.

    ``repo`` stores ``connections`` rows (see :func:`register_connection`); ``client`` is a
    boto3 ``apigatewaymanagementapi`` client bound to the WebSocket API stage endpoint.
    """

    def __init__(self, repo: Repository, client: Any, *, max_connections: int = 1000) -> None:
        self._repo = repo
        self._c = client
        self._max = max_connections
        self.failures = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, repo: Repository | None = None,
                 client: Any = None) -> "ApiGwWebSocketPublisher":
        from app.adapters.aws.session import env_of, make_client

        e = env_of(env)
        if client is None:
            endpoint = (e.get("SAFEPATH_WS_ENDPOINT") or "").strip()
            if not endpoint.startswith("https://"):
                raise AdapterNotAvailable(
                    "WebSocket fallback needs SAFEPATH_WS_ENDPOINT "
                    "(https://<api-id>.execute-api.<region>.amazonaws.com/<stage>)")
            client = make_client("apigatewaymanagementapi", e, endpoint=False, endpoint_url=endpoint)
        if repo is None:
            from app.adapters.aws.dynamo import DynamoRepository

            repo = DynamoRepository.from_env(e)
        return cls(repo, client)

    def publish(self, incident_id: str, event: Event) -> None:
        try:
            data = json.dumps(event_envelope(event), separators=(",", ":"), default=str).encode("utf-8")
            rows = self._repo.query(CONNECTIONS_TABLE, {"pk": incident_id}, limit=self._max)
        except Exception:
            self.failures += 1
            log.exception("WebSocket publish failed for event %s", event.id)
            return
        for row in rows:
            conn = row.get("connection_id") or row.get("sk")
            if not conn:
                continue
            try:
                self._c.post_to_connection(ConnectionId=conn, Data=data)
            except Exception as exc:
                if _is_gone(exc):
                    self._prune(incident_id, conn)
                else:
                    self.failures += 1
                    log.warning("WebSocket post_to_connection failed: %s", type(exc).__name__)

    def _prune(self, incident_id: str, connection_id: str) -> None:
        try:
            unregister_connection(self._repo, connection_id, incident_id)
        except Exception:
            log.warning("could not prune gone connection")


def _is_gone(exc: Exception) -> bool:
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code == "GoneException" or status == 410 or type(exc).__name__ == "GoneException"


def register_connection(repo: Repository, connection_id: str, incident_id: str, now: float) -> None:
    """Store both lookup directions: incident -> connections and connection -> incident."""
    ttl = int(now) + CONNECTION_TTL_SECONDS
    repo.put(CONNECTIONS_TABLE, {"pk": incident_id, "sk": connection_id,
                                 "connection_id": connection_id, "incident_id": incident_id,
                                 "ttl": ttl})
    repo.put(CONNECTIONS_TABLE, {"pk": f"conn#{connection_id}", "sk": "incident",
                                 "incident_id": incident_id, "ttl": ttl})


def unregister_connection(repo: Repository, connection_id: str, incident_id: str | None = None) -> None:
    if incident_id is None:
        row = repo.get(CONNECTIONS_TABLE, {"pk": f"conn#{connection_id}", "sk": "incident"})
        incident_id = row.get("incident_id") if row else None
    if incident_id:
        repo.delete_where(CONNECTIONS_TABLE, {"pk": incident_id, "sk": connection_id})
    repo.delete_where(CONNECTIONS_TABLE, {"pk": f"conn#{connection_id}", "sk": "incident"})
