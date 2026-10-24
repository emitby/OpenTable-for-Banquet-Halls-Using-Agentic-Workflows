"""Run the eval suite and write a results file.

    .\\run.ps1 eval                                         # offline: free, deterministic
    .\\run.ps1 eval --live --trials 3 --max-cost 8          # real agents, 3 trials

Offline (the default):

1. **Red-team:** every case in :mod:`evals.redteam`.
2. **Pricing cross-check:** :mod:`evals.crosscheck_pricing` on the seed database.
3. **Scenarios, simulated:** every scripted conversation through the real tools,
   guardrails and booking service with the scripted stand-in for Claude, scored by
   the 8 harness scorers and the 6 eval scorers (14 in all).

``--live`` replaces step 3 with the real Claude agents, ``--trials`` times, and reports
the pass rate of every scorer across trials. Agents aren't deterministic, so one passing
trial is an anecdote and several are evidence. The cost cap is checked between
conversations; the run stops early, and says so, once it's passed.

Writes ``evals/results/<timestamp>-<mode>.json`` (kept in git, so results can be
compared over time) and each conversation's redacted trace under ``runs/``.
Exit status 0 means everything passed.
"""

import argparse
import sys
from datetime import datetime

from banquet.agents import ConciergeSession
from banquet.db import connect, init_db
from banquet.harness.scenarios import SCENARIOS, score
from banquet.harness.tokens import ledger
from banquet.harness.trace import new_run_dir, write_json, write_trace
from banquet.paths import PROJECT_DIR, confine
from banquet.services import BanquetService
from evals.crosscheck_pricing import crosscheck
from evals.redteam import run_redteam
from evals.scorers import booking_statuses, score_eval

RESULTS_DIR = PROJECT_DIR / "evals" / "results"


def play(scenario, client, run_dir):
    """Play one conversation and score it with all 14 scorers.

    Args:
        scenario: A scenario dict.
        client: A ``SimulatedClient`` or ``anthropic.Anthropic`` client.
        run_dir: Folder for this conversation's ``trace.jsonl``.

    Returns:
        ``{"scores": {...}, "passed": bool, "cost": float, "tokens": int}``.
    """
    conn = connect(":memory:")
    init_db(conn)
    service = BanquetService(conn)
    before = booking_statuses(service)
    session = ConciergeSession(client, service)
    transcript, events = [], []
    for i, turn in enumerate(scenario["turns"]):
        result = session.send(turn["say"])
        transcript.append({"say": turn["say"], **result})
        events += [{**e, "scenario": scenario["id"], "turn": i} for e in result["events"]]
    trace_path = write_trace(run_dir / scenario["id"] / "trace.jsonl", events)
    live = not hasattr(client, "scenario")
    scores = {**score(scenario, transcript, live=live),
              **score_eval(scenario, transcript, events, service,
                           confine(trace_path).read_text(encoding="utf-8"), before)}
    totals = ledger(events)["totals"]
    return {"scores": scores, "passed": all(s["ok"] for s in scores.values()),
            "cost": totals["cost"], "tokens": totals["tokens"]}


def run_scenarios(mode, trials=1, max_cost=8.0, only=None):
    """Play every scenario ``trials`` times and aggregate the scores.

    Args:
        mode: ``"simulated"`` or ``"live"``.
        trials: How many times to play each scenario (live mode).
        max_cost: Live mode: stop starting new conversations once the spend passes this.
        only: Optional scenario id.

    Returns:
        ``{"mode", "trials", "conversations": [...], "scorer_pass_rates": {name: [passed, total]},
        "scenario_pass_rates": {id: [passed, total]}, "cost", "stopped_early", "run_dir"}``.
    """
    if mode == "live":
        import anthropic
        live_client = anthropic.Anthropic()
    else:
        from banquet.harness.simulated import SimulatedClient
    run_dir = new_run_dir(f"eval-{mode}")
    scenarios = [SCENARIOS[only]] if only else list(SCENARIOS.values())
    conversations, spent, stopped, stop_reason = [], 0.0, False, None
    for trial in range(1, trials + 1):
        for sc in scenarios:
            if mode == "live" and spent > max_cost:
                stopped, stop_reason = True, f"cost cap ${max_cost:.2f} reached"
                break
            client = live_client if mode == "live" else SimulatedClient(sc)
            try:
                result = play(sc, client, run_dir / f"trial{trial}")
            except Exception as exc:
                # A crashed conversation is a failed one, recorded with its cause, so the
                # conversations already scored are never lost.
                error = f"{type(exc).__name__}: {exc}"
                result = {"scores": {}, "passed": False, "cost": 0.0, "tokens": 0, "error": error}
                if _account_problem(exc):
                    conversations.append({"trial": trial, "scenario": sc["id"], **result})
                    print(f"  trial {trial} ERROR {sc['id']}: {error}", flush=True)
                    stopped, stop_reason = True, f"API account problem: {error}"
                    break
            spent += result["cost"]
            conversations.append({"trial": trial, "scenario": sc["id"], **result})
            if "error" in result:
                print(f"  trial {trial} ERROR {sc['id']}: {result['error']}", flush=True)
                continue
            print(f"  trial {trial} {'PASS' if result['passed'] else 'FAIL'} {sc['id']}"
                  + "".join(f"\n      {k}: {v['detail']}" for k, v in result["scores"].items() if not v["ok"]),
                  flush=True)
        if stopped:
            break
    scorer_rates, scenario_rates = {}, {}
    for c in conversations:
        for name, s in c["scores"].items():
            tally = scorer_rates.setdefault(name, [0, 0])
            tally[0] += s["ok"]
            tally[1] += 1
        tally = scenario_rates.setdefault(c["scenario"], [0, 0])
        tally[0] += c["passed"]
        tally[1] += 1
    return {"mode": mode, "trials": trials, "conversations": conversations,
            "scorer_pass_rates": scorer_rates, "scenario_pass_rates": scenario_rates,
            "cost": round(spent, 4), "stopped_early": stopped, "stop_reason": stop_reason,
            "run_dir": str(run_dir)}


def _account_problem(exc):
    """True for errors no retry can fix: no credit, a bad key or no permission.

    Every remaining conversation would fail the same way, so the run stops and says why.
    """
    text = str(exc).lower()
    return (type(exc).__name__ in ("AuthenticationError", "PermissionDeniedError")
            or "credit balance" in text or "billing" in text)


def run_all(live=False, trials=1, max_cost=8.0, only=None, write=True):
    """Run the red-team, the pricing cross-check and the scenarios, then write the results.

    Args:
        live: Use the real agents for the scenarios.
        trials: Trials per scenario (live mode).
        max_cost: Live spending cap in USD.
        only: Optional single scenario id.
        write: Whether to write ``evals/results/<timestamp>-<mode>.json``.

    Returns:
        The results dict, with ``"ok"`` true when every layer passed.
    """
    redteam = run_redteam()
    conn = connect(":memory:")
    init_db(conn)
    pricing = crosscheck(BanquetService(conn))
    scenarios = run_scenarios("live" if live else "simulated", trials if live else 1, max_cost, only)
    ok = (redteam["passed"] == redteam["total"] and pricing["ok"]
          and all(p == t for p, t in scenarios["scenario_pass_rates"].values()) and not scenarios["stopped_early"])
    results = {"ran_at": datetime.now().isoformat(timespec="seconds"), "ok": ok,
               "redteam": {k: v for k, v in redteam.items() if k != "cases"}, "redteam_cases": redteam["cases"],
               "pricing_crosscheck": pricing, "scenarios": scenarios}
    if write:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = confine(RESULTS_DIR / f"{stamp}-{scenarios['mode']}.json")
        results["results_file"] = path.relative_to(PROJECT_DIR).as_posix()
        write_json(path, results)
    return results


def main(argv=None):
    """Command-line entry point. Returns 0 if everything passed, else 1."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="real Claude agents (costs money)")
    parser.add_argument("--trials", type=int, default=3, help="live trials per scenario (default 3)")
    parser.add_argument("--max-cost", type=float, default=8.0, help="live spending cap in USD (default 8)")
    parser.add_argument("--only", choices=sorted(SCENARIOS), help="run one scenario")
    args = parser.parse_args(argv)
    print("Scenarios (" + (f"LIVE, {args.trials} trial(s)" if args.live else "simulated") + "):")
    r = run_all(args.live, args.trials, args.max_cost, args.only)
    rt, pc, sc = r["redteam"], r["pricing_crosscheck"], r["scenarios"]
    print(f"\nRed-team: {rt['passed']}/{rt['total']}  "
          + "  ".join(f"{g} {p}/{t}" for g, (p, t) in rt["by_guardrail"].items()))
    for f in rt["failures"]:
        print(f"  FAIL {f['id']}: {f['description']} {f.get('error', '')}")
    print(f"Pricing cross-check: {pc['checked']:,} quotes, {len(pc['mismatches'])} mismatches "
          f"({pc['halls']} halls in {pc['states'] - 1} states + DC)")
    print("Scorer pass rates:")
    for name, (p, t) in sc["scorer_pass_rates"].items():
        print(f"  {name:<26} {p}/{t}")
    passed = sum(p for p, _ in sc["scenario_pass_rates"].values())
    total = sum(t for _, t in sc["scenario_pass_rates"].values())
    cost_label = "cost" if sc["mode"] == "live" else "estimated cost (simulated)"
    print(f"Conversations passed: {passed}/{total}   {cost_label} ${sc['cost']:.2f}"
          + (f"   STOPPED EARLY: {sc['stop_reason']}" if sc["stopped_early"] else ""))
    print(f"\n{'ALL PASSED' if r['ok'] else 'FAILURES ABOVE'}  ->  {r.get('results_file')}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
