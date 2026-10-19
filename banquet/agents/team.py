"""The agent team: a customer-facing Concierge that delegates to two specialists.

    Customer ──► Concierge ──► Venue Scout      (search, details, availability)
                     │    └──► Pricing Analyst  (quotes, cost comparisons)
                     └──► place_hold / show_venues

Specialists start each brief with a fresh context, so the many venue records
they read never pile up in the Concierge's conversation. They can read data
but cannot change it. Only the Concierge can place a hold, and only the
customer can confirm one (through the web UI or API, never through an agent).
"""

import threading
from datetime import date

from banquet.agents.runtime import Agent, Tool

ID = {"type": "integer", "minimum": 1}
DATE = {"type": "string", "description": "Event date, YYYY-MM-DD."}
GUESTS = {"type": "integer", "minimum": 1, "maximum": 2000}


def _obj(properties, required):
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


# ----------------------------------------------------------- shared data tools

def data_tools(service):
    return {
        "search_venues": Tool(
            "search_venues",
            "Search banquet venues. Every filter is optional; omit filters the customer "
            "didn't specify. Returns venues with the halls and packages that match. "
            "If event_date is given, each hall includes an `available` flag.",
            _obj({
                "guest_count": {**GUESTS, "description": "Seated guests; halls must fit this."},
                "city": {"type": "string", "description": "City or neighborhood, e.g. 'Brooklyn', 'Midtown'."},
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
    return date.today().strftime("%A, %B %d, %Y")


SCOUT_PROMPT = """You are the Venue Scout for Banquet Concierge, a booking service for banquet halls in the New York area. Today is {today}.

The Concierge sends you a brief describing a customer's event. Find the venues that best fit it.

How to work:
- Search with the filters the brief supports. If nothing matches, relax the least important filter and say which one you relaxed.
- When a date is known, check availability for each hall you recommend. If it's taken, report the nearest open dates.
- Look at venue details when the brief mentions amenities, atmosphere or dietary needs that the search results don't settle.

Reply with a shortlist of at most 4 options, best fit first. For each give: venue name and venue_id, hall name and hall_id, capacity and guest minimum, the most relevant package names with package_id and per-guest price, availability on the requested date, and one line on why it fits or what the trade-off is. Use only facts from your tools; never invent venues, prices or amenities. Be concise. The Concierge, not the customer, reads your reply."""

PRICING_PROMPT = """You are the Pricing Analyst for Banquet Concierge, a booking service for banquet halls in the New York area. Today is {today}.

The Concierge sends you a brief naming specific halls, packages, dates and guest counts. Build the quotes and compare them.

How to work:
- Call build_quote for every combination the brief asks about. Use get_venue_details if you need package ids or a hall's guest minimum.
- If the customer has a budget, say which options fit it. When an option is over budget or close to it, quote one realistic way to save, such as a Sunday-Thursday date, an off-peak month, a cheaper package at the same venue, or a smaller hall, and show the difference.
- Point out the guest minimums, weekend premiums and deposits that drive the price.

Reply with each option's total, per-guest cost and deposit, with its hall_id and package_id, then a short recommendation. Use only numbers from your tools. Be concise. The Concierge, not the customer, reads your reply."""

CONCIERGE_PROMPT = """You are the Concierge for Banquet Concierge, a service that helps people find and reserve banquet halls in the New York area for weddings, galas, corporate events, showers, anniversaries and other celebrations. Today is {today}.

You talk with the customer. Two specialists work for you:
- ask_venue_scout searches venues, reads venue details and checks availability. Send it a self-contained brief: event type, date or date range, guest count, area, style, cuisine, dietary needs, must-have amenities and budget, whichever are known.
- ask_pricing_analyst builds itemized quotes and compares costs. Send it the exact hall_id, package_id, date and guest count for each option, plus the budget if known.
When you need both, call them in the same turn so they run in parallel. Specialists don't see this conversation, so put everything they need in the brief.

How to help:
- Start by learning what matters: the event type, date (or flexibility), guest count and area. Ask for at most two or three missing details at a time, and search as soon as you have enough to be useful. You don't need every detail first.
- After the scout replies, call show_venues with the venue_ids you're recommending so the customer sees venue cards, then summarize the options in a few lines each.
- Quote prices only from the Pricing Analyst or a tool result. Never estimate or invent prices, availability, venues or amenities.
- When the customer wants to reserve, place_hold needs the hall, package, date, guest count, event type, and the customer's full name and email. Ask for anything missing. Only place a hold when the customer has clearly asked you to.
- A hold is free, non-binding and lasts 72 hours. You cannot confirm bookings, take payments or cancel bookings. Tell the customer to press **Confirm** on the hold in their Holds panel when they're ready. Confirming commits them to the quoted total, with a 25% deposit due.

Style: warm, efficient and specific. Keep replies short, using short bullet lists for options. Don't mention tool names, ids or internal agents to the customer."""


# ----------------------------------------------------------------------- team

class ConciergeSession:
    """One customer conversation. Holds the history and what the UI should show."""

    def __init__(self, client, service):
        self.service = service
        self.messages = []
        self.hold_ids = []
        self._lock = threading.Lock()
        self._turn = None   # per-turn UI state: events, venue cards

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
                 _obj({"brief": {"type": "string", "description": "Self-contained description of the event and what to find."}}, ["brief"]),
                 lambda brief: self._delegate(self.scout, brief)),
            Tool("ask_pricing_analyst",
                 "Delegate pricing to the Pricing Analyst. Returns itemized quotes, a comparison "
                 "and savings ideas.",
                 _obj({"brief": {"type": "string", "description": "The exact hall_id/package_id/date/guest_count options to price, plus budget."}}, ["brief"]),
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
                       "event_type": {"type": "string"},
                       "customer_name": {"type": "string"},
                       "customer_email": {"type": "string"}},
                      ["hall_id", "package_id", "event_date", "guest_count", "event_type",
                       "customer_name", "customer_email"]),
                 self._place_hold),
        ], effort="medium")

    def send(self, text):
        """Run one customer turn. Returns the reply plus everything the UI needs."""
        with self._lock:
            self._turn = {"events": [], "cards": []}
            self.messages.append({"role": "user", "content": text})
            reply = self.concierge.run(self.messages, on_event=self._turn["events"].append)
            return {"reply": reply, "events": self._turn["events"],
                    "venues": self._turn["cards"], "holds": self.holds()}

    def holds(self):
        return [self.service.get_booking(i) for i in self.hold_ids]

    def _delegate(self, agent, brief):
        return agent.run([{"role": "user", "content": brief}],
                         on_event=self._turn["events"].append)

    def _show_venues(self, venue_ids):
        cards = [self.service.get_venue(v) for v in dict.fromkeys(venue_ids)]
        self._turn["cards"] = cards
        return f"Showing {len(cards)} venue cards: " + ", ".join(c["name"] for c in cards)

    def _place_hold(self, hall_id, package_id, event_date, guest_count, event_type,
                    customer_name, customer_email):
        booking = self.service.place_hold(hall_id, package_id, event_date, guest_count,
                                          event_type, customer_name, customer_email)
        self.hold_ids.append(booking["booking_id"])
        return booking
