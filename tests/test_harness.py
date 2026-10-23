"""Harness tests: token attribution math, the simulated scenario suite, and trace writing."""

import json
import secrets
from datetime import date

import pytest

from banquet.harness.scenarios import build_scenarios, score
from banquet.harness.run_scenarios import run_scenario, run_suite
from banquet.harness.simulated import SimulatedClient, estimate_tokens
from banquet.harness.tokens import PRICES, cost_of, ledger
from banquet.harness.trace import write_json, write_trace
from banquet.paths import CACHE_DIR, PathError, confine


def model_call(run, agent, usage, tool_uses=(), parent=None):
    """A model_call event."""
    return {"type": "model_call", "run": run, "agent": agent, "parent": parent,
            "usage": dict(zip(("input", "output", "cache_write", "cache_read"), usage)),
            "tool_uses": [{"id": i, "name": n, "input_chars": c} for i, n, c in tool_uses]}


def tool_result(id_, chars, ok=True):
    """A tool_result event."""
    return {"type": "tool_result", "id": id_, "ok": ok, "result_chars": chars}


def test_ledger_attributes_decision_result_and_specialist_tokens():
    """Decision, result and specialist tokens are attributed exactly as documented in tokens.py."""
    events = [
        # Concierge call 1 asks for two tools; 300 output tokens split 1:2 by argument size.
        model_call(1, "Concierge", (1000, 300, 0, 0), [("a", "ask_venue_scout", 100), ("b", "show_venues", 200)]),
        # The Scout's run (delegated from tool call "a") uses 500 + 50 tokens.
        model_call(2, "Venue Scout", (500, 50, 0, 0), parent="a"),
        tool_result("a", 600), tool_result("b", 200, ok=False),
        # Concierge call 2: prompt grew from 1000 to 2100; minus 300 output = 800 result tokens, split 3:1.
        model_call(1, "Concierge", (100, 40, 0, 2000)),
    ]
    led = ledger(events)
    scout, show = led["tools"]["ask_venue_scout"], led["tools"]["show_venues"]
    assert (scout["decision"], show["decision"]) == (100, 200)
    assert (scout["result"], show["result"]) == (600, 200)
    assert scout["specialist"] == 550 and show["specialist"] == 0
    assert show["errors"] == 1
    assert led["agents"]["Concierge"]["total"] == 1000 + 300 + 100 + 40 + 2000
    assert led["totals"]["tokens"] == 3440 + 550
    assert led["agents"]["Venue Scout"]["cost"] == pytest.approx(cost_of({"input": 500, "output": 50}), abs=1e-4)


def test_ledger_keeps_scenarios_apart_when_ids_collide():
    """Regression: two scenarios reusing the same run and tool ids must not mix (found in the PDF review).

    Scenario A delegates through tool call "x"; scenario B uses the same id "x" for a data
    tool. The specialist's tokens must stay with A's delegation tool.
    """
    a = [{**e, "scenario": "A"} for e in [
        model_call(1, "Concierge", (100, 10, 0, 0), [("x", "ask_venue_scout", 10)]),
        model_call(2, "Venue Scout", (300, 30, 0, 0), parent="x"),
        tool_result("x", 50),
        model_call(1, "Concierge", (200, 5, 0, 0)),
    ]]
    b = [{**e, "scenario": "B"} for e in [
        model_call(1, "Pricing Analyst", (100, 10, 0, 0), [("x", "build_quote", 10)]),
        tool_result("x", 50),
        model_call(1, "Pricing Analyst", (150, 5, 0, 0)),
    ]]
    tools = ledger(a + b)["tools"]
    assert tools["ask_venue_scout"]["specialist"] == 330
    assert tools["build_quote"]["specialist"] == 0 and tools["build_quote"]["calls"] == 1


def test_cost_uses_configured_prices():
    """Cost is tokens x price per million for each token type."""
    assert cost_of({"input": 1_000_000}) == PRICES["input"]
    assert cost_of({"output": 500_000, "cache_read": 1_000_000}) == PRICES["output"] / 2 + PRICES["cache_read"]


def test_estimate_tokens_is_chars_over_four():
    """The simulated estimate is JSON characters / 4, rounded up."""
    assert estimate_tokens("x" * 10) == 3      # '"xxxxxxxxxx"' is 12 characters


SCENARIOS = build_scenarios(date(2026, 10, 22))


@pytest.mark.parametrize("sid", sorted(SCENARIOS))
def test_every_simulated_scenario_passes_every_scorer(sid):
    """Each scripted scenario passes all eight scorers through the real runtime and guardrails."""
    sc = SCENARIOS[sid]
    transcript, _ = run_scenario(sc, SimulatedClient(sc))
    failed = {k: v["detail"] for k, v in score(sc, transcript).items() if not v["ok"]}
    assert not failed


def test_scorers_fail_on_a_broken_transcript():
    """The scorers catch a wrong-state card, a fallback note, a specialist write and an unexpected guardrail."""
    sc = SCENARIOS["texas-wedding"]
    bad_turn = {"reply": "x", "venues": [{"name": "Elsewhere", "state": "OH"}], "holds": [],
                "guardrails": ["G4 grounding: fallback note for $1"],
                "events": [{"type": "guardrail", "reason": "fallback note"},
                           {"type": "tool_call", "agent": "Venue Scout", "tool": "place_hold"}]}
    scores = score(sc, [bad_turn, bad_turn])
    for name in ("venues_in_state", "prices_grounded", "expected_guardrails", "holds", "read_only_specialists"):
        assert not scores[name]["ok"], name


def test_live_mode_allows_but_does_not_require_misbehavior_guardrails():
    """Regression (independent review): live agents that never invent a price still pass; G1 stays required."""
    sc = SCENARIOS["denver-price-guess"]
    clean = [{"reply": "Here are your quotes.", "venues": [], "holds": [], "guardrails": [], "events": []}]
    assert not score(sc, clean)["expected_guardrails"]["ok"]
    assert score(sc, clean, live=True)["expected_guardrails"]["ok"]
    injection = SCENARIOS["off-topic-injection"]
    no_g1 = [{"reply": "x", "venues": [], "holds": [], "guardrails": [], "events": []}] * 2
    assert not score(injection, no_g1, live=True)["expected_guardrails"]["ok"]


def test_suite_summary_has_tokens_per_tool_and_agent():
    """The suite reports every tool and agent, and labels the mode."""
    summary = run_suite("simulated", write=False)
    assert summary["mode"] == "simulated" and summary["passed"] == summary["total"] == len(SCENARIOS)
    assert {"ask_venue_scout", "ask_pricing_analyst", "search_venues", "check_availability", "build_quote",
            "show_venues", "place_hold"} <= set(summary["ledger"]["tools"])
    assert set(summary["ledger"]["agents"]) == {"Concierge", "Venue Scout", "Pricing Analyst"}
    assert all(t["total"] > 0 for t in summary["ledger"]["tools"].values())


def test_trace_files_are_redacted_and_confined():
    """Traces and reports never contain emails, and can't be written outside the project."""
    out = CACHE_DIR / "test-output" / secrets.token_hex(6)
    write_trace(out / "trace.jsonl", [{"input": {"customer_email": "ada@example.com"}}])
    write_json(out / "report.json", {"say": "I'm Ada, ada@example.com"})
    assert "ada@example.com" not in (out / "trace.jsonl").read_text(encoding="utf-8")
    assert json.loads((out / "report.json").read_text(encoding="utf-8")) == {"say": "I'm Ada, <email>"}
    with pytest.raises(PathError):
        confine(confine(".").parent / "outside.txt")
