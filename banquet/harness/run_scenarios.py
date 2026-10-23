"""Run the scripted scenarios through the real agent team and write a run report.

    .\\run.ps1 scenarios                       # simulated (no API key, no cost)
    .\\run.ps1 scenarios --only texas-wedding
    .\\run.ps1 scenarios --live --max-cost 5   # real Claude agents, stops past $5

Each scenario gets a fresh in-memory database, so runs never touch ``data/`` and are
repeatable. Output folder ``runs/<timestamp>-<mode>/``:

* ``<scenario>/trace.jsonl``: every event (emails redacted)
* ``<scenario>/report.json``: transcript, scores and token ledger for that scenario
* ``summary.json``: all scores, the suite's token ledger per agent and per tool, and
  the mode (``simulated`` or ``live``), so reports can label the numbers honestly.
"""

import argparse
import sys

from banquet.agents import ConciergeSession
from banquet.db import connect, init_db
from banquet.harness.scenarios import SCENARIOS, score
from banquet.harness.tokens import ledger
from banquet.harness.trace import new_run_dir, write_json, write_trace
from banquet.services import BanquetService


def run_scenario(scenario, client):
    """Play one scenario's turns through a fresh :class:`ConciergeSession`.

    Args:
        scenario: A scenario dict.
        client: A :class:`~banquet.harness.simulated.SimulatedClient` or a real
            ``anthropic.Anthropic`` client.

    Returns:
        ``(transcript, events)``: one dict per turn (``say`` plus what ``send``
        returned), and every event tagged with its scenario and turn number.
    """
    conn = connect(":memory:")
    init_db(conn)
    session = ConciergeSession(client, BanquetService(conn))
    transcript, events = [], []
    for i, turn in enumerate(scenario["turns"]):
        result = session.send(turn["say"])
        transcript.append({"say": turn["say"], **result})
        events += [{**e, "scenario": scenario["id"], "turn": i} for e in result["events"]]
    return transcript, events


def run_suite(mode="simulated", only=None, max_cost=5.0, write=True):
    """Run every scenario (or one) and optionally write the run folder.

    Args:
        mode: ``"simulated"`` or ``"live"``.
        only: A scenario id to run alone, or ``None`` for all.
        max_cost: Live mode only: stop before the next scenario once the estimated
            cost so far exceeds this many dollars. It's checked between scenarios, so
            the last scenario can take the total somewhat past the cap (each conversation
            is also bounded by the per-turn token budget, G6).
        write: Whether to write ``runs/<id>/``.

    Returns:
        The summary dict (also written as ``summary.json``): ``mode``, per-scenario
        ``scores`` and ``ledger``, the suite ``ledger``, ``passed``/``total`` counts
        and ``run_dir``.
    """
    if mode == "live":
        import anthropic
        live_client = anthropic.Anthropic()
    else:
        from banquet.harness.simulated import SimulatedClient
    run_dir = new_run_dir(mode) if write else None
    scenarios = [SCENARIOS[only]] if only else list(SCENARIOS.values())
    all_events, results, spent = [], {}, 0.0
    for sc in scenarios:
        if mode == "live" and spent > max_cost:
            results[sc["id"]] = {"skipped": f"cost cap ${max_cost:.2f} reached"}
            continue
        client = live_client if mode == "live" else SimulatedClient(sc)
        transcript, events = run_scenario(sc, client)
        scores = score(sc, transcript, live=(mode == "live"))
        sc_ledger = ledger(events)
        spent += sc_ledger["totals"]["cost"]
        all_events += events
        results[sc["id"]] = {"title": sc["title"], "scores": scores, "ledger": sc_ledger,
                             "passed": all(s["ok"] for s in scores.values()),
                             "transcript": [{k: t[k] for k in ("say", "reply", "guardrails")} for t in transcript]}
        if write:
            write_trace(run_dir / sc["id"] / "trace.jsonl", events)
            write_json(run_dir / sc["id"] / "report.json", results[sc["id"]])
    summary = {"mode": mode, "scenarios": results, "ledger": ledger(all_events),
               "passed": sum(1 for r in results.values() if r.get("passed")),
               "total": len(results), "run_dir": str(run_dir) if run_dir else None}
    if write:
        write_json(run_dir / "summary.json", summary)
    return summary


def main(argv=None):
    """Command-line entry point: run the suite and print scores and the token ledger.

    Returns:
        Exit status: 0 if every scenario passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="use the real Claude API (costs money)")
    parser.add_argument("--max-cost", type=float, default=5.0, help="live mode cost cap in USD")
    parser.add_argument("--only", choices=sorted(SCENARIOS), help="run one scenario")
    args = parser.parse_args(argv)
    summary = run_suite("live" if args.live else "simulated", args.only, args.max_cost)
    label = "LIVE" if args.live else "SIMULATED (token counts are estimates)"
    print(f"\nScenario results, {label}")
    for sid, r in summary["scenarios"].items():
        if "skipped" in r:
            print(f"  SKIP {sid}: {r['skipped']}")
            continue
        failed = [k for k, s in r["scores"].items() if not s["ok"]]
        print(f"  {'PASS' if r['passed'] else 'FAIL'} {sid}" + (f"  failed: {failed}" if failed else ""))
    led = summary["ledger"]
    print("\nTokens by tool (attributed)")
    for name, t in sorted(led["tools"].items(), key=lambda kv: -kv[1]["total"]):
        print(f"  {name:<22} calls {t['calls']:>3}  total {t['total']:>8,}  ${t['cost']:.4f}")
    print("\nTokens by agent (as reported)")
    for name, a in led["agents"].items():
        print(f"  {name:<22} calls {a['calls']:>3}  total {a['total']:>8,}  ${a['cost']:.4f}")
    print(f"\n{summary['passed']}/{summary['total']} scenarios passed · {led['totals']['tokens']:,} tokens · "
          f"${led['totals']['cost']:.4f} · report: {summary['run_dir']}")
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
