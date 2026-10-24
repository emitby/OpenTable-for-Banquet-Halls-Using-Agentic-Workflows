"""The eval suite: every layer passes on the real code, and each one fails when it should.

An eval that can't fail proves nothing, so most tests here plant a defect (break a
guardrail, the pricing code or a conversation) and check that the matching red-team
cases, cross-check or scorer catch it.
"""

import json
from datetime import date, timedelta

import pytest

from banquet import guardrails, services
from banquet.agents import team
from banquet.harness.scenarios import SCENARIOS
from banquet.harness.simulated import SimulatedClient
from banquet.paths import CACHE_DIR
from evals import redteam
from evals.crosscheck_pricing import crosscheck, independent_quote
from evals.run_evals import play, run_all
from evals.scorers import booking_statuses, score_eval


def _results(cases):
    """Run some red-team cases and return ``{case id: ok}``."""
    return {c["id"]: c["ok"] for c in redteam.run_redteam(cases)["cases"]}


def test_redteam_all_pass_and_cover_every_guardrail():
    """Every case passes, and each guardrail has both attacks and benign look-alikes (G6/G7/Rules included)."""
    r = redteam.run_redteam()
    assert r["passed"] == r["total"] >= 60, r["failures"]
    for gid in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "Rules"):
        kinds = {c.kind for c in redteam.CASES if c.guardrail == gid}
        assert kinds == {"attack", "benign"}, gid
    assert len({c.id for c in redteam.CASES}) == len(redteam.CASES)


def test_redteam_catches_a_disabled_input_check(monkeypatch):
    """With G1 switched off, every G1 attack fails and every benign case still passes."""
    monkeypatch.setattr(guardrails, "check_customer_message", lambda text: guardrails.InputCheck(True, text))
    res = _results(redteam.G1_CASES)
    assert not any(res[c.id] for c in redteam.G1_CASES if c.kind == "attack")
    assert all(res[c.id] for c in redteam.G1_CASES if c.kind == "benign")


def test_redteam_catches_a_disabled_grounding_check(monkeypatch):
    """With G4 switched off, every G4 attack fails."""
    monkeypatch.setattr(guardrails, "ungrounded_amounts", lambda reply, evidence: [])
    res = _results(redteam.G4_CASES)
    assert not any(res[c.id] for c in redteam.G4_CASES if c.kind == "attack")


def test_redteam_catches_a_disabled_email_check(monkeypatch):
    """With the G3 email rule switched off, invented and partial emails get through and are caught."""
    monkeypatch.setattr(team.g, "email_was_given", lambda email, texts: True)
    res = _results(redteam.G3_CASES)
    assert not res["g3-invented-email"] and not res["g3-partial-email"]


def test_redteam_catches_a_raised_hold_limit(monkeypatch):
    """With the hold limit raised to 10, the fourth-hold and parallel-hold attacks fail."""
    monkeypatch.setattr(team.g, "MAX_ACTIVE_HOLDS", 10)
    res = _results(redteam.G3_CASES)
    assert not res["g3-fourth-hold"] and not res["g3-parallel-holds"]


def test_redteam_reports_a_crashing_case_as_failed():
    """A case that raises is a failure with the error recorded, not a crash of the suite."""
    boom = redteam.Case("boom", "G1", "attack", "raises", lambda: 1 / 0)
    r = redteam.run_redteam([boom])
    assert r["passed"] == 0 and "ZeroDivisionError" in r["failures"][0]["error"]


def test_pricing_crosscheck_agrees_across_every_state():
    """Thousands of quotes in every state and DC match the independent re-pricing to the cent."""
    r = crosscheck(redteam._service())
    assert r["ok"] and r["states"] == 51 and r["halls"] > 300 and r["checked"] > 4000


@pytest.mark.parametrize("constant, value", [("SERVICE_CHARGE", 0.18), ("WEEKEND_PREMIUM", 0.20),
                                             ("DEPOSIT_RATE", 0.30), ("PEAK_MONTHS", {6, 7, 8})])
def test_pricing_crosscheck_catches_a_pricing_bug(monkeypatch, constant, value):
    """Changing any pricing rule in the app (but not in the documented rules) produces mismatches."""
    monkeypatch.setattr(services, constant, value)
    assert crosscheck(redteam._service())["mismatches"]


def test_independent_quote_matches_a_hand_calculation():
    """$2,000 fee on a Saturday in June, 100 guests at $80, 6% tax, worked by hand."""
    q = independent_quote({"rental_fee": 2000, "min_guests": 50}, {"per_guest_price": 80}, 0.06,
                          date(2027, 6, 5), 100)
    # rental 2000 + 500 weekend + 300 peak = 2800; food 8000; service 1600; subtotal 12400; tax 744
    assert q == {"total": 13144.0, "deposit_due": 3286.0, "per_guest": 131.44}


# ------------------------------------------------------------------ scorers on planted defects

@pytest.fixture
def wedding(tmp_path_factory):
    """A real simulated texas-wedding conversation, with its service, transcript and trace."""
    sc = SCENARIOS["texas-wedding"]
    from banquet.agents import ConciergeSession
    from banquet.db import connect, init_db
    conn = connect(":memory:")
    init_db(conn)
    service = services.BanquetService(conn)
    before = booking_statuses(service)
    session = ConciergeSession(SimulatedClient(sc), service)
    transcript = [{"say": t["say"], **session.send(t["say"])} for t in sc["turns"]]
    events = [e for t in transcript for e in t["events"]]
    return {"sc": sc, "service": service, "transcript": transcript, "events": events, "before": before,
            "trace": "\n".join(json.dumps(guardrails.redact(e), default=str) for e in events)}


def _score(w, **overrides):
    """Score the wedding conversation, optionally with a planted defect."""
    args = {"scenario": w["sc"], "transcript": w["transcript"], "events": w["events"],
            "service": w["service"], "trace_text": w["trace"], "before": w["before"], **overrides}
    return score_eval(**args)


def test_eval_scorers_pass_on_a_good_conversation(wedding):
    """All six eval scorers pass on the unmodified conversation (which placed one hold)."""
    scores = _score(wedding)
    assert all(s["ok"] for s in scores.values()), scores
    assert len(wedding["transcript"][-1]["holds"]) == 1


def test_no_internal_leak_catches_ids_and_agent_names(wedding):
    """A reply mentioning a hall_id or the Pricing Analyst fails ``no_internal_leak``."""
    bad = [dict(t) for t in wedding["transcript"]]
    bad[0]["reply"] += "\n(hall_id 12, checked by our Pricing Analyst)"
    assert not _score(wedding, transcript=bad)["no_internal_leak"]["ok"]


def test_holds_priced_correctly_catches_a_wrong_stored_total(wedding):
    """A hold whose stored total is off by $50 fails ``holds_priced_correctly``."""
    bad = [dict(t) for t in wedding["transcript"]]
    bad[-1]["holds"] = [{**h, "quote_total": h["quote_total"] + 50} for h in bad[-1]["holds"]]
    assert not _score(wedding, transcript=bad)["holds_priced_correctly"]["ok"]


def test_holds_match_request_catches_a_wrong_guest_count(wedding):
    """A hold for 150 guests when the customer asked for 180 fails ``holds_match_request``."""
    bad = [dict(t) for t in wedding["transcript"]]
    bad[-1]["holds"] = [{**h, "guest_count": 150} for h in bad[-1]["holds"]]
    assert not _score(wedding, transcript=bad)["holds_match_request"]["ok"]


def test_no_agent_commitments_catches_a_confirmed_hold(wedding):
    """If the hold ends up confirmed during the conversation, ``no_agent_commitments`` fails."""
    hold = wedding["transcript"][-1]["holds"][0]
    wedding["service"].confirm_booking(hold["booking_id"], "maria.lopez@example.com")
    assert not _score(wedding)["no_agent_commitments"]["ok"]


def test_trace_redacted_catches_an_email_on_disk(wedding):
    """A trace containing a raw email address fails ``trace_redacted``."""
    assert not _score(wedding, trace_text=wedding["trace"] + '\n{"x": "maria.lopez@example.com"}')["trace_redacted"]["ok"]


def test_blocked_turns_no_model_catches_a_leaky_block(wedding):
    """A G1-blocked turn that still has a model call fails ``blocked_turns_no_model``."""
    bad = [dict(t) for t in wedding["transcript"]]
    bad[0]["guardrails"] = ["G1 input: prompt_injection"]
    assert not _score(wedding, transcript=bad)["blocked_turns_no_model"]["ok"]


# ------------------------------------------------------------------ the runner

def test_play_scores_with_all_fourteen_scorers():
    """``play`` runs a conversation through both scorer sets and writes a redacted trace."""
    run_dir = CACHE_DIR / "test-output" / "eval-play"
    result = play(SCENARIOS["hold-without-email"], SimulatedClient(SCENARIOS["hold-without-email"]), run_dir)
    assert len(result["scores"]) == 14 and result["passed"]
    assert "@" not in (run_dir / "hold-without-email" / "trace.jsonl").read_text(encoding="utf-8").replace("<email>", "")


def test_offline_suite_passes_end_to_end():
    """The whole offline suite (red-team, cross-check, simulated scenarios) passes."""
    r = run_all(write=False)
    assert r["ok"], r["scenarios"]["scorer_pass_rates"]
    sc = r["scenarios"]
    assert sc["mode"] == "simulated" and len(sc["conversations"]) == len(SCENARIOS)
    assert all(p == t for p, t in sc["scorer_pass_rates"].values()) and len(sc["scorer_pass_rates"]) == 14


class _NoCreditClient:
    """A client whose every call fails the way the API does when the account has no credit."""

    def __init__(self):
        """Expose ``beta.messages.create`` like the real client."""
        from types import SimpleNamespace
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._fail))

    def _fail(self, **kwargs):
        """Raise the API's out-of-credit error."""
        raise RuntimeError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")


def test_live_run_stops_cleanly_on_an_account_problem(monkeypatch):
    """An out-of-credit API is recorded as an error and stops the run, instead of crashing it."""
    import anthropic
    from evals import run_evals
    monkeypatch.setattr(anthropic, "Anthropic", _NoCreditClient)
    sc = run_evals.run_scenarios("live", trials=2)
    assert sc["stopped_early"] and "credit balance" in sc["stop_reason"]
    assert len(sc["conversations"]) == 1 and not sc["conversations"][0]["passed"]
    assert "error" in sc["conversations"][0]


def test_dates_in_scenarios_are_in_the_future():
    """Every date a scenario asks for is in the future, so live trials never request past dates."""
    plans = [t["plan"] for sc in SCENARIOS.values() for t in sc["turns"] if t["plan"].get("date")]
    assert plans and all(date.fromisoformat(p["date"]) > date.today() + timedelta(days=7) for p in plans)
