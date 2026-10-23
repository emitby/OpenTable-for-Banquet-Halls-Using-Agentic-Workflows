"""Nationwide search: every state has venues, the state filter, and per-state sales tax."""

from datetime import date

import pytest

from banquet.db import connect, generated_venues, init_db
from banquet.services import BanquetService, BookingError
from banquet.states import STATES, normalize_state


@pytest.fixture(scope="module")
def svc():
    """One seeded in-memory service shared by this module's read-only tests."""
    conn = connect(":memory:")
    init_db(conn, today=date(2026, 3, 2))
    return BanquetService(conn)


@pytest.mark.parametrize("value, code", [("TX", "TX"), ("tx", "TX"), ("Texas", "TX"), (" new york ", "NY"),
                                         ("Washington", "WA"), ("Washington DC", "DC"), ("D.C.", "DC"),
                                         ("Washington, D.C.", "DC"), ("Washington state", "WA"),
                                         ("New York State", "NY")])
def test_normalize_state_accepts_names_and_codes(value, code):
    """Codes and names work in any case; 'Washington' alone is the state, 'Washington DC' is DC."""
    assert normalize_state(value) == code


def test_normalize_state_rejects_unknown():
    """An unknown state raises an error that says how to write a state."""
    with pytest.raises(ValueError, match="two-letter code"):
        normalize_state("Narnia")


def test_every_state_and_dc_has_venues(svc):
    """A state-only search returns venues in all 50 states plus DC, all in that state."""
    assert len(STATES) == 51
    for code in STATES:
        results = svc.search_venues(state=code)
        assert len(results) >= 4, code
        assert {v["state"] for v in results} == {code}


def test_generated_venue_names_are_unique():
    """The name generator never repeats a name (see the reasoning in db.py)."""
    names = [v[0] for v in generated_venues()]
    assert len(names) == len(set(names))


def test_state_filter_combines_with_city_and_guests(svc):
    """State, city and guest filters combine."""
    results = svc.search_venues(state="Texas", city="Austin", guest_count=150)
    assert results and all(v["state"] == "TX" and "Austin" in v["location"] for v in results)


def test_search_unknown_state_is_a_booking_error(svc):
    """An unknown state comes back as a BookingError, which agents receive as a fixable tool error."""
    with pytest.raises(BookingError, match="Unknown state"):
        svc.search_venues(state="Atlantis")


def test_quote_uses_the_venue_state_tax(svc):
    """Quotes use the venue's state tax rate and say it's approximate."""
    v = svc.search_venues(state="OR")[0]          # Oregon has no general sales tax
    q = svc.build_quote(v["halls"][0]["hall_id"], v["packages"][0]["package_id"], "2026-11-04", 100)
    tax = next(line for line in q["line_items"] if line["item"].startswith("Sales tax"))
    assert tax["item"] == "Sales tax (OR 0.000%)" and tax["amount"] == 0
    assert q["state"] == "OR"
    assert any("approximate" in n for n in q["notes"])
