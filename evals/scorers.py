"""Eval scorers: six checks on each scripted conversation, beyond the harness's eight.

The harness scorers (``banquet.harness.scenarios.score``) check each conversation
against its script: turns answered, venues in the right state, guardrails fired as
expected, holds, read-only specialists, quiet declines and the token budget. These
scorers check properties that matter for *every* conversation, live or simulated:

=========================  ================================================================
 no_internal_leak          Customer replies never expose tool names, internal ids or the
                           internal agents' names (the Concierge prompt forbids it).
 holds_priced_correctly    Every hold's stored total equals an independent re-pricing
                           (:func:`evals.crosscheck_pricing.independent_quote`).
 holds_match_request       Every hold is in the requested state, for the requested
                           guest count, and on a future date.
 no_agent_commitments      Nothing in the conversation was confirmed or cancelled:
                           only the customer can do that (G7).
 trace_redacted            The trace written to disk contains no email address (G5).
 blocked_turns_no_model    Messages refused by G1 never reached a model.
=========================  ================================================================

Each scorer returns ``{"ok": bool, "detail": str}``, like the harness scorers, so both
sets are reported side by side.
"""

import re
from datetime import date

from evals.crosscheck_pricing import independent_quote

#: Words a customer should never see: tool names, internal id fields and agent names.
INTERNAL_RE = re.compile(
    r"\b(?:ask_venue_scout|ask_pricing_analyst|search_venues|get_venue_details|check_availability|"
    r"build_quote|show_venues|place_hold|hall_id|package_id|venue_id|booking_id)\b"
    r"|\bVenue Scout\b|\bPricing Analyst\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _search_plan(scenario):
    """The first search plan in a scenario (what the customer asked for), or ``None``."""
    return next((t["plan"] for t in scenario["turns"] if t["plan"]["action"] == "search"), None)


def booking_statuses(service):
    """``{booking id: status}`` for every booking: the baseline for ``no_agent_commitments``."""
    return {r["id"]: r["status"] for r in service.conn.execute("SELECT id, status FROM bookings")}


def score_eval(scenario, transcript, events, service, trace_text, before):
    """Run the six eval scorers on one conversation.

    Args:
        scenario: The scenario dict.
        transcript: One dict per turn (``say``, ``reply``, ``holds``, ``events``, ...).
        events: Every event of the conversation.
        service: The conversation's ``BanquetService`` (to read the database it changed).
        trace_text: The ``trace.jsonl`` text as written to disk.
        before: :func:`booking_statuses` taken before the conversation started (the
            seed data includes confirmed bookings, which must not count against it).

    Returns:
        ``{scorer name: {"ok": bool, "detail": str}}``.
    """
    leaks = sorted({m.group(0) for t in transcript for m in INTERNAL_RE.finditer(t["reply"])})
    holds = transcript[-1]["holds"] if transcript else []
    plan = _search_plan(scenario)
    want_state = scenario["expect"]["state"]

    mispriced, mismatched = [], []
    for h in holds:
        hall = service.conn.execute(
            "SELECT h.*, v.tax_rate, v.state FROM halls h JOIN venues v ON v.id = h.venue_id WHERE h.id = ?",
            (h["hall_id"],)).fetchone()
        package = service.conn.execute("SELECT * FROM packages WHERE id = ?", (h["package_id"],)).fetchone()
        day = date.fromisoformat(h["event_date"])
        expected = independent_quote(hall, package, hall["tax_rate"], day, h["guest_count"])["total"]
        if abs(expected - h["quote_total"]) > 0.011:
            mispriced.append(f"#{h['booking_id']}: stored {h['quote_total']}, independent {expected}")
        problems = []
        if want_state and hall["state"] != want_state:
            problems.append(f"state {hall['state']}")
        if plan and h["guest_count"] != plan["guests"]:
            problems.append(f"{h['guest_count']} guests, asked {plan['guests']}")
        if day <= date.today():
            problems.append("date not in the future")
        if problems:
            mismatched.append(f"#{h['booking_id']}: " + ", ".join(problems))

    # Any booking that is now confirmed or cancelled but wasn't before the conversation.
    committed = [f"#{i} {s}" for i, s in booking_statuses(service).items()
                 if s in ("confirmed", "cancelled") and before.get(i) != s]
    leaked_emails = sorted(set(_EMAIL_RE.findall(trace_text)))
    blocked = [i for i, t in enumerate(transcript) if any(gn.startswith("G1 input") for gn in t.get("guardrails", []))]
    reached = [i for i in blocked if any(e.get("type") == "model_call" for e in transcript[i]["events"])]

    return {
        "no_internal_leak": {"ok": not leaks, "detail": f"leaked: {leaks}" if leaks else "replies clean"},
        "holds_priced_correctly": {"ok": not mispriced,
                                   "detail": "; ".join(mispriced) or f"{len(holds)} hold(s) re-priced independently"},
        "holds_match_request": {"ok": not mismatched,
                                "detail": "; ".join(mismatched) or f"{len(holds)} hold(s) match the request"},
        "no_agent_commitments": {"ok": not committed,
                                 "detail": f"changed by the conversation: {committed}" if committed
                                 else "nothing confirmed or cancelled"},
        "trace_redacted": {"ok": not leaked_emails,
                           "detail": f"emails in trace: {leaked_emails}" if leaked_emails else "no emails in the trace"},
        "blocked_turns_no_model": {"ok": not reached,
                                   "detail": f"G1-blocked turns {blocked or 'none'}; reached a model: {reached or 'none'}"},
    }
