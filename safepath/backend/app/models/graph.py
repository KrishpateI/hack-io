"""Routing Graph models (Req 12). `Unknown` is the string "Unknown", never null/false/0."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator

Unknown = Literal["Unknown"]
UNKNOWN = "Unknown"

# Strict so that None / 0 / False / "" can never silently stand in for Unknown (12.3).
StrictNumber = StrictFloat | StrictInt
MaybeFloat = StrictNumber | Unknown
MaybeBool = StrictBool | Unknown
MaybeStr = Annotated[str, Field(min_length=1)] | Unknown

PositiveNumber = Annotated[StrictNumber, Field(gt=0)]

NodeKind = Literal["intersection", "building_entrance", "pedestrian_junction", "indoor"]
TravelMode = Literal["walking", "wheelchair", "driving"]


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    kind: NodeKind
    level: str = "surface"
    elevation_m: MaybeFloat = UNKNOWN
    elevation_conf: Literal["High", "Medium", "Low", "Unknown"] = UNKNOWN
    building_id: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str
    from_node: str
    to_node: str
    geometry: str
    distance_meters: PositiveNumber
    estimated_travel_time_seconds: PositiveNumber
    travel_mode: list[TravelMode]
    source: str
    source_ids: dict[str, str] = {}
    road_type: MaybeStr = UNKNOWN
    elevation_start: MaybeFloat = UNKNOWN
    elevation_end: MaybeFloat = UNKNOWN
    elevation_confidence: Literal["High", "Medium", "Low", "Unknown"] = UNKNOWN
    bridge_flag: MaybeBool = UNKNOWN
    tunnel_flag: MaybeBool = UNKNOWN
    infrastructure_level: Literal[
        "surface", "lower-level", "elevated", "bridge", "tunnel", "Unknown"
    ] = UNKNOWN
    pedestrian_access: MaybeBool = UNKNOWN
    mobility_accessibility: Literal["accessible", "inaccessible", "Unknown"] = UNKNOWN
    current_closure_status: Literal["open", "closed", "Unknown"] = UNKNOWN
    closure_verification: str | None = None
    hazard_flags: list[str] = []
    data_timestamp: datetime
    edge_kind: Literal["street", "indoor", "level_transition"] = "street"
    transition_type: Literal["stairwell", "exit", "platform_stair", "ramp"] | None = None
    classification: Literal["Static", "Simulated"]

    @model_validator(mode="after")
    def _check_transition(self) -> "Edge":
        if self.edge_kind == "level_transition" and self.transition_type is None:
            raise ValueError("level_transition edge requires transition_type")
        if self.edge_kind != "level_transition" and self.transition_type is not None:
            raise ValueError("transition_type only allowed on level_transition edges")
        return self


class GraphIntegrityError(ValueError):
    pass


class Graph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    warnings: list[str] = []

    @classmethod
    def build(cls, nodes: list[Node], edges: list[Edge]) -> "Graph":
        """Build a Graph. Edges with missing endpoints are excluded with a warning naming the
        edge_id (12.8). Level-crossing edges that are not Level_Transitions raise (12.5)."""
        by_id: dict[str, Node] = {}
        for n in nodes:
            if n.node_id in by_id:
                raise GraphIntegrityError(f"duplicate node_id {n.node_id}")
            by_id[n.node_id] = n
        kept: list[Edge] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for e in edges:
            if e.edge_id in seen:
                raise GraphIntegrityError(f"duplicate edge_id {e.edge_id}")
            seen.add(e.edge_id)
            missing = [x for x in (e.from_node, e.to_node) if x not in by_id]
            if missing:
                warnings.append(
                    f"edge {e.edge_id} excluded: endpoint(s) not in graph: {', '.join(missing)}"
                )
                continue
            if (
                by_id[e.from_node].level != by_id[e.to_node].level
                and e.edge_kind != "level_transition"
            ):
                raise GraphIntegrityError(
                    f"edge {e.edge_id} connects different levels without a Level_Transition"
                )
            kept.append(e)
        return cls(nodes=nodes, edges=kept, warnings=warnings)

    @model_validator(mode="after")
    def _validate(self) -> "Graph":
        ids = {n.node_id for n in self.nodes}
        by_id = {n.node_id: n for n in self.nodes}
        for e in self.edges:
            if e.from_node not in ids or e.to_node not in ids:
                raise ValueError(f"edge {e.edge_id} references a missing node")
            if (
                by_id[e.from_node].level != by_id[e.to_node].level
                and e.edge_kind != "level_transition"
            ):
                raise ValueError(
                    f"edge {e.edge_id} connects different levels without a Level_Transition"
                )
        return self
