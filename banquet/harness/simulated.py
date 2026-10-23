"""SimulatedClient: a scripted stand-in for Claude, for offline tests, screenshots and the PDF.

It has the same ``client.beta.messages.create(**kwargs)`` method the runtime calls, and
plays each agent's part by following the scenario's ``plan`` for the current turn:

* **Concierge:** delegates to the Venue Scout, shows venue cards, asks the Pricing
  Analyst to quote, then replies; or places a hold; or declines off-topic requests.
* **Venue Scout:** searches (relaxing the city filter if nothing matches), checks
  availability of the best halls, and replies with a shortlist that includes ids.
* **Pricing Analyst:** builds a quote for every option and compares them to the budget.

Every tool call goes through the **real** runtime, guardrails and booking service, so
the tool results, venue cards, holds and guardrail events are genuine. Only the
"model" is scripted.

**Token usage is estimated, not measured.** Prompt tokens are the size of the request
(system prompt, tools and messages as JSON) divided by 4, a common rule of thumb for
English text. Each response starts with a placeholder ``thinking`` block standing in for
the model's reasoning (about 40 tokens at low effort, 120 at medium); like real thinking
blocks, it stays in the conversation history and counts toward later prompts. Output
tokens are the size of the whole response divided by 4. Caching is simulated the
way the API reports it: the part of the prompt already sent in the same conversation
is counted as ``cache_read``; only new tokens count as ``input``. Every report built
from these numbers is labeled "simulated".
"""

import json
import math
import re
import secrets
from types import SimpleNamespace as NS

CHARS_PER_TOKEN = 4
REASONING_ALLOWANCE = {"low": 40, "medium": 120}


def _plain(obj):
    """Convert content blocks (SimpleNamespace or SDK objects) to JSON-compatible values for sizing."""
    if isinstance(obj, NS):
        return {k: _plain(v) for k, v in vars(obj).items()}
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def estimate_tokens(obj):
    """Estimate tokens as JSON characters / 4, rounded up. See the module docstring."""
    return math.ceil(len(json.dumps(_plain(obj), default=str)) / CHARS_PER_TOKEN)


def _text(t):
    """A text content block."""
    return NS(type="text", text=t)


def _money(v):
    """Format a number as dollars with cents, e.g. 41310.5 -> "$41,310.50"."""
    return f"${v:,.2f}"


class SimulatedClient:
    """Scripted stand-in for ``anthropic.Anthropic`` (see the module docstring).

    Args:
        scenario: A scenario from :mod:`banquet.harness.scenarios`. Its turns' ``say``
            text identifies the turn; its ``plan`` says what to do.

    Attributes:
        requests: Every request received, for tests.
    """

    def __init__(self, scenario):
        """Set up the scripted client (see the class docstring)."""
        self.scenario = scenario
        self.requests = []
        self.memory = {}                 # what the scripted Concierge remembers between turns
        self._seen = {}                  # conversation key -> prompt tokens already sent (for caching)
        self._ids = 0
        self._prefix = secrets.token_hex(3)    # tool ids unique across clients, like real API ids
        self.beta = NS(messages=NS(create=self._create))

    # ----------------------------------------------------------------- API surface

    def _create(self, **kw):
        """Answer one Messages API request as the agent named in the system prompt."""
        self.requests.append(kw)
        system, messages = kw["system"], kw["messages"]
        if system.startswith("You are the Venue Scout"):
            content, stop = self._scout(messages)
        elif system.startswith("You are the Pricing Analyst"):
            content, stop = self._pricing(messages)
        else:
            content, stop = self._concierge(messages)
        effort = kw.get("output_config", {}).get("effort", "medium")
        reasoning = NS(type="thinking", thinking="." * (REASONING_ALLOWANCE.get(effort, 120) * CHARS_PER_TOKEN))
        content = [reasoning] + content
        return NS(content=content, stop_reason=stop, usage=self._usage(kw, content))

    def _usage(self, kw, content):
        """Estimate usage for one call (see the module docstring for the method)."""
        prompt = estimate_tokens({"system": kw["system"], "tools": kw["tools"], "messages": kw["messages"]})
        key = (kw["system"][:60], json.dumps(_plain(kw["messages"][0]), default=str)[:200])
        cached = min(self._seen.get(key, 0), prompt)
        self._seen[key] = prompt
        output = estimate_tokens(content)
        return NS(input_tokens=prompt - cached, output_tokens=output,
                  cache_creation_input_tokens=0, cache_read_input_tokens=cached)

    def _tool(self, name, **args):
        """A tool_use content block with a fresh id."""
        self._ids += 1
        return NS(type="tool_use", id=f"sim_{self._prefix}_{self._ids}", name=name, input=args)

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _tail_results(messages, start):
        """Map tool name -> list of (content, is_error) for tool results after index ``start``."""
        names, out = {}, {}
        for m in messages[start:]:
            if m["role"] == "assistant":
                for b in m["content"]:
                    if getattr(b, "type", None) == "tool_use":
                        names[b.id] = b.name
            elif isinstance(m["content"], list):
                for r in m["content"]:
                    out.setdefault(names.get(r["tool_use_id"]), []).append((r["content"], r.get("is_error", False)))
        return out

    def _plan_for(self, text):
        """Find the scenario turn whose ``say`` matches the customer's message."""
        for turn in self.scenario["turns"]:
            if turn["say"] == text:
                return turn["plan"]
        return {"action": "decline"}

    # ----------------------------------------------------------------- Concierge

    def _concierge(self, messages):
        """Play the Concierge for the current turn. Returns ``(content, stop_reason)``."""
        # The last plain-text user message is either the customer's or a G4 correction note.
        idx = max(i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str))
        corrected = messages[idx]["content"].startswith("[Guardrail]")
        cust_idx = max(i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)
                       and not m["content"].startswith("[Guardrail]"))
        plan = self._plan_for(messages[cust_idx]["content"])
        results = self._tail_results(messages, cust_idx + 1)

        if plan["action"] == "decline":
            return [_text("I'm sorry, I can only help with finding, pricing and reserving event venues. "
                          "If you're planning an event, tell me what it is, how many guests, where and when.")], "end_turn"

        if plan["action"] == "hold":
            if "place_hold" not in results:
                opt = self.memory["options"][0]
                return [self._tool("place_hold", hall_id=opt["hall_id"], package_id=opt["package_id"],
                                   event_date=opt["date"], guest_count=self.memory["guests"],
                                   event_type=self.memory["event"], customer_name=plan["name"],
                                   customer_email=plan["email"])], "tool_use"
            content, is_error = results["place_hold"][-1]
            if is_error:
                return [_text("I'd be glad to hold it. To place the hold I need the email address you'd "
                              "like it under. What's the best email for you?")], "end_turn"
            booking = json.loads(content)
            q = booking["quote"]
            return [_text(f"Done! **{booking['venue_name']} · {booking['hall_name']}** is on hold for "
                          f"{booking['event_date']}.\n- Quoted total: {_money(q['total'])}\n"
                          f"- 25% deposit on confirmation: {_money(q['deposit_due'])}\n"
                          "The hold is free and lasts 72 hours. Press **Confirm booking** in your Holds "
                          "panel when you're ready.")], "end_turn"

        # action == "search"
        self.memory.update(guests=plan["guests"], event=plan["event"])
        if "ask_venue_scout" not in results:
            brief = (f"Event: {plan['event']}\nGuests: {plan['guests']}\nState: {plan['state']}\n"
                     f"City: {plan['city']}\nDate: {plan['date']}\n"
                     + (f"Budget: {_money(plan['budget'])}\n" if plan.get("budget") else "")
                     + "Find up to 3 options; give venue_id, hall_id and package_id.")
            return [self._tool("ask_venue_scout", brief=brief)], "tool_use"
        if "ask_pricing_analyst" not in results:
            shortlist = results["ask_venue_scout"][-1][0]
            options = [{"venue_id": int(v), "hall_id": int(h), "package_id": int(p), "date": d,
                        "name": n, "status": s}
                       for v, h, p, d, n, s in re.findall(
                           r"venue_id=(\d+) hall_id=(\d+) package_id=(\d+) date=(\S+) \| (.+?) · .* · (.+)$",
                           shortlist, re.MULTILINE)]
            if not options:
                return [_text("I couldn't find a venue that fits all of that. Could you widen the area "
                              "or adjust the guest count?")], "end_turn"
            self.memory["options"] = options
            lines = "\n".join(f"Option: hall_id={o['hall_id']} package_id={o['package_id']} date={o['date']} "
                              f"guests={plan['guests']}" for o in options)
            budget = f"Budget: {_money(plan['budget'])}\n" if plan.get("budget") else ""
            return [self._tool("show_venues", venue_ids=[o["venue_id"] for o in options]),
                    self._tool("ask_pricing_analyst", brief=budget + lines)], "tool_use"

        priced = re.findall(r"^- (.+?): total (\$[\d,.]+), (\$[\d,.]+) per guest, deposit (\$[\d,.]+)(.*)$",
                            results["ask_pricing_analyst"][-1][0], re.MULTILINE)
        bullets = "\n".join(f"- **{name}**: {total} total ({per} per guest), deposit {dep}.{extra}"
                            for name, total, per, dep, extra in priced)
        n = len(priced)
        intro = (f"Here {'is' if n == 1 else 'are'} {n} option{'' if n == 1 else 's'} for your "
                 f"{plan['event']} for {plan['guests']} guests:")
        taken = [o for o in self.memory["options"] if o["status"].startswith("taken")]
        if taken:
            for o in taken:
                others = o["status"].split("nearest open ")[1].split(", ")[1:]
                bullets += (f"\nYour date is already booked at {o['name']}, so I priced its nearest open "
                            f"date, {o['date']}" + (f" (also open: {', '.join(others)})." if others else "."))
        if plan.get("ungrounded_first") and not corrected:
            # Deliberately ungrounded, to exercise guardrail G4 in the scenario suite.
            return [_text(f"{intro}\n{bullets}\nAll in, you should budget about $99,500 for an event like this.")], "end_turn"
        return [_text(f"{intro}\n{bullets}\nWould you like me to place a free 72-hour hold on one of them?")], "end_turn"

    # ----------------------------------------------------------------- Venue Scout

    def _scout(self, messages):
        """Play the Venue Scout on its one-message brief. Returns ``(content, stop_reason)``."""
        brief = messages[0]["content"]
        field = lambda name: (re.search(rf"^{name}: (.+)$", brief, re.MULTILINE) or [None, None])[1]  # noqa: E731
        guests, state, city, day = int(field("Guests")), field("State"), field("City"), field("Date")
        results = self._tail_results(messages, 1)
        searches = results.get("search_venues", [])
        if not searches:
            return [self._tool("search_venues", state=state, city=city, guest_count=guests, event_date=day)], "tool_use"
        found = json.loads(searches[-1][0])
        if not found and len(searches) == 1:
            # Nothing in that city: relax the least important filter (the city) and say so.
            return [self._tool("search_venues", state=state, guest_count=guests, event_date=day)], "tool_use"
        picks = []
        for v in found[:3]:
            hall = v["halls"][0]
            package = min(v["packages"], key=lambda p: p["per_guest_price"])
            picks.append((v, hall, package))
        if "check_availability" not in results and picks:
            return [self._tool("check_availability", hall_id=h["hall_id"], event_date=day) for _, h, _ in picks], "tool_use"
        avail = {r["hall_id"]: r for r in (json.loads(c) for c, err in results.get("check_availability", []) if not err)}
        lines = []
        for v, h, p in picks:
            a = avail.get(h["hall_id"], {"available": True})
            when = day if a["available"] else a["nearest_open_dates"][0]
            status = (f"available on {day}" if a["available"]
                      else f"taken on {day}; nearest open {', '.join(a['nearest_open_dates'])}")
            lines.append(f"- venue_id={v['venue_id']} hall_id={h['hall_id']} package_id={p['package_id']} "
                         f"date={when} | {v['name']} · {h['name']} (seats {h['seated_capacity']}, "
                         f"min {h['min_guests']}) · {p['name']} {_money(p['per_guest_price'])}/guest · {status}")
        relaxed = " (no venues in that city, so I searched the whole state)" if len(searches) > 1 else ""
        return [_text(f"Shortlist{relaxed}:\n" + ("\n".join(lines) or "No matching venues."))], "end_turn"

    # ----------------------------------------------------------------- Pricing Analyst

    def _pricing(self, messages):
        """Play the Pricing Analyst on its one-message brief. Returns ``(content, stop_reason)``."""
        brief = messages[0]["content"]
        options = re.findall(r"hall_id=(\d+) package_id=(\d+) date=(\S+) guests=(\d+)", brief)
        budget = re.search(r"Budget: \$([\d,.]+)", brief)
        results = self._tail_results(messages, 1)
        if "build_quote" not in results:
            return [self._tool("build_quote", hall_id=int(h), package_id=int(p), event_date=d, guest_count=int(n))
                    for h, p, d, n in options], "tool_use"
        lines = []
        for content, is_error in results["build_quote"]:
            if is_error:
                continue
            q = json.loads(content)
            extra = ""
            if budget:
                b = float(budget.group(1).replace(",", ""))
                diff = b - q["total"]
                extra = f" Within budget by {_money(diff)}." if diff >= 0 else f" Over budget by {_money(-diff)}."
            lines.append(f"- {q['venue']} · {q['hall']} · {q['package']} on {q['date']}: total {_money(q['total'])}, "
                         f"{_money(q['per_guest'])} per guest, deposit {_money(q['deposit_due'])}{extra}")
        return [_text("Quotes:\n" + "\n".join(lines))], "end_turn"
