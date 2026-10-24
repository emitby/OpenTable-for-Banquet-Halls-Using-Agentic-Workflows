"""Independent pricing cross-check: re-derive quotes from the raw tables and compare.

The Pricing Analyst's numbers all come from ``BanquetService.build_quote``, and
guardrail G4 only checks that the Concierge repeats them faithfully. If the pricing code
itself were wrong, every quote would be wrong *consistently*, and nothing else would
notice. This module prices the same requests a second way: plain SQL on the venue,
hall and package tables, and the pricing rules as they're documented in the README and
``docs/architecture.md``, written out again from scratch. It imports nothing from
``banquet.services``.

Documented rules:

* rental = hall fee; +25% of the fee on a Friday or Saturday; +15% in May, June,
  September, October or December
* food & beverage = per-guest package price x max(guests, hall minimum)
* service charge = 20% of food & beverage
* tax = venue tax rate x (rental + premiums + food & beverage + service)
* deposit = 25% of the total; per guest = total / guests (the guests actually coming)

The sample is every hall in every state (302 halls), each with its venue's cheapest and
most expensive package, on four dates (a weekday off-peak, a Saturday off-peak, a
weekday in a peak month, a Saturday in a peak month), and two party sizes (below the
hall minimum where one exists, and near capacity). Totals must agree to the cent.
"""

import itertools
from datetime import date, timedelta

#: Documented rule constants, restated here on purpose rather than imported.
WEEKEND_DAYS = {4, 5}                   # Friday, Saturday (Monday = 0)
WEEKEND_RATE = 0.25
PEAK_MONTHS = {5, 6, 9, 10, 12}
PEAK_RATE = 0.15
SERVICE_RATE = 0.20
DEPOSIT_RATE = 0.25


def independent_quote(hall, package, tax_rate, day, guests):
    """Price one request from raw table values, using only the documented rules.

    Args:
        hall: Row with ``rental_fee`` and ``min_guests``.
        package: Row with ``per_guest_price``.
        tax_rate: The venue's tax rate (e.g. 0.0625).
        day: A ``datetime.date``.
        guests: Guests attending.

    Returns:
        ``{"total", "deposit_due", "per_guest"}``, each rounded to cents.
    """
    fee = hall["rental_fee"]
    rental = fee + (fee * WEEKEND_RATE if day.weekday() in WEEKEND_DAYS else 0) \
        + (fee * PEAK_RATE if day.month in PEAK_MONTHS else 0)
    food = package["per_guest_price"] * max(guests, hall["min_guests"])
    pre_tax = rental + food * (1 + SERVICE_RATE)
    total = pre_tax * (1 + tax_rate)
    return {"total": round(total, 2), "deposit_due": round(total * DEPOSIT_RATE, 2),
            "per_guest": round(total / guests, 2)}


def _sample_dates(today):
    """Four future dates: weekday and Saturday, off-peak (March) and peak (October)."""
    year = today.year + 1

    def first(month, weekday):
        """The first given weekday of a month next year."""
        d = date(year, month, 1)
        return d + timedelta(days=(weekday - d.weekday()) % 7)
    return [first(3, 1), first(3, 5), first(10, 2), first(10, 5)]


def crosscheck(service, today=None):
    """Compare ``service.build_quote`` with :func:`independent_quote` across the sample.

    Args:
        service: A ``BanquetService`` (its ``conn`` is read with plain SQL).
        today: Base date for the sample dates (default: today).

    Returns:
        ``{"checked", "mismatches": [...], "states", "halls", "ok"}``. Each mismatch names
        the request and both answers.
    """
    conn = service.conn
    rows = conn.execute(
        "SELECT h.id AS hall_id, h.rental_fee, h.min_guests, h.seated_capacity, h.venue_id,"
        " v.tax_rate, v.state FROM halls h JOIN venues v ON v.id = h.venue_id ORDER BY h.id").fetchall()
    checked, mismatches, states = 0, [], set()
    for hall in rows:
        states.add(hall["state"])
        packages = conn.execute("SELECT id, per_guest_price FROM packages WHERE venue_id = ?"
                                " ORDER BY per_guest_price", (hall["venue_id"],)).fetchall()
        picks = {packages[0]["id"]: packages[0], packages[-1]["id"]: packages[-1]}.values()
        sizes = sorted({max(1, hall["min_guests"] - 5), hall["seated_capacity"]})
        for package, day, guests in itertools.product(picks, _sample_dates(today or date.today()), sizes):
            app = service.build_quote(hall["hall_id"], package["id"], day.isoformat(), guests)
            ours = independent_quote(hall, package, hall["tax_rate"], day, guests)
            checked += 1
            diffs = {k: (app[k], v) for k, v in ours.items() if abs(app[k] - v) > 0.011}
            if diffs:
                mismatches.append({"hall_id": hall["hall_id"], "package_id": package["id"],
                                   "date": day.isoformat(), "guests": guests, "app_vs_independent": diffs})
    return {"checked": checked, "mismatches": mismatches, "states": len(states), "halls": len(rows),
            "ok": checked > 0 and not mismatches}
