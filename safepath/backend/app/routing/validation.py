"""Route request validation (Req 13.11, 43.5, 43.6, 43.7).

Pure function: raw payload in, ``RouteRequest`` out, or ``RequestValidationError``
carrying the HTTP status, error-envelope code and every invalid field.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from app.geo import CoordinateStatus, classify_coordinate
from app.models.domain import MobilityProfile, TravelMode
from app.models.routing import LatLon, RouteRequest

SUPPORTED_TRAVEL_MODES = tuple(m.value for m in TravelMode)
SUPPORTED_MOBILITY_PROFILES = tuple(m.value for m in MobilityProfile)
DEFAULT_ALTERNATIVES = 1


class RequestValidationError(Exception):
    """Carries the error envelope pieces: status, code, message, fields."""

    def __init__(self, status: int, code: str, message: str, fields: list[dict[str, str]]):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "fields": self.fields,
                "retry_after_seconds": None,
            }
        }


def _check_enum(raw: Mapping[str, Any], name: str, supported: tuple[str, ...], errors: list) -> str | None:
    value = raw.get(name)
    if not isinstance(value, str) or value not in supported:
        errors.append(
            {
                "field": name,
                "problem": f"unsupported value; supported values: {', '.join(supported)}",
            }
        )
        return None
    return value


def _check_point(raw: Mapping[str, Any], name: str, errors: list) -> tuple[Any, Any] | None:
    point = raw.get(name)
    if not isinstance(point, Mapping):
        errors.append({"field": name, "problem": "required object with latitude and longitude"})
        return None
    ok = True
    for key in ("latitude", "longitude"):
        if key not in point:
            errors.append({"field": f"{name}.{key}", "problem": "required"})
            ok = False
    return (point["latitude"], point["longitude"]) if ok else None


def validate_route_request(
    raw: Any,
    incident_exists: Callable[[str], bool] | None = None,
) -> RouteRequest:
    """Validate a raw route request payload.

    Order: schema (400 validation_error, every bad field listed), then coordinates
    (400 invalid_coordinate / outside_region naming origin, destination or both),
    then incident existence (404 not_found) when ``incident_exists`` is given.
    """
    if not isinstance(raw, Mapping):
        raise RequestValidationError(
            400, "validation_error", "request body must be a JSON object",
            [{"field": "body", "problem": "must be an object"}],
        )

    errors: list[dict[str, str]] = []

    incident_id = raw.get("incident_id")
    if not isinstance(incident_id, str) or not incident_id.strip():
        errors.append({"field": "incident_id", "problem": "required non-empty string"})

    origin = _check_point(raw, "origin", errors)
    destination = _check_point(raw, "destination", errors)
    mode = _check_enum(raw, "travel_mode", SUPPORTED_TRAVEL_MODES, errors)
    profile = _check_enum(raw, "mobility_profile", SUPPORTED_MOBILITY_PROFILES, errors)

    alternatives = raw.get("alternatives", DEFAULT_ALTERNATIVES)
    if isinstance(alternatives, bool) or not isinstance(alternatives, int) or not 1 <= alternatives <= 3:
        errors.append({"field": "alternatives", "problem": "must be an integer from 1 to 3"})

    indoor = raw.get("origin_indoor_node_id")
    if indoor is not None and not isinstance(indoor, str):
        errors.append({"field": "origin_indoor_node_id", "problem": "must be a string or null"})

    # Coordinate type/range/region checks (only for points that are structurally present).
    bad_coords: dict[str, CoordinateStatus] = {}
    for name, pt in (("origin", origin), ("destination", destination)):
        if pt is None:
            continue
        status = classify_coordinate(*pt)
        if status is not CoordinateStatus.INSIDE:
            bad_coords[name] = status

    if errors:
        # Structural problems win; coordinate faults are reported alongside as fields.
        for name, status in bad_coords.items():
            errors.append({"field": name, "problem": _coord_problem(status)})
        raise RequestValidationError(
            400, "validation_error",
            "invalid fields: " + ", ".join(e["field"] for e in errors), errors,
        )

    if bad_coords:
        worst = (
            CoordinateStatus.INVALID
            if CoordinateStatus.INVALID in bad_coords.values()
            else CoordinateStatus.OUTSIDE_REGION
        )
        failed = "both" if len(bad_coords) == 2 else next(iter(bad_coords))
        code = "invalid_coordinate" if worst is CoordinateStatus.INVALID else "outside_region"
        fields = [{"field": n, "problem": _coord_problem(s)} for n, s in bad_coords.items()]
        raise RequestValidationError(
            400, code, f"{failed} {_coord_problem(worst)}", fields
        )

    assert origin and destination and incident_id and mode and profile
    if incident_exists is not None and not incident_exists(incident_id):
        raise RequestValidationError(
            404, "not_found", f"incident '{incident_id}' not found",
            [{"field": "incident_id", "problem": "unknown incident"}],
        )

    return RouteRequest(
        incident_id=incident_id,
        origin=LatLon(latitude=origin[0], longitude=origin[1]),
        destination=LatLon(latitude=destination[0], longitude=destination[1]),
        travel_mode=TravelMode(mode),
        mobility_profile=MobilityProfile(profile),
        alternatives=alternatives,
        origin_indoor_node_id=indoor,
    )


def _coord_problem(status: CoordinateStatus) -> str:
    return (
        "invalid coordinate"
        if status is CoordinateStatus.INVALID
        else "outside the supported Chicago region"
    )
