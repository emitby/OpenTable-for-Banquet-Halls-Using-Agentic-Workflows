"""Token and cost accounting per agent and per tool.

The Messages API reports usage per **model call**, not per tool. This module attributes
it to tools from the events the runtime emits (see :mod:`banquet.agents.runtime`):

* **Decision tokens** (to *call* a tool): the output tokens of the model call that
  requested it. When one call requests several tools, they're split in proportion to
  the size of each tool call's arguments. Output tokens include the model's reasoning,
  so this is "what it cost to decide on and write the call".
* **Result tokens** (to *read* a tool's result): the growth in prompt size on the
  agent's next model call, minus the previous call's output (which is now part of the
  history). Split across that call's tools in proportion to their result size.
* **Specialist tokens** (delegation tools only): ``ask_venue_scout`` and
  ``ask_pricing_analyst`` run a whole agent. Everything that agent used (all its model
  calls) is attributed to the delegation tool call that started it.

Per-agent totals are exact sums of reported usage. Per-tool numbers are an
attribution, not a measurement, and they're labeled that way in reports. Results are
in whole tokens.

Events from several scenarios can be combined: ids are keyed by ``(scenario, id)``,
so two scenarios that reuse the same tool-call or run id never mix.
"""

from collections import defaultdict

#: USD per million tokens, as configured for claude-opus-5-5 in this project.
#: Check current pricing before relying on these; change them here only.
PRICES = {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20}


def cost_of(usage):
    """Cost in USD of a usage dict (``input``, ``output``, ``cache_write``, ``cache_read``)."""
    return sum(usage.get(k, 0) * PRICES[k] for k in PRICES) / 1_000_000


def prompt_tokens(usage):
    """All prompt-side tokens of one call: uncached input plus cache writes and reads."""
    return usage["input"] + usage["cache_write"] + usage["cache_read"]


def _split(total, weights):
    """Split ``total`` across ``weights`` proportionally (equally if all weights are 0)."""
    if not weights:
        return []
    s = sum(weights)
    return [total * (w / s if s else 1 / len(weights)) for w in weights]


def ledger(events):
    """Build the token ledger from one session's (or one suite's) events.

    Args:
        events: Event dicts from ``ConciergeSession.send(...)["events"]``, in order.
            Only ``model_call`` and ``tool_result`` events are used.

    Returns:
        ``{"agents": {...}, "tools": {...}, "totals": {...}}`` where

        * ``agents[name]`` = ``{calls, input, output, cache_write, cache_read, total, cost}``
        * ``tools[name]`` = ``{calls, errors, decision, result, specialist, total, cost}``
        * ``totals`` = ``{tokens, cost, model_calls, tool_calls}``

        ``tools[...].cost`` prices decision tokens as output, result tokens as input,
        and specialist tokens at the specialist's real usage mix.
    """
    agents = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cache_write": 0,
                                  "cache_read": 0, "total": 0, "cost": 0.0})
    tools = defaultdict(lambda: {"calls": 0, "errors": 0, "decision": 0.0, "result": 0.0,
                                 "specialist": 0.0, "total": 0.0, "cost": 0.0})
    runs = defaultdict(list)            # (scenario, run id) -> its model_call events, in order
    results = {}                        # (scenario, tool_use id) -> tool_result event
    run_parent = {}                     # (scenario, run id) -> (scenario, tool_use id) that started it
    names = {}                          # (scenario, tool_use id) -> tool name

    for e in events:
        scope = e.get("scenario")          # None for a single session's events
        if e.get("type") == "model_call":
            runs[(scope, e["run"])].append(e)
            run_parent[(scope, e["run"])] = (scope, e["parent"]) if e.get("parent") else None
            for tu in e["tool_uses"]:
                names[(scope, tu["id"])] = tu["name"]
        elif e.get("type") == "tool_result":
            results[(scope, e["id"])] = e

    # Exact per-agent usage.
    run_usage = {}
    for run_id, calls in runs.items():
        total_usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        for c in calls:
            a = agents[c["agent"]]
            a["calls"] += 1
            for k in total_usage:
                a[k] += c["usage"][k]
                total_usage[k] += c["usage"][k]
            a["total"] += sum(c["usage"].values())
            a["cost"] += cost_of(c["usage"])
        run_usage[run_id] = total_usage

    # Attributed per-tool usage.
    for run_id, calls in runs.items():
        for i, c in enumerate(calls):
            tus = c["tool_uses"]
            if not tus:
                continue
            decision = _split(c["usage"]["output"], [t["input_chars"] for t in tus])
            scope = run_id[0]
            if i + 1 < len(calls):
                grown = prompt_tokens(calls[i + 1]["usage"]) - prompt_tokens(c["usage"]) - c["usage"]["output"]
                sizes = [results.get((scope, t["id"]), {}).get("result_chars", 0) for t in tus]
                result = _split(max(grown, 0), sizes)
            else:
                result = [0.0] * len(tus)        # no later call (e.g. turn limit): nothing read
            for t, d, r in zip(tus, decision, result):
                row = tools[t["name"]]
                row["calls"] += 1
                row["errors"] += 0 if results.get((scope, t["id"]), {}).get("ok", True) else 1
                row["decision"] += d
                row["result"] += r
                row["cost"] += (d * PRICES["output"] + r * PRICES["input"]) / 1_000_000

    for run_id, parent in run_parent.items():
        if parent is not None and parent in names:
            row = tools[names[parent]]
            row["specialist"] += sum(run_usage[run_id].values())
            row["cost"] += cost_of(run_usage[run_id])

    for row in tools.values():
        for k in ("decision", "result", "specialist"):
            row[k] = round(row[k])
        row["total"] = row["decision"] + row["result"] + row["specialist"]
        row["cost"] = round(row["cost"], 4)
    for row in agents.values():
        row["cost"] = round(row["cost"], 4)

    totals = {"tokens": sum(a["total"] for a in agents.values()),
              "cost": round(sum(a["cost"] for a in agents.values()), 4),
              "model_calls": sum(a["calls"] for a in agents.values()),
              "tool_calls": sum(t["calls"] for t in tools.values())}
    return {"agents": dict(agents), "tools": dict(tools), "totals": totals}
