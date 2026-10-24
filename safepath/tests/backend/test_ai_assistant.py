"""Optional AI_Assistant tests (Req 33, 13.9). A fake client stands in for Bedrock; no AWS calls."""
from __future__ import annotations

import copy
import time

import pytest
from fastapi.testclient import TestClient

from app.adapters import MemoryRepository
from app.auth.deps import install_auth
from app.auth.provider import DemoTokenAuthProvider
from app.config import Settings
from app.main import create_app
from app.services.ai import (
    AI_LABEL, ALLOWED_CATEGORIES, REMINDER, AIAssistant, AIUnavailable, NullClient, build_assistant,
    filter_categories, keyword_categories,
)

NOTICES = [
    {"notice_id": "n1", "text": "Lower Wacker Drive is closed between Michigan Avenue and State Street.",
     "source_record_id": "src_cdot_1", "source_organization": "CDOT", "verification_status": "verified",
     "simulation_flag": False},
]


class Fake:
    model_id = "fake-model"

    def __init__(self, reply="", exc=None, delay=0.0):
        self.reply, self.exc, self.delay, self.calls = reply, exc, delay, []

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        if self.delay:
            time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.reply


def ai(reply, **kw):
    return AIAssistant(Fake(reply, **kw))


def test_faithful_output_passes():
    a = ai("CDOT reports Lower Wacker Drive is closed between Michigan Avenue and State Street [src_cdot_1].")
    r = a.summarize_notices(NOTICES)
    assert r.ai_used and not r.guardrail_applied and r.model == "fake-model"
    assert r.text.startswith(AI_LABEL) and r.text.endswith(REMINDER)
    assert r.sources == ("src_cdot_1",)


@pytest.mark.parametrize("reply", [
    "A shelter at Navy Pier is open [src_cdot_1].",                       # hallucinated shelter
    "Lower Wacker Drive and Wells Street are closed [src_cdot_1].",       # hallucinated entity
    "An evacuation order is in effect for the Loop [src_cdot_1].",         # hallucinated order
    "The stairwell on the east side is open for exit [src_cdot_1].",       # stairwell claim
    "Lower Wacker Drive is closed. This is the safe option [src_cdot_1].",  # safe label
    "Lower Wacker Drive is closed and certified by CDOT [src_cdot_1].",
    "Lower Wacker Drive is closed.",                                       # no citation
    "Lower Wacker Drive is closed [src_other].",                           # unsupplied source
    "Lower Wacker Drive is closed for 45 days [src_cdot_1].",              # unsupplied number
    "",
])
def test_hallucinations_blocked_and_replaced(reply):
    r = ai(reply).summarize_notices(NOTICES)
    assert not r.ai_used and r.guardrail_applied
    assert "Lower Wacker Drive is closed between Michigan Avenue and State Street." in r.body
    assert "[src_cdot_1]" in r.body
    assert r.text.endswith(REMINDER)


def test_disabled_returns_template_without_calling_model():
    r = AIAssistant().summarize_notices(NOTICES)
    assert not r.ai_used and not r.guardrail_applied and r.model is None
    assert "Lower Wacker" in r.body and REMINDER in r.text


def test_error_and_timeout_fall_back():
    for a in (ai("x", exc=AIUnavailable("down")), ai("x", exc=RuntimeError("boom"))):
        r = a.summarize_notices(NOTICES)
        assert not r.ai_used and "Lower Wacker" in r.body
    slow = AIAssistant(Fake("fine [src_cdot_1]", delay=0.5), timeout=0.05)
    t = time.time()
    r = slow.summarize_notices(NOTICES)
    assert not r.ai_used and time.time() - t < 0.4


def test_inputs_not_mutated():
    notices = copy.deepcopy(NOTICES)
    flags = ["report_flooding"]
    restr = [{"kind": "closure", "id": "c1", "source_record_id": "s1", "simulated": False}]
    pen = {"hazard": 4.0}
    snap = copy.deepcopy((notices, flags, restr, pen))
    a = ai("Lower Wacker Drive is closed [src_cdot_1].")
    a.summarize_notices(notices)
    a.explain_route_flags(flags, restr, pen)
    assert (notices, flags, restr, pen) == snap


def test_notice_requires_source_and_verified():
    a = AIAssistant()
    with pytest.raises(ValueError):
        a.summarize_notices([{"text": "x", "source_record_id": ""}])
    with pytest.raises(ValueError):
        a.summarize_notices([{"text": "x", "source_record_id": "s", "verification_status": "Unverified"}])


def test_route_flags_template_and_guard():
    restr = [{"kind": "closure", "id": "c1", "source_record_id": "s1", "simulated": False}]
    ok = ai("The route has a closure c1 and a hazard penalty of 4.0 [s1].")
    r = ok.explain_route_flags(["report_flooding"], restr, {"hazard": 4.0})
    assert r.ai_used
    bad = ai("The route has a closure c1 near a shelter [s1].")
    r = bad.explain_route_flags(["report_flooding"], restr, {"hazard": 4.0})
    assert r.guardrail_applied and not r.ai_used and "closure c1" in r.body


def test_categories_restricted_to_allowed_set():
    r = ai('{"categories": ["flooding", "made up", "other", "FLOODING"]}').categorize_report("water on road")
    assert r.ai_used and set(r.categories) <= set(ALLOWED_CATEGORIES)
    assert list(r.categories) == ["flooding", "other"]
    bad = ai('{"categories": ["invented"]}').categorize_report("a tree fell on the road")
    assert not bad.ai_used and bad.guardrail_applied and set(bad.categories) <= set(ALLOWED_CATEGORIES)
    assert filter_categories("x") == [] and keyword_categories("zzz") == ["other"]
    assert len(ALLOWED_CATEGORIES) == 9


def test_verification_explainer():
    off = AIAssistant().explain_verification()
    assert not off.ai_used and "Unverified" in off.body and "Simulated" in off.body
    r = ai("Confirmed information is Officially_Verified and recorded with a Source_Record. "
           "Corroborated means several community members agree. Unverified reports are unchecked. "
           "Simulated data is not a real event.").explain_verification()
    assert r.ai_used
    r = ai("Confirmed information is a guaranteed closure.").explain_verification()
    assert r.guardrail_applied


def test_build_assistant_off_by_default():
    assert isinstance(build_assistant({}).client, NullClient)
    assert isinstance(build_assistant({"SAFEPATH_AI_ENABLED": "true"}).client, NullClient)  # no model id
    assert isinstance(build_assistant({"SAFEPATH_AI_ENABLED": "false",
                                       "SAFEPATH_BEDROCK_MODEL_ID": "m"}).client, NullClient)


# -------------------------------------------------------------------------- endpoint
@pytest.fixture()
def api():
    app = create_app(Settings())
    provider = DemoTokenAuthProvider("test-secret")
    install_auth(app, auth=provider, repository=MemoryRepository())
    client = TestClient(app, raise_server_exceptions=False)
    g = provider.issue_guest()
    return app, client, {"Authorization": f"Bearer {g.token}"}


def test_endpoint_requires_auth(api):
    _, c, _ = api
    assert c.post("/api/ai/summarize", json={"kind": "verification_explainer"}).status_code == 401


def test_endpoint_disabled_returns_templates(api):
    _, c, h = api
    r = c.post("/api/ai/summarize", json={"kind": "notice_summary", "notices": NOTICES}, headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["ai_used"] is False and j["sources"] == ["src_cdot_1"] and REMINDER in j["text"]
    for body in ({"kind": "verification_explainer"},
                 {"kind": "route_flags", "hazard_flags": ["report_flooding"]},
                 {"kind": "report_categorize", "text": "smoke from the building"}):
        j = c.post("/api/ai/summarize", json=body, headers=h).json()
        assert j["ai_used"] is False and j["label"] and j["guardrail_applied"] is False
    assert j["categories"] == ["fire or smoke"]


def test_endpoint_validation_and_cap(api):
    _, c, h = api
    assert c.post("/api/ai/summarize", json={"kind": "bogus"}, headers=h).status_code == 400
    assert c.post("/api/ai/summarize", json={"kind": "notice_summary"}, headers=h).status_code == 400
    assert c.post("/api/ai/summarize", json={"kind": "report_categorize", "text": "x" * 1001},
                  headers=h).status_code == 400
    unsourced = [{"text": "x", "source_record_id": ""}]
    assert c.post("/api/ai/summarize", json={"kind": "notice_summary", "notices": unsourced},
                  headers=h).status_code == 400


def test_endpoint_uses_guard_with_fake_client(api):
    app, c, h = api
    app.state.ai_assistant = ai("A shelter at Navy Pier is open [src_cdot_1].")
    j = c.post("/api/ai/summarize", json={"kind": "notice_summary", "notices": NOTICES}, headers=h).json()
    assert j["ai_used"] is False and j["guardrail_applied"] is True and "shelter" not in j["text"].lower()


def test_endpoint_rate_limited(api):
    _, c, h = api
    codes = [c.post("/api/ai/summarize", json={"kind": "verification_explainer"}, headers=h).status_code
             for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
