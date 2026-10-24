"""DemoService: SIMULATED demo scenario control behind the Demo_Admin_Console (Req 37-40).

Wraps :mod:`app.services.demo_data` (seed, apply_step, reset) and the real report, verification,
messaging and overlay services so every effect fans out to connected clients as a normal event.
Everything it creates is flagged ``simulation_flag`` and labeled SIMULATED.

Seeding strategy (startup time): ``create_app`` with ``SAFEPATH_DEMO_MODE=true`` starts a daemon
thread that seeds the scenarios, so startup returns immediately (seeding the real graph takes
~9 s). Any demo request that needs the data calls :meth:`ensure_seeded`, which waits on the same
lock, so a request never sees a half-seeded state.

Security: demo mode creates a Moderator-capable token endpoint (local profile only). It must be off
in any shared or production deployment; the router is inert (404) when demo mode is off.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.adapters.ports import Principal, Role
from app.services import demo_data as dd

log = logging.getLogger(__name__)

NOTICE = ("SIMULATED demo control. Every incident, closure, report, user and message created here is "
          "invented for the SafePath demo and is not real or official.")
PROGRESS_PK = "progress"
RESET_LIMIT_SECONDS = 30.0
SCENARIO_RESET = "scenario_reset"
STAIRWELL = "blocked_stairwell"
STAIRWELL_ACTORS = ("A", "B", "C")
STAIRWELL_ACTIONS = ("report", "corroborate", "dispute", "message", "confirm", "resolve")
MODERATOR_ACTIONS = ("confirm", "resolve")


class DemoError(Exception):
    """Carries the HTTP status the router should answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoService:
    def __init__(self, app: Any) -> None:
        self.app = app
        self._lock = threading.RLock()
        self.seed_state = "not_started"  # not_started | seeding | seeded | failed
        self.seed_error: str | None = None
        self.graph_info: dict[str, Any] | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def settings(self):
        return self.app.state.settings

    @property
    def repo(self):
        repo = getattr(self.app.state, "repository", None)
        if repo is None:
            raise DemoError(503, "storage unavailable")
        return repo

    @property
    def bus(self):
        from app.api.routers.events import get_event_bus

        bus = get_event_bus(self.app)
        if bus is None:
            raise DemoError(503, "event bus unavailable")
        return bus

    def _on_change(self):
        provider = getattr(self.app.state, "routing_context_provider", None)
        return getattr(provider, "invalidate", None)

    def _graph(self):
        """The app's routing graph; the SIMULATED fixture graph when none loaded (recorded)."""
        from app.routing.runtime import get_graph

        g = get_graph(self.app)
        if g.loaded:
            return g
        return dd.load_demo_graph()[0]

    def definitions(self) -> dict[str, Any]:
        return dd.load_definitions()

    def _check_id(self, scenario_id: str) -> None:
        if scenario_id not in dd.scenario_ids():
            raise DemoError(404, f"unknown scenario {scenario_id!r}")

    # ------------------------------------------------------------------ seeding
    def start_background_seed(self) -> None:
        if self._thread is not None:
            return

        def run() -> None:
            try:
                self.ensure_seeded()
            except Exception:  # recorded in seed_error; requests retry on demand
                log.exception("background demo seeding failed")

        self._thread = threading.Thread(target=run, name="safepath-demo-seed", daemon=True)
        self._thread.start()

    def ensure_seeded(self, scenarios: list[str] | None = None) -> dd.SeedResult:
        with self._lock:
            self.seed_state = "seeding"
            try:
                graph = self._graph()
                result = dd.seed(self.repo, graph=graph, scenarios=scenarios, bus=self.bus,
                                 on_change=self._on_change())
            except Exception as exc:
                self.seed_state, self.seed_error = "failed", f"{type(exc).__name__}: {exc}"
                raise DemoError(503, f"demo seeding failed: {exc}") from exc
            self.seed_state, self.seed_error = "seeded", None
            self.graph_info = result.graph
            return result

    # ------------------------------------------------------------------ progress
    def _progress(self, sid: str) -> list[str]:
        row = self.repo.get(dd.DEMO_TABLE, {"pk": PROGRESS_PK, "sk": sid})
        return list(row["applied"]) if row else []

    def _mark(self, sid: str, step_id: str) -> None:
        applied = self._progress(sid)
        if step_id not in applied:
            applied.append(step_id)
        self.repo.put(dd.DEMO_TABLE, {"pk": PROGRESS_PK, "sk": sid, "applied": applied,
                                      "updated_at": _now().isoformat()})

    def _clear_progress(self, sid: str) -> int:
        return self.repo.delete_where(dd.DEMO_TABLE, {"pk": PROGRESS_PK, "sk": sid})

    # ------------------------------------------------------------------ views
    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        sid = row["scenario_id"]
        applied = self._progress(sid)
        steps = [{**{k: v for k, v in s.items()}, "status": "applied" if s["id"] in applied else "pending"}
                 for s in sorted(row["steps"], key=lambda s: s.get("order", 0))]
        pending = [s["id"] for s in steps if s["status"] == "pending"]
        return {
            "scenario_id": sid, "incident_id": row["incident_id"], "name": row["name"],
            "incident_type": row["incident_type"], "optional": row.get("optional", False),
            "simulation_flag": True, "classification": "Simulated", "label": dd.SIMULATED,
            "seeded": True, "pristine": dd.is_seeded_state(self.repo, sid) and not applied,
            "graph": row.get("graph"), "origin": row.get("origin"), "destination": row.get("destination"),
            "steps": steps, "next_step": pending[0] if pending else None,
            "applied_steps": applied, "notice": NOTICE,
        }

    def list_scenarios(self) -> dict[str, Any]:
        self.ensure_seeded()
        rows = {r["scenario_id"]: r for r in dd.list_scenarios(self.repo)}
        out = []
        for sid in dd.scenario_ids():
            if sid in rows:
                out.append(self._view(rows[sid]))
            else:
                out.append({"scenario_id": sid, "seeded": False, "simulation_flag": True, "label": dd.SIMULATED})
        return {"simulated": True, "notice": NOTICE, "scenarios": out}

    def status(self) -> dict[str, Any]:
        s = self.settings
        out: dict[str, Any] = {"simulated": True, "notice": NOTICE, "demo_mode": bool(s.demo_mode),
                               "profile": s.profile}
        if not s.demo_mode:
            return out
        repo = getattr(self.app.state, "repository", None)
        rows = {r["scenario_id"]: r for r in dd.list_scenarios(repo)} if repo is not None else {}
        graph = next((r.get("graph") for r in rows.values() if r.get("graph")), self.graph_info)
        out.update({
            "seed_state": self.seed_state, "seed_error": self.seed_error,
            "scenarios": [{"scenario_id": sid, "seeded": sid in rows} for sid in dd.scenario_ids()],
            "scenarios_seeded": len(rows) == len(dd.scenario_ids()),
            "graph": graph,
            "graph_used": (graph or {}).get("used"),
            "moderator_sessions_available": self.moderator_sessions_allowed(),
        })
        return out

    # ------------------------------------------------------------------ start / steps
    def start(self, sid: str) -> dict[str, Any]:
        self._check_id(sid)
        self.ensure_seeded([sid])
        row = dd.get_scenario(self.repo, sid)
        if row is None:
            raise DemoError(500, "scenario could not be seeded")
        inc = dd.IncidentService(self.repo, self.bus).get_incident(row["incident_id"])
        view = self._view(row)
        view.update({"incident_status": inc.status if inc else None, "activated": bool(inc and inc.status == "active")})
        return view

    def client_action(self, row: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
        """What the console (or the acting user) must do for a client-driven step."""
        kind, resolved = step["kind"], row["resolved"]
        base = {"incident_id": row["incident_id"], "simulated": True}
        if kind == "activate_incident":
            return {**base, "do": "open_incident"}
        if kind == "show_layer":
            return {**base, "do": "show_layer", "layer": step.get("layer"),
                    "data": resolved.get(step.get("layer", ""))}
        if kind == "calculate_routes":
            body: dict[str, Any] = {"incident_id": row["incident_id"], **(row.get("route_request") or {})}
            if row.get("origin"):
                body["origin"] = {k: row["origin"][k] for k in ("latitude", "longitude")}
            if row.get("destination"):
                body["destination"] = {k: row["destination"][k] for k in ("latitude", "longitude")}
            action: dict[str, Any] = {**base, "do": "calculate_routes", "endpoint": "POST /api/routes/calculate",
                                      "body": body, "expect": step.get("expect")}
            if row["scenario_id"] == STAIRWELL:
                actors = step.get("actors") or [step.get("actor", "A")]
                action["bodies_by_actor"] = {
                    a: {**body, **self._indoor_origin(row, a)} for a in actors}
            return action
        if kind == "load_indoor_graph":
            return {**base, "do": "load_indoor_graph", "building_id": resolved.get("building_id"),
                    "users": resolved.get("users")}
        if kind in ("submit_report", "confirm_report", "post_message"):
            act = {"submit_report": "report", "confirm_report": "corroborate",
                   "post_message": "message"}[kind]
            return {**base, "do": "act", "endpoint": f"POST /api/demo/scenarios/{row['scenario_id']}/act",
                    "body": {"actor": step.get("actor"), "action": act,
                             **({"content": step["content"]} if step.get("content") else {})}}
        if kind == "observe":
            return {**base, "do": "observe", "actor": step.get("actor"),
                    "within_seconds": step.get("within_seconds")}
        return {**base, "do": kind}

    @staticmethod
    def _indoor_origin(row: dict[str, Any], actor: str) -> dict[str, Any]:
        for u in row["resolved"].get("users", []):
            if u["key"] == actor:
                return {"origin": {"latitude": u["latitude"], "longitude": u["longitude"]},
                        "origin_indoor_node_id": u["indoor_node_id"]}
        return {}

    def apply_step(self, sid: str, step_id: str) -> dict[str, Any]:
        self._check_id(sid)
        self.ensure_seeded([sid])
        row = dd.get_scenario(self.repo, sid)
        if row is None:
            raise DemoError(404, f"scenario {sid!r} is not seeded")
        step = next((s for s in row["steps"] if s["id"] == step_id), None)
        if step is None:
            raise DemoError(404, f"scenario {sid!r} has no step {step_id!r}")
        started = time.monotonic()
        try:
            result = dd.apply_step(self.repo, sid, step_id, bus=self.bus, on_change=self._on_change())
        except dd.DemoDataError as exc:
            raise DemoError(400, str(exc)) from exc
        self._mark(sid, step_id)
        result.update({
            "scenario_id": sid, "incident_id": row["incident_id"], "simulation_flag": True,
            "label": dd.SIMULATED, "notice": NOTICE,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        })
        if not result.get("applied"):
            result["client_action"] = self.client_action(row, step)
        else:
            result["client_action"] = (self.client_action(row, step) if step["kind"] == "calculate_routes" else None)
        return result

    def advance(self, sid: str) -> dict[str, Any]:
        self._check_id(sid)
        self.ensure_seeded([sid])
        row = dd.get_scenario(self.repo, sid)
        if row is None:
            raise DemoError(404, f"scenario {sid!r} is not seeded")
        applied = self._progress(sid)
        nxt = next((s for s in sorted(row["steps"], key=lambda s: s.get("order", 0))
                    if s["id"] not in applied), None)
        if nxt is None:
            return {"scenario_id": sid, "done": True, "simulation_flag": True, "notice": NOTICE}
        return {**self.apply_step(sid, nxt["id"]), "done": False}

    # ------------------------------------------------------------------ reset
    def _publish_reset(self, sid: str, incident_id: str, removed: dict[str, int] | None) -> None:
        self.bus.commit([], incident_id, SCENARIO_RESET, {
            "scenario_id": sid, "incident_id": incident_id, "simulated": True, "notice": NOTICE,
            "reset_at": _now().isoformat(), "removed": removed or {}})

    def reset(self, sid: str) -> dict[str, Any]:
        """Restore one scenario to its seeded state. A no-op when it already is (Req 40.7)."""
        self._check_id(sid)
        started = time.monotonic()
        with self._lock:
            row = dd.get_scenario(self.repo, sid)
            iid = next(s["incident_id"] for s in self.definitions()["scenarios"] if s["scenario_id"] == sid)
            if row is not None and dd.is_seeded_state(self.repo, sid):
                cleared = self._clear_progress(sid)
                return self._reset_result(sid, iid, False, started, {}, progress_cleared=bool(cleared))
            removed = dd.reset_scenario(self.repo, sid, on_change=self._on_change())
            self._clear_progress(sid)
            self.ensure_seeded([sid])
            self._publish_reset(sid, iid, removed)
        return self._reset_result(sid, iid, True, started, removed)

    def reset_all(self) -> dict[str, Any]:
        started = time.monotonic()
        with self._lock:
            ids = dd.scenario_ids()
            dirty = [s for s in ids if not (dd.get_scenario(self.repo, s) and dd.is_seeded_state(self.repo, s))]
            if not dirty:
                for s in ids:
                    self._clear_progress(s)
                return {"changed": False, "scenarios": [], "simulation_flag": True, "notice": NOTICE,
                        "elapsed_ms": int((time.monotonic() - started) * 1000), "within_limit": True}
            removed = dd.reset_all(self.repo, on_change=self._on_change())
            for s in ids:
                self._clear_progress(s)
            self.ensure_seeded()
            incs = {s["scenario_id"]: s["incident_id"] for s in self.definitions()["scenarios"]}
            for s in dirty:
                self._publish_reset(s, incs[s], removed.get(s))
        elapsed = time.monotonic() - started
        return {"changed": True, "scenarios": dirty, "simulation_flag": True, "notice": NOTICE,
                "elapsed_ms": int(elapsed * 1000), "within_limit": elapsed <= RESET_LIMIT_SECONDS}

    def _reset_result(self, sid: str, iid: str, changed: bool, started: float, removed: dict[str, int],
                      progress_cleared: bool = False) -> dict[str, Any]:
        elapsed = time.monotonic() - started
        return {"scenario_id": sid, "incident_id": iid, "changed": changed, "pristine": True,
                "removed": removed, "progress_cleared": progress_cleared, "event": SCENARIO_RESET if changed else None,
                "elapsed_ms": int(elapsed * 1000), "within_limit": elapsed <= RESET_LIMIT_SECONDS,
                "simulation_flag": True, "label": dd.SIMULATED, "notice": NOTICE}

    # ------------------------------------------------------------------ ad hoc closures
    def _demo_incident(self, incident_id: str):
        inc = dd.IncidentService(self.repo, self.bus).get_incident(incident_id)
        if inc is None:
            raise DemoError(404, "incident not found")
        if not inc.simulation_flag:
            raise DemoError(403, "demo controls only change SIMULATED incidents")
        return inc

    def add_closure(self, incident_id: str, *, edge_ids: list[str], geometry: dict | None,
                    kind: str) -> dict[str, Any]:
        from app.services.wiring import get_overlay_service

        self._demo_incident(incident_id)
        if not edge_ids and not geometry:
            raise DemoError(400, "edge_ids or geometry is required")
        if edge_ids:
            known = set(self._graph().edge_ids)
            bad = [e for e in edge_ids if e not in known]
            if bad:
                raise DemoError(400, f"unknown edge ids: {bad[:5]}")
        rid = f"sim-adhoc-{int(time.time() * 1000):x}"
        version = get_overlay_service(self.app).add_closure(
            incident_id, edge_ids=edge_ids, geometry=geometry, kind=kind, simulated=True,
            source_record_id="SIMULATED-DEMO-SOURCE", restriction_id=rid)
        return {"restriction_id": rid, "incident_id": incident_id, "overlay_version": version,
                "simulation_flag": True, "label": dd.SIMULATED, "notice": NOTICE}

    def remove_closure(self, incident_id: str, restriction_id: str) -> dict[str, Any]:
        from app.services.overlays import RestrictionNotFound
        from app.services.wiring import get_overlay_service

        self._demo_incident(incident_id)
        try:
            version = get_overlay_service(self.app).remove_closure(incident_id, restriction_id)
        except RestrictionNotFound:
            raise DemoError(404, "restriction not found") from None
        return {"restriction_id": restriction_id, "incident_id": incident_id, "overlay_version": version,
                "removed": True, "simulation_flag": True, "label": dd.SIMULATED, "notice": NOTICE}

    # ------------------------------------------------------------------ demo sessions
    def moderator_sessions_allowed(self) -> bool:
        """Moderator demo tokens exist only with demo mode on AND the local profile."""
        return bool(self.settings.demo_mode) and self.settings.profile == "local"

    def create_session(self, kind: str, actor: str | None = None) -> dict[str, Any]:
        """A pre-roled token for the console. The role comes from ``kind`` (a fixed server-side
        choice), never from a client-supplied role claim."""
        auth = getattr(self.app.state, "auth", None)
        accounts = getattr(self.app.state, "accounts", None)
        if auth is None:
            raise DemoError(503, "authentication service unavailable")
        if kind == "guest":
            g = auth.issue_guest()
            token, expires, subject = g.token, g.expires_at, g.principal.subject
        elif kind == "user":
            subject = None
            if actor is not None:
                subject = self._actor_user_id(actor)
            subject = subject or f"sim-demo-user-{int(time.time() * 1000):x}"
            token, expires = auth.issue_token(subject)
            if accounts is not None:
                accounts.ensure_user(subject, Role.USER, f"SIMULATED {subject}")
        elif kind == "moderator":
            if not self.moderator_sessions_allowed():
                raise DemoError(403, "moderator demo sessions need demo mode and the local profile")
            if not hasattr(auth, "grant_role"):
                raise DemoError(403, "this auth provider does not support demo roles")
            subject = self.definitions()["moderator"]["user_id"]
            auth.grant_role(subject, Role.MODERATOR)
            token, expires = auth.issue_token(subject)
            if accounts is not None:
                accounts.ensure_user(subject, Role.MODERATOR, self.definitions()["moderator"]["display_name"])
        else:
            raise DemoError(400, "kind must be one of: guest, user, moderator")
        role = auth.role_for(subject, is_guest=(kind == "guest"))
        return {"token": token, "token_type": "bearer", "expires_at": expires.isoformat(),
                "subject": subject, "role": role.value, "simulation_flag": True, "label": dd.SIMULATED,
                "notice": NOTICE,
                "warning": "SIMULATED demo session. Demo mode must be OFF in production: with it on, "
                           "anyone who can reach this endpoint can obtain a moderator token."}

    def _actor_user_id(self, actor: str) -> str:
        row = dd.get_scenario(self.repo, STAIRWELL)
        if row is None:
            self.ensure_seeded([STAIRWELL])
            row = dd.get_scenario(self.repo, STAIRWELL)
        for u in (row or {}).get("resolved", {}).get("users", []):
            if u["key"] == actor:
                return u["user_id"]
        raise DemoError(400, f"actor must be one of: {', '.join(STAIRWELL_ACTORS)}")

    # ------------------------------------------------------------------ blocked stairwell actors
    def act(self, sid: str, caller: Principal, actor: str, action: str, *,
            content: str | None = None, report_id: str | None = None,
            message_type: str = "assistance_request") -> dict[str, Any]:
        """Perform one stairwell step as SIMULATED user A, B or C, or as the moderator, through the
        real report, verification and messaging services (events fan out to connected clients)."""
        from app.api.routers.messages import get_messaging_service
        from app.api.routers.reports import _detail_view, get_report_service, get_verification_service
        from app.communication.messaging import IncidentNotFound as MsgIncidentNotFound
        from app.services import verification as v
        from app.services.reports import IncidentNotFound as RepIncidentNotFound

        if sid != STAIRWELL:
            raise DemoError(400, f"scenario {sid!r} has no actor actions")
        if action not in STAIRWELL_ACTIONS:
            raise DemoError(400, "action must be one of: " + ", ".join(STAIRWELL_ACTIONS))
        self.ensure_seeded([sid])
        row = dd.get_scenario(self.repo, sid)
        if row is None:
            raise DemoError(404, "scenario is not seeded")
        iid = row["incident_id"]
        users = {u["key"]: u for u in row["resolved"]["users"]}
        is_mod_action = action in MODERATOR_ACTIONS
        if is_mod_action:
            if actor != "moderator":
                raise DemoError(400, f"action {action!r} is performed by actor 'moderator'")
            principal = caller
        else:
            if actor not in users:
                raise DemoError(400, "actor must be one of: " + ", ".join(STAIRWELL_ACTORS))
            principal = Principal(subject=users[actor]["user_id"], role=Role.USER)
        reports = get_report_service(self.app)
        verification = get_verification_service(self.app)
        feature = f"stair-{row['resolved']['stairwell']}"
        out: dict[str, Any] = {"scenario_id": sid, "incident_id": iid, "actor": actor, "action": action,
                               "simulation_flag": True, "label": dd.SIMULATED, "notice": NOTICE}

        def current_report_id() -> str:
            if report_id:
                return report_id
            rows = self.repo.query("reports", {"pk": iid}, limit=1000)
            if not rows:
                raise DemoError(409, "no report exists yet; run actor A 'report' first")
            return sorted(rows, key=lambda r: r["created_at"])[0]["report_id"]

        try:
            if action == "report":
                u = users[actor]
                res = reports.create(principal, {
                    "incident_id": iid, "category": "inaccessible stairwell", "indoor_feature_id": feature,
                    "description": content or f"SIMULATED: Stairwell {row['resolved']['stairwell']} is blocked.",
                    "client_report_id": f"sim-stairwell-report-{actor}"})
                out["created"] = res.created
                out["report"] = _detail_view(verification, verification.get_report(res.report.report_id))
                out["indoor_node_id"] = u["indoor_node_id"]
            elif action in ("corroborate", "dispute"):
                res = verification.vote(principal, current_report_id(), "confirm" if action == "corroborate" else "dispute")
                out["status_changed"] = res.status_changed
                out["report"] = _detail_view(verification, res.report)
            elif action == "message":
                rec = get_messaging_service(self.app).create(principal, {
                    "incident_id": iid, "message_type": message_type,
                    "content": content or "SIMULATED: I need help reaching an exit from Floor 2."})
                out["message_id"] = rec.message_id
                out["message_type"] = rec.message_type
            else:  # confirm / resolve (moderator)
                if principal.role not in (Role.MODERATOR, Role.AUTHORIZED_OFFICIAL):
                    raise DemoError(403, "insufficient role for this operation")
                res_m = verification.moderate(principal, current_report_id(),
                                              "verify" if action == "confirm" else "resolve",
                                              reason="SIMULATED demo moderator action")
                out["report"] = _detail_view(verification, res_m.report)
                out["overlay_version"] = res_m.overlay_version
                out["routes_changed"] = res_m.overlay_version is not None
        except DemoError:
            raise
        except (RepIncidentNotFound, MsgIncidentNotFound):
            raise DemoError(404, "incident not found") from None
        except v.ReportNotFound:
            raise DemoError(404, "report not found") from None
        except v.OwnReportVote:
            raise DemoError(403, "a user cannot vote on their own report") from None
        except (v.DuplicateVote, v.InvalidTransition, v.ConcurrentUpdate) as exc:
            raise DemoError(409, str(exc) or exc.__class__.__name__) from None
        except v.NotPermitted as exc:
            raise DemoError(403, str(exc)) from None
        out["route_request"] = self.client_action(
            row, {"kind": "calculate_routes", "actors": list(users)}).get("bodies_by_actor")
        return out
