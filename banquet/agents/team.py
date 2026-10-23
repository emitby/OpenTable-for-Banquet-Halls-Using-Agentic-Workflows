"""The agent team: a customer-facing Concierge that delegates to two specialists.

    Customer ──► Concierge ──► Venue Scout      (search, details, availability)
                     │    └──► Pricing Analyst  (quotes, cost comparisons)
                     └──► place_hold / show_venues

Specialists start each brief with a fresh context, so the many venue records
they read never pile up in the Concierge's conversation. They can read data
but cannot change it. Only the Concierge can place a hold, and only the
customer can confirm one (through the web UI or API, never through an agent).

Guardrails wired in here (see :mod:`banquet.guardrails`):

* G1: :meth:`ConciergeSession.send` checks each customer message before any model call.
* G3: :meth:`ConciergeSession._place_hold` limits active holds and requires the email
  to come from the customer's own messages.
* G4: :meth:`ConciergeSession.send` checks every dollar amount in the reply against
  this session's tool results, and sends the reply back once for correction if needed.
* G6: one :class:`~banquet.guardrails.TokenBudget` per session, shared by all three agents.
"""

import threading
from datetime import date

from banquet import guardrails as g
from banquet.agents.runtime import Agent, Tool
from banquet.services import BookingError

ID = {"type": "integer", "minimum": 1}
DATE = {"type": "string", "description": "Event date, YYYY-MM-DD."}
GUESTS = {"type": "integer", "minimum": 1, "maximum": 2000}


def _obj(properties, required):
    """Build a strict JSON object schema: listed properties only, some required."""
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


# ----------------------------------------------------------- shared data tools

def data_tools(service):
    """Wrap the booking service's read operations as tools shared by the agents.

    Args:
        service: A :class:`~banquet.services.BanquetService`.

    Returns:
        ``{tool name: Tool}`` for ``search_venues``, ``get_venue_details``,
        ``check_availability`` and ``build_quote``. None of them can change data.
    """
    return {
        "search_venues": Tool(
            "search_venues",
            "Search banquet venues. Every filter is optional; omit filters the customer "
            "didn't specify. Returns venues with the halls and packages that match. "
            "If event_date is given, each hall includes an `available` flag.",
            _obj({
                "guest_count": {**GUESTS, "description": "Seated guests; halls must fit this."},
                "state": {"type": "string", "maxLength": 40,
                          "description": "US state name or two-letter code, e.g. 'Texas' or 'TX'. "
                                         "Use it whenever the customer names a state or a city whose state is clear."},
                "city": {"type": "string", "maxLength": 80,
                         "description": "City or neighborhood, e.g. 'Austin', 'Brooklyn', 'Midtown'."},
                "style": {"type": "string", "description": "e.g. 'ballroom', 'loft', 'waterfront', 'garden'."},
                "cuisine": {"type": "string", "description": "e.g. 'Indian', 'Italian', 'Kosher'."},
                "dietary": {"type": "array", "items": {"type": "string"},
                            "description": "Required dietary options, e.g. ['vegan', 'halal']."},
                "amenities": {"type": "array", "items": {"type": "string"},
                              "description": "Required hall amenities, e.g. ['dance floor', 'parking']."},
                "max_price_per_guest": {"type": "number", "description": "Package price ceiling per guest, USD."},
                "event_date": DATE,
            }, []),
            service.search_venues,
        ),
        "get_venue_details": Tool(
            "get_venue_details",
            "Full details for one venue: address, description, every hall with capacities, "
            "minimums, rental fee and amenities, and every catering package.",
            _obj({"venue_id": ID}, ["venue_id"]),
            service.get_venue,
        ),
        "check_availability": Tool(
            "check_availability",
            "Check whether a hall is free on a date. If it's taken, returns the nearest open dates.",
            _obj({"hall_id": ID, "event_date": DATE}, ["hall_id", "event_date"]),
            service.check_availability,
        ),
        "build_quote": Tool(
            "build_quote",
            "Itemized price quote for a hall + catering package on a date: rental, weekend and "
            "peak-season premiums, food & beverage (subject to the hall's guest minimum), "
            "service charge, tax, total, per-guest cost and deposit.",
            _obj({"hall_id": ID, "package_id": ID, "event_date": DATE, "guest_count": GUESTS},
                 ["hall_id", "package_id", "event_date", "guest_count"]),
            service.build_quote,
        ),
    }


# --------------------------------------------------------------------- prompts

def _today():
    """Today's date, written out for the prompts (e.g. "Thursday, October 22, 2026")."""
    return date.today().strftime("%A, %B %d, %Y")


SCOUT_PROMPT = """You are the Venue Scout for Banquet Concierge, a booking service for banquet halls across the United States. Today is {today}.

The Concierge sends you a brief describing a customer's event. Find the venues that best fit it.

How to work:
- Search with the filters the brief supports. Always pass `state` when the brief names a state or a city whose state is clear (e.g. Austin -> TX); pass `city` too when a city is named. If nothing matches, relax the least important filter and say which one you relaxed.
- When a date is known, check availability for each hall you recommend. If it's taken, report the nearest open dates.
- Look at venue details when the brief mentions amenities, atmosphere or dietary needs that the search results don't settle.

Reply with a shortlist of at most 4 options, best fit first. For each give: venue name and venue_id, hall name and hall_id, capacity and guest minimum, the most relevant package names with package_id and per-guest price, availability on the requested date, and one line on why it fits or what the trade-off is. Use only facts from your tools; never invent venues, prices or amenities. Be concise. The Concierge, not the customer, reads your reply."""

PRICING_PROMPT = """You are the Pricing Analyst for Banquet Concierge, a booking service for banquet halls across the United States. Today is {today}.

The Concierge sends you a brief naming specific halls, packages, dates and guest counts. Build the quotes and compare them.

How to work:
- Call build_quote for every combination the brief asks about. Use get_venue_details if you need package ids or a hall's guest minimum.
- If the customer has a budget, say which options fit it. When an option is over budget or close to it, quote one realistic way to save, such as a Sunday-Thursday date, an off-peak month, a cheaper package at the same venue, or a smaller hall, and show the difference.
- Point out the guest minimums, weekend premiums, sales tax and deposits that drive the price. Sales tax rates are approximate; say so if tax matters to the decision.

Reply with each option's total, per-guest cost and deposit, with its hall_id and package_id, then a short recommendation. Use only numbers from your tools. Be concise. The Concierge, not the customer, reads your reply."""

CONCIERGE_PROMPT = """You are the Concierge for Banquet Concierge, a service that helps people find and reserve banquet halls anywhere in the United States for weddings, galas, corporate events, showers, anniversaries and other celebrations. Today is {today}.

You talk with the customer. Two specialists work for you:
- ask_venue_scout searches venues, reads venue details and checks availability. Send it a self-contained brief: event type, date or date range, guest count, state and city or area, style, cuisine, dietary needs, must-have amenities and budget, whichever are known.
- ask_pricing_analyst builds itemized quotes and compares costs. Send it the exact hall_id, package_id, date and guest count for each option, plus the budget if known.
When you need both, call them in the same turn so they run in parallel. Specialists don't see this conversation, so put everything they need in the brief.

How to help:
- Start by learning what matters: the event type, date (or flexibility), guest count and location (state and city). We list venues in every US state and DC. Ask for at most two or three missing details at a time, and search as soon as you have enough to be useful. You don't need every detail first.
- After the scout replies, call show_venues with the venue_ids you're recommending so the customer sees venue cards, then summarize the options in a few lines each.
- Quote prices only from the Pricing Analyst or a tool result. Never estimate or invent prices, availability, venues or amenities. Every dollar amount you write is checked automatically against the quotes.
- When the customer wants to reserve, place_hold needs the hall, package, date, guest count, event type, and the customer's full name and email, exactly as the customer typed them. Ask for anything missing. A session can have at most 3 active holds. Only place a hold when the customer has clearly asked you to.
- A hold is free, non-binding and lasts 72 hours. You cannot confirm bookings, take payments or cancel bookings. Tell the customer to press **Confirm** on the hold in their Holds panel when they're ready. Confirming commits them to the quoted total, with a 25% deposit due.

Only help with finding, pricing and reserving event venues; politely decline anything else.

Style: warm, efficient and specific. Keep replies short, using short bullet lists for options. Don't mention tool names, ids or internal agents to the customer."""


# ----------------------------------------------------------------------- team

class ConciergeSession:
    """One customer conversation: the agent team, its history and what the UI should show.

    Args:
        client: An ``anthropic.Anthropic`` client (or a test/simulated stand-in).
        service: The :class:`~banquet.services.BanquetService` all tools use.
        budget: Optional :class:`~banquet.guardrails.TokenBudget`; a fresh one with the
            default limits is created if omitted.

    Turns are serialized with a lock, so one session never runs two turns at once.
    """

    def __init__(self, client, service, budget=None):
        """Build the three agents and their tools (see the class docstring)."""
        self.service = service
        self.messages = []          # the Concierge's append-only history
        self.customer_texts = []    # exactly what the customer typed (G3 email check, G4 evidence)
        self.hold_ids = []
        self.budget = budget or g.TokenBudget()
        self._lock = threading.Lock()        # one customer turn at a time
        self._hold_lock = threading.Lock()   # G3: parallel place_hold calls are checked one by one
        self._turn = None   # per-turn state: events, venue cards, guardrails that fired

        tools = data_tools(service)
        self.scout = Agent(client, "Venue Scout", SCOUT_PROMPT.format(today=_today()),
                           [tools["search_venues"], tools["get_venue_details"],
                            tools["check_availability"]], effort="low")
        self.pricing = Agent(client, "Pricing Analyst", PRICING_PROMPT.format(today=_today()),
                             [tools["get_venue_details"], tools["build_quote"]], effort="low")
        self.concierge = Agent(client, "Concierge", CONCIERGE_PROMPT.format(today=_today()), [
            Tool("ask_venue_scout",
                 "Delegate a venue search to the Venue Scout. Returns a shortlist with ids, "
                 "capacities, packages and availability.",
                 _obj({"brief": {"type": "string", "maxLength": 4000,
                                 "description": "Self-contained description of the event and what to find."}}, ["brief"]),
                 lambda brief: self._delegate(self.scout, brief)),
            Tool("ask_pricing_analyst",
                 "Delegate pricing to the Pricing Analyst. Returns itemized quotes, a comparison "
                 "and savings ideas.",
                 _obj({"brief": {"type": "string", "maxLength": 4000,
                                 "description": "The exact hall_id/package_id/date/guest_count options to price, plus budget."}}, ["brief"]),
                 lambda brief: self._delegate(self.pricing, brief)),
            tools["check_availability"],
            Tool("show_venues",
                 "Display venue cards to the customer for the venues you're recommending, in order.",
                 _obj({"venue_ids": {"type": "array", "items": ID, "minItems": 1, "maxItems": 6}}, ["venue_ids"]),
                 self._show_venues),
            Tool("place_hold",
                 "Place a free, non-binding 72-hour hold on a hall for the customer. Only call "
                 "this after the customer explicitly asks to reserve and has given their name and email.",
                 _obj({"hall_id": ID, "package_id": ID, "event_date": DATE, "guest_count": GUESTS,
                       "event_type": {"type": "string", "maxLength": 80},
                       "customer_name": {"type": "string", "maxLength": 120},
                       "customer_email": {"type": "string", "maxLength": 254}},
                      ["hall_id", "package_id", "event_date", "guest_count", "event_type",
                       "customer_name", "customer_email"]),
                 self._place_hold),
        ], effort="medium")

    def send(self, text):
        """Run one customer turn through the guardrails and the agent team.

        Steps: G1 input check (a refused message never reaches a model) -> Concierge
        run (which may delegate to the specialists) -> G4 price grounding, with at most
        one correction round.

        Args:
            text: The customer's message.

        Returns:
            ``{"reply", "events", "venues", "holds", "guardrails"}``. ``events`` is the
            turn's trace (model calls, tool calls, guardrail events); ``guardrails``
            lists which guardrails fired this turn.

        Raises:
            AgentError, BudgetExceeded, anthropic.APIError: Passed to the caller (the API
            turns them into friendly HTTP errors).
        """
        with self._lock:
            self._turn = {"events": [], "cards": [], "guardrails": []}
            emit = self._turn["events"].append
            fired = self._turn["guardrails"]

            check = g.check_customer_message(text)
            if not check.ok:
                emit({"type": "guardrail", "guardrail": "G1 input", "reason": check.reason})
                return {"reply": check.reply, "events": self._turn["events"], "venues": [],
                        "holds": self.holds(), "guardrails": [f"G1 input: {check.reason}"]}

            self.budget.start_turn()
            self.customer_texts.append(check.text)
            self.messages.append({"role": "user", "content": check.text})
            reply = self.concierge.run(self.messages, on_event=emit, budget=self.budget)

            bad = g.ungrounded_amounts(reply, self._evidence())
            if bad:
                emit({"type": "guardrail", "guardrail": "G4 grounding", "reason": "ungrounded amounts",
                      "amounts": bad})
                fired.append(f"G4 grounding: sent back {', '.join(bad)}")
                self.messages.append({"role": "user",
                                      "content": g.GROUNDING_NOTE.format(amounts=", ".join(bad))})
                reply = self.concierge.run(self.messages, on_event=emit, budget=self.budget)
                still = g.ungrounded_amounts(reply, self._evidence())
                if still:
                    emit({"type": "guardrail", "guardrail": "G4 grounding", "reason": "fallback note",
                          "amounts": still})
                    fired.append(f"G4 grounding: fallback note for {', '.join(still)}")
                    reply += g.GROUNDING_FALLBACK
            return {"reply": reply, "events": self._turn["events"], "venues": self._turn["cards"],
                    "holds": self.holds(), "guardrails": fired}

    def holds(self):
        """Return the current state of every hold placed in this session."""
        return [self.service.get_booking(i) for i in self.hold_ids]

    def _evidence(self):
        """Texts a price in the reply may come from: every tool result so far and the customer's messages."""
        texts = list(self.customer_texts)
        for m in self.messages:
            if m["role"] == "user" and isinstance(m["content"], list):
                texts += [b["content"] for b in m["content"]
                          if isinstance(b, dict) and b.get("type") == "tool_result" and not b.get("is_error")]
        return texts

    def _delegate(self, agent, brief):
        """Run a specialist on a one-message conversation (fresh context) and return its reply."""
        return agent.run([{"role": "user", "content": brief}],
                         on_event=self._turn["events"].append, budget=self.budget)

    def _show_venues(self, venue_ids):
        """Set this turn's venue cards (duplicates removed, order kept) and confirm to the agent."""
        cards = [self.service.get_venue(v) for v in dict.fromkeys(venue_ids)]
        self._turn["cards"] = cards
        return f"Showing {len(cards)} venue cards: " + ", ".join(c["name"] for c in cards)

    def _refuse_hold(self, reason):
        """Record a G3 refusal in this turn's events and guardrail list."""
        self._turn["events"].append({"type": "guardrail", "guardrail": "G3 holds", "reason": reason})
        self._turn["guardrails"].append(f"G3 holds: {reason}")

    def _place_hold(self, hall_id, package_id, event_date, guest_count, event_type,
                    customer_name, customer_email):
        """Place a hold after the G3 checks; the booking rules themselves are in the service.

        Raises:
            BookingError: If the session already has :data:`~banquet.guardrails.MAX_ACTIVE_HOLDS`
                active holds, if the email wasn't typed by the customer, or for any
                booking-rule violation. The agent receives the message and can ask the
                customer or explain.
        """
        # The Concierge can request several holds in one response, and the runtime runs them
        # in parallel. The lock makes count-check, insert and record one step, so parallel
        # calls can't all see "2 < 3" and end with more than MAX_ACTIVE_HOLDS.
        with self._hold_lock:
            if self.service.active_holds(self.hold_ids) >= g.MAX_ACTIVE_HOLDS:
                self._refuse_hold("hold limit")
                raise BookingError(f"This conversation already has {g.MAX_ACTIVE_HOLDS} active holds. "
                                   "Ask the customer to release one before placing another.")
            if not g.email_was_given(customer_email, self.customer_texts):
                self._refuse_hold("email not given by customer")
                raise BookingError("The email must be exactly what the customer typed in this conversation. "
                                   "Ask the customer for their email address.")
            booking = self.service.place_hold(hall_id, package_id, event_date, guest_count,
                                              event_type, customer_name, customer_email)
            self.hold_ids.append(booking["booking_id"])
            return booking
