"""Property 41: AI output is accepted only if grounded.

**Validates: Requirements 33.2, 33.3, 33.6, 33.7**

A stubbed model client stands in for Amazon Bedrock (no network). For any context of verified
official notices and any model output, the output is accepted only if every shelter / closure /
order / route / hazard / named entity it references exists in the context and it cites at least
one supplied Source_Record; otherwise the unsummarized source data is returned. Accepting or
rejecting never mutates the supplied notices, restrictions, route or verification statuses.
"""
# Feature: safepath-evacuation-navigation, Property 41: AI output is accepted only if grounded
from __future__ import annotations

import copy
import re

from hypothesis import given, strategies as st

from app.services.ai import AIAssistant, notice_facts

# Name pools are disjoint and none is a case-insensitive substring of another, of the orgs, of
# the fixed template words, or of the numeric source ids, so "absent" really means absent.
PRESENT_NAMES = ("Wacker", "Michigan", "Wabash", "Clark", "Dearborn", "Monroe", "Madison", "Randolph")
ABSENT_NAMES = ("Halsted", "Ashland", "Pulaski", "Cicero", "Kedzie")
ORGS = ("CDOT", "OEMC", "NWS")

# Entity kind -> (notice sentence template, sentence asserting that kind with no named entity).
KINDS: dict[str, tuple[str, str]] = {
    "closure": ("{n} Street is closed.", "There is also a closed lane nearby."),
    "shelter": ("{n} shelter is open.", "There is also a shelter nearby."),
    "order": ("An evacuation order covers {n}.", "There is also an evacuation order nearby."),
    "route": ("Use the detour along {n} Avenue.", "There is also a detour nearby."),
    "hazard": ("Flooding reported on {n} Drive.", "There is also flooding nearby."),
}
MUTATIONS = ("none", "absent_kind", "absent_entity", "no_citation", "foreign_citation")


class StubBedrock:
    """Stubbed Bedrock client: returns a canned reply, records calls, never touches the network."""

    model_id = "stub-bedrock"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.reply


@st.composite
def contexts(draw: st.DrawFn) -> list[dict]:
    """1-4 verified official notices, each naming one entity of one kind, with unique sources."""
    n = draw(st.integers(1, 4))
    kinds = draw(st.lists(st.sampled_from(list(KINDS)), min_size=n, max_size=n))
    names = draw(st.lists(st.sampled_from(PRESENT_NAMES), min_size=n, max_size=n, unique=True))
    sids = draw(st.lists(st.integers(0, 999), min_size=n, max_size=n, unique=True))
    return [{
        "notice_id": f"n{i}",
        "text": KINDS[k][0].format(n=name),
        "source_record_id": f"src_{sid:03d}",
        "source_organization": draw(st.sampled_from(ORGS)),
        "verification_status": draw(st.sampled_from(["verified", "Officially_Verified"])),
        "simulation_flag": draw(st.booleans()),
    } for i, (k, name, sid) in enumerate(zip(kinds, names, sids))]


def _cited(text: str, sid: str) -> str:
    return f"{text[:-1]} [{sid}]."


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[list[dict], str, bool]:
    """(notices, model output, expected_grounded)."""
    notices = draw(contexts())
    picked = draw(st.lists(st.sampled_from(notices), min_size=1, max_size=len(notices),
                           unique_by=lambda x: x["notice_id"]))
    grounded = " ".join(_cited(x["text"], x["source_record_id"]) for x in picked)
    present = {k for x in notices for k in KINDS if x["text"] in {KINDS[k][0].format(n=nm) for nm in PRESENT_NAMES}}
    absent_kinds = [k for k in KINDS if k not in present]
    options = [m for m in MUTATIONS if m != "absent_kind" or absent_kinds]
    mutation = draw(st.sampled_from(options))
    if mutation == "none":
        return notices, grounded, True
    if mutation == "absent_kind":
        return notices, f"{grounded} {KINDS[draw(st.sampled_from(absent_kinds))][1]}", False
    if mutation == "absent_entity":
        name = draw(st.sampled_from(ABSENT_NAMES))
        kind = draw(st.sampled_from(list(KINDS)))
        extra = f"Also, {KINDS[kind][0].format(n=name)}"
        return notices, f"{grounded} {extra}", False
    if mutation == "no_citation":
        return notices, re.sub(r" \[[^\]]+\]", "", grounded), False
    foreign = f"ext_{draw(st.integers(0, 999)):03d}"
    replace_all = draw(st.booleans())
    out = (re.sub(r"\[[^\]]+\]", f"[{foreign}]", grounded) if replace_all
           else f"{grounded[:-1]} [{foreign}].")
    return notices, out, False


@given(scenarios(), st.lists(st.fixed_dictionaries({
    "kind": st.sampled_from(["closure", "hazard_zone", "evacuation_order"]),
    "id": st.text("abc123", min_size=1, max_size=6),
    "source_record_id": st.text("xyz789", min_size=1, max_size=6),
    "verification_status": st.sampled_from(["Officially_Verified", "Unverified", "Corroborated"]),
    "policy_outcome": st.sampled_from(["Blocked", "Allowed", "Penalized"]),
}), max_size=3))
def test_ai_output_accepted_only_if_grounded(scenario, restrictions):
    notices, reply, expected_grounded = scenario
    route = {"edge_ids": ["e1", "e2"], "cost": 12.5, "hazard_flags": ["report_flooding"]}
    snapshot = copy.deepcopy((notices, restrictions, route))
    facts = notice_facts(notices)
    unsummarized = AIAssistant().summarize_notices(notices)  # AI off: source data only
    client = StubBedrock(reply)

    result = AIAssistant(client).summarize_notices(notices)
    route_result = AIAssistant(StubBedrock(reply)).explain_route_flags(
        route["hazard_flags"], restrictions, {"hazard": 4.0})

    assert len(client.calls) == 1
    # Req 33.2: the model only sees the supplied context.
    assert all(f"[{x['source_record_id']}]" in client.calls[0][1] for x in notices)
    if expected_grounded:
        assert result.ai_used and not result.guardrail_applied, result.reason
        assert result.body == reply
        assert result.sources == facts.sources
    else:
        # Req 33.6 / 33.7: discarded and replaced by the unsummarized source data.
        assert not result.ai_used and result.guardrail_applied and result.reason
        assert result.body == facts.template == unsummarized.body
        assert result.sources == facts.sources
        for x in notices:
            assert x["text"] in result.body and f"[{x['source_record_id']}]" in result.body
    # Req 33.3: accepting or rejecting never changes outcomes, restrictions, routes or statuses.
    assert (notices, restrictions, route) == snapshot
    assert route_result.body  # the route path also returns text without touching its inputs
