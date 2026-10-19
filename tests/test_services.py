from datetime import date, datetime, timedelta

import pytest

from banquet.db import connect, init_db
from banquet.services import BanquetService, BookingError

TODAY = date(2026, 3, 2)   # a Monday


class Clock:
    def __init__(self):
        self.now = datetime(2026, 3, 2, 12, 0)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def svc(tmp_path, clock):
    conn = connect(tmp_path / "test.db")
    init_db(conn, today=TODAY)
    return BanquetService(conn, clock=clock)


def hall(svc, venue_name, hall_name):
    venue = next(v for v in svc.search_venues() if v["name"] == venue_name)
    details = svc.get_venue(venue["venue_id"])
    h = next(h for h in details["halls"] if h["name"] == hall_name)
    return h, details["packages"]


def test_search_filters_by_capacity_and_cuisine(svc):
    results = svc.search_venues(guest_count=500, cuisine="Indian")
    assert [v["name"] for v in results] == ["Marigold Manor"]
    assert [h["name"] for h in results[0]["halls"]] == ["Royal Hall"]


def test_search_respects_guest_minimum(svc):
    names = {v["name"] for v in svc.search_venues(guest_count=20)}
    assert "Larkspur Hall" not in names        # smallest hall needs 40
    assert "The Gilded Pear" in names


def test_search_dietary_requires_all_options(svc):
    results = svc.search_venues(dietary=["vegan", "halal"])
    assert [v["name"] for v in results] == ["Bayview Terrace"]
    assert [p["name"] for p in results[0]["packages"]] == ["Mediterranean"]


def test_search_with_date_flags_availability(svc):
    taken = (TODAY + timedelta(days=30)).isoformat()   # Grand Ballroom is booked
    larkspur = next(v for v in svc.search_venues(event_date=taken) if v["name"] == "Larkspur Hall")
    flags = {h["name"]: h["available"] for h in larkspur["halls"]}
    assert flags == {"Grand Ballroom": False, "Mezzanine Salon": True}


def test_seeded_booking_blocks_date_and_suggests_alternatives(svc):
    h, _ = hall(svc, "Larkspur Hall", "Grand Ballroom")
    taken = (TODAY + timedelta(days=30)).isoformat()
    result = svc.check_availability(h["hall_id"], taken)
    assert result["available"] is False
    assert len(result["nearest_open_dates"]) == 3
    assert taken not in result["nearest_open_dates"]


def test_quote_applies_weekend_peak_and_minimum(svc):
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
    h, _ = hall(svc, "Larkspur Hall", "Grand Ballroom")
    _, other = hall(svc, "Brightwater Club", "Olive Room")
    with pytest.raises(BookingError, match="not offered"):
        svc.build_quote(h["hall_id"], other[0]["package_id"], "2026-06-13", 200)


def test_quote_rejects_over_capacity(svc):
    h, packages = hall(svc, "The Gilded Pear", "Parlor Floor")
    with pytest.raises(BookingError, match="seats at most"):
        svc.build_quote(h["hall_id"], packages[0]["package_id"], "2026-06-13", 51)


def test_hold_blocks_date_then_confirm(svc):
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
    h, packages = hall(svc, "The Copperleaf", "Full Buyout")
    hold = svc.place_hold(h["hall_id"], packages[0]["package_id"], "2026-05-20", 80,
                          "party", "Ada", "ada@example.com")
    clock.now += timedelta(hours=73)
    assert svc.get_booking(hold["booking_id"])["status"] == "expired"
    assert svc.check_availability(h["hall_id"], "2026-05-20")["available"] is True
    with pytest.raises(BookingError, match="expired"):
        svc.confirm_booking(hold["booking_id"], "ada@example.com")


def test_cancel_frees_date(svc):
    h, packages = hall(svc, "The Copperleaf", "Full Buyout")
    hold = svc.place_hold(h["hall_id"], packages[0]["package_id"], "2026-05-20", 80,
                          "party", "Ada", "ada@example.com")
    svc.cancel_booking(hold["booking_id"], "ada@example.com")
    assert svc.check_availability(h["hall_id"], "2026-05-20")["available"] is True


@pytest.mark.parametrize("day, match", [
    ("2026-03-02", "future"),
    ("2029-01-01", "two years"),
    ("not-a-date", "Invalid date"),
])
def test_hold_date_validation(svc, day, match):
    h, packages = hall(svc, "The Copperleaf", "Full Buyout")
    with pytest.raises(BookingError, match=match):
        svc.place_hold(h["hall_id"], packages[0]["package_id"], day, 80, "party", "Ada", "ada@example.com")
