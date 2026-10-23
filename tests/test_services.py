"""Booking-rule tests: search, availability, pricing and holds (no agents, no API calls).

The database is in memory (``":memory:"``), so tests never write outside the project.
Search assertions about specific venues are limited to the original 10 New York-area
venues (``ORIGINAL``), because the generated venues in every state would otherwise
also match broad filters.
"""

from datetime import date, datetime, timedelta

import pytest

from banquet.db import VENUES, connect, init_db
from banquet.services import BanquetService, BookingError

TODAY = date(2026, 3, 2)   # a Monday
ORIGINAL = {v[0] for v in VENUES}


class Clock:
    """A controllable clock: tests move ``now`` forward to expire holds."""

    def __init__(self):
        """Start at noon on :data:`TODAY`."""
        self.now = datetime(2026, 3, 2, 12, 0)

    def __call__(self):
        """Return the current fake time."""
        return self.now


@pytest.fixture
def clock():
    """A fresh :class:`Clock`."""
    return Clock()


@pytest.fixture
def svc(clock):
    """A booking service over a freshly seeded in-memory database."""
    conn = connect(":memory:")
    init_db(conn, today=TODAY)
    return BanquetService(conn, clock=clock)


def originals(results):
    """Keep only the original New York-area venues from search results."""
    return [v for v in results if v["name"] in ORIGINAL]


def hall(svc, venue_name, hall_name):
    """Look up a hall and its venue's packages by name."""
    venue = next(v for v in svc.search_venues() if v["name"] == venue_name)
    details = svc.get_venue(venue["venue_id"])
    h = next(h for h in details["halls"] if h["name"] == hall_name)
    return h, details["packages"]


def test_search_filters_by_capacity_and_cuisine(svc):
    """Guest count and cuisine filters narrow results to the halls and packages that fit."""
    results = originals(svc.search_venues(guest_count=500, cuisine="Indian"))
    assert [v["name"] for v in results] == ["Marigold Manor"]
    assert [h["name"] for h in results[0]["halls"]] == ["Royal Hall"]


def test_search_respects_guest_minimum(svc):
    """A hall whose guest minimum is above the party size is excluded."""
    names = {v["name"] for v in svc.search_venues(guest_count=20)}
    assert "Larkspur Hall" not in names        # smallest hall needs 40
    assert "The Gilded Pear" in names


def test_search_dietary_requires_all_options(svc):
    """Every requested dietary option must be offered by the same package."""
    results = originals(svc.search_venues(dietary=["vegan", "halal"]))
    assert [v["name"] for v in results] == ["Bayview Terrace"]
    assert [p["name"] for p in results[0]["packages"]] == ["Mediterranean"]


def test_search_with_date_flags_availability(svc):
    """With a date, each hall in the results says whether it's free."""
    taken = (TODAY + timedelta(days=30)).isoformat()   # Grand Ballroom is booked
    larkspur = next(v for v in svc.search_venues(event_date=taken) if v["name"] == "Larkspur Hall")
    flags = {h["name"]: h["available"] for h in larkspur["halls"]}
    assert flags == {"Grand Ballroom": False, "Mezzanine Salon": True}


def test_seeded_booking_blocks_date_and_suggests_alternatives(svc):
    """A booked date is unavailable, and three other open dates are suggested."""
    h, _ = hall(svc, "Larkspur Hall", "Grand Ballroom")
    taken = (TODAY + timedelta(days=30)).isoformat()
    result = svc.check_availability(h["hall_id"], taken)
    assert result["available"] is False
    assert len(result["nearest_open_dates"]) == 3
    assert taken not in result["nearest_open_dates"]


def test_quote_applies_weekend_peak_and_minimum(svc):
    """The quote applies weekend and peak premiums, the guest minimum, service, NYC tax and deposit."""
    h, packages = hall(svc, "Larkspur Hall", "Grand Ballroom")
    pkg = packages[0]                                   # Classic Plated, $165
    quote = svc.build_quote(h["hall_id"], pkg["package_id"], "2026-06-13", 100)  # June Saturday
    items = {line["item"].split(" (")[0]: line["amount"] for line in quote["line_items"]}
    assert items["Friday/Saturday premium"] == 3000      # 25% of 12,000
    assert items["Peak-season premium"] == 1800          # 15% of 12,000
    assert items["Classic Plated"] == 165 * 150          # billed at the 150 minimum
    assert quote["notes"]
    subtotal = 12000 + 3000 + 1800 + 24750 * 1.2
    assert quote["total"] == pytest.approx(subtotal * 1.08875, abs=0.01)
    assert quote["deposit_due"] == pytest.approx(quote["total"] * 0.25, abs=0.01)


def test_quote_rejects_package_from_another_venue(svc):
    """A package can only be quoted with a hall at the same venue."""
    h, _ = hall(svc, "Larkspur Hall", "Grand Ballroom")
    _, other = hall(svc, "Brightwater Club", "Olive Room")
    with pytest.raises(BookingError, match="not offered"):
        svc.build_quote(h["hall_id"], other[0]["package_id"], "2026-06-13", 200)


def test_quote_rejects_over_capacity(svc):
    """More guests than the hall seats is refused."""
    h, packages = hall(svc, "The Gilded Pear", "Parlor Floor")
    with pytest.raises(BookingError, match="seats at most"):
        svc.build_quote(h["hall_id"], packages[0]["package_id"], "2026-06-13", 51)


def test_hold_blocks_date_then_confirm(svc):
    """A hold takes the date; only the matching email can confirm it."""
    h, packages = hall(svc, "The Copperleaf", "Full Buyout")
    hold = svc.place_hold(h["hall_id"], packages[0]["package_id"], "2026-05-20", 80,
                          "rehearsal dinner", "Ada Lovelace", "Ada@Example.com")
    assert hold["status"] == "hold"
    assert hold["customer_email"] == "ada@example.com"
    assert svc.check_availability(h["hall_id"], "2026-05-20")["available"] is False
    with pytest.raises(BookingError, match="not available"):
        svc.place_hold(h["hall_id"], packages[0]["package_id"], "2026-05-20", 80,
                       "party", "Someone Else", "x@example.com")

    with pytest.raises(BookingError, match="doesn't match"):
        svc.confirm_booking(hold["booking_id"], "wrong@example.com")
    confirmed = svc.confirm_booking(hold["booking_id"], "ada@example.com")
    assert confirmed["status"] == "confirmed"


def test_expired_hold_frees_date_and_cannot_be_confirmed(svc, clock):
    """After 72 hours a hold reads as expired, frees the date and can't be confirmed."""
    h, packages = hall(svc, "The Copperleaf", "Full Buyout")
    hold = svc.place_hold(h["hall_id"], packages[0]["package_id"], "2026-05-20", 80,
                          "party", "Ada", "ada@example.com")
    clock.now += timedelta(hours=73)
    assert svc.get_booking(hold["booking_id"])["status"] == "expired"
    assert svc.check_availability(h["hall_id"], "2026-05-20")["available"] is True
    with pytest.raises(BookingError, match="expired"):
        svc.confirm_booking(hold["booking_id"], "ada@example.com")


def test_cancel_frees_date(svc):
    """Cancelling (releasing) a hold frees its date; the row is kept, not deleted."""
    h, packages = hall(svc, "The Copperleaf", "Full Buyout")
    hold = svc.place_hold(h["hall_id"], packages[0]["package_id"], "2026-05-20", 80,
                          "party", "Ada", "ada@example.com")
    svc.cancel_booking(hold["booking_id"], "ada@example.com")
    assert svc.check_availability(h["hall_id"], "2026-05-20")["available"] is True
    assert svc.get_booking(hold["booking_id"])["status"] == "cancelled"


@pytest.mark.parametrize("day, match", [
    ("2026-03-02", "future"),
    ("2029-01-01", "two years"),
    ("not-a-date", "Invalid date"),
])
def test_hold_date_validation(svc, day, match):
    """Holds need a valid date in the future and within two years."""
    h, packages = hall(svc, "The Copperleaf", "Full Buyout")
    with pytest.raises(BookingError, match=match):
        svc.place_hold(h["hall_id"], packages[0]["package_id"], day, 80, "party", "Ada", "ada@example.com")
