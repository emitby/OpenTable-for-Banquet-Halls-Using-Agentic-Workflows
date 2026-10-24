"""Booking domain logic: search, availability, quotes and holds.

This module knows nothing about agents. The agents call it through tools, and
the web API calls it directly for the human-only actions (confirm, cancel).
Every number a customer sees (prices, totals, deposits, availability) is computed
here, in plain Python, never by a model.
"""

import functools
import json
import threading
from datetime import date, datetime, timedelta

from banquet.states import normalize_state

# Pricing rules
WEEKEND_PREMIUM = 0.25          # Friday and Saturday rental premium
PEAK_MONTHS = {5, 6, 9, 10, 12}
PEAK_PREMIUM = 0.15             # applied to rental in peak months
SERVICE_CHARGE = 0.20           # on food & beverage
# Sales tax is per venue (venues.tax_rate): the approximate state base rate, or a
# combined local rate for the original New York and New Jersey venues. See states.py.
DEPOSIT_RATE = 0.25
HOLD_HOURS = 72
MAX_BOOKING_DAYS_OUT = 730


class BookingError(ValueError):
    """A request the booking rules don't allow. The message is safe to show users."""


def _parse_date(value):
    """Parse a ``YYYY-MM-DD`` string (or pass a ``date`` through).

    Args:
        value: A ``date`` or ISO date string.

    Returns:
        A :class:`datetime.date`.

    Raises:
        BookingError: If the value isn't a valid ISO date, with the expected format in
            the message so an agent can retry correctly.
    """
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise BookingError(f"Invalid date {value!r}; use YYYY-MM-DD.") from None


def _serialized(method):
    """Run a :class:`BanquetService` method while holding the service's lock.

    One SQLite connection is shared by every agent, and agents run tool calls in parallel
    threads. Python's ``sqlite3`` connection isn't safe for concurrent use: overlapping
    queries can return another query's rows or none at all (a live eval trial hit this:
    ``get_venue_details`` failed with an empty hall row). So every public method, reads
    included, runs one at a time. The lock is re-entrant, because write methods call read
    methods (``place_hold`` calls ``build_quote`` and ``get_booking``).
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        """Call ``method`` under ``self._lock``."""
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class BanquetService:
    """The booking rules over a SQLite connection: search, availability, quotes, holds.

    Args:
        conn: A connection from :func:`banquet.db.connect`.
        clock: A function returning "now" as a ``datetime``. Tests pass a fake clock to
            control hold expiry.

    Every public method holds ``self._lock`` (see :func:`_serialized`): reads, because the
    shared connection isn't safe for concurrent queries, and writes, so two requests
    can't book the same hall and date at once.
    """

    def __init__(self, conn, clock=datetime.now):
        """Store the connection and clock (see the class docstring)."""
        self.conn = conn
        self.clock = clock
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ reads

    @_serialized
    def search_venues(self, guest_count=None, city=None, state=None, style=None, cuisine=None,
                      dietary=None, amenities=None, max_price_per_guest=None,
                      event_date=None):
        """Return venues with at least one hall that fits every given filter.

        All filters are optional. Text filters match case-insensitively as substrings.

        Args:
            guest_count: A hall must seat at least this many and have a guest minimum at
                or below it.
            city: Matched against the venue's city and neighborhood ("Brooklyn", "Austin").
            state: A US state name or code ("Texas", "TX"); only venues in that state.
            style: Matched against the venue style ("ballroom", "barn").
            cuisine: At least one package with this cuisine.
            dietary: Every listed option must be offered by the same package.
            amenities: Every listed amenity must be in the same hall.
            max_price_per_guest: Package price ceiling in USD.
            event_date: If given, each hall gets an ``available`` flag for that date.

        Returns:
            A list of venue summaries, best rated first, each with only the halls and
            packages that matched.

        Raises:
            BookingError: For an invalid date or an unknown state.
        """
        event_date = _parse_date(event_date) if event_date else None
        if state:
            try:
                state = normalize_state(state)
            except ValueError as exc:
                raise BookingError(str(exc)) from None
            venues = self.conn.execute("SELECT * FROM venues WHERE state = ? ORDER BY rating DESC",
                                       (state,)).fetchall()
        else:
            venues = self.conn.execute("SELECT * FROM venues ORDER BY rating DESC").fetchall()
        results = []
        for v in venues:
            if city and city.lower() not in f"{v['city']} {v['neighborhood']}".lower():
                continue
            if style and style.lower() not in v["style"].lower():
                continue

            packages = self._packages(v["id"])
            if cuisine:
                packages = [p for p in packages if cuisine.lower() in p["cuisine"].lower()]
            if dietary:
                wanted = {d.lower() for d in dietary}
                packages = [p for p in packages if wanted <= {d.lower() for d in p["dietary_options"]}]
            if max_price_per_guest is not None:
                packages = [p for p in packages if p["per_guest_price"] <= max_price_per_guest]
            if not packages:
                continue

            halls = []
            for h in self._halls(v["id"]):
                if guest_count and not (h["min_guests"] <= guest_count <= h["seated_capacity"]):
                    continue
                if amenities:
                    have = {a.lower() for a in h["amenities"]}
                    if not all(any(a.lower() in x for x in have) for a in amenities):
                        continue
                if event_date:
                    h["available"] = self._is_free(h["hall_id"], event_date)
                halls.append(h)
            if not halls:
                continue

            results.append({
                "venue_id": v["id"],
                "name": v["name"],
                "location": f"{v['neighborhood']}, {v['city']}, {v['state']}",
                "state": v["state"],
                "style": v["style"],
                "rating": v["rating"],
                "halls": [{k: h[k] for k in ("hall_id", "name", "min_guests", "seated_capacity",
                                              "rental_fee", "available") if k in h} for h in halls],
                "packages": [{k: p[k] for k in ("package_id", "name", "cuisine", "per_guest_price")}
                             for p in packages],
            })
        return results

    @_serialized
    def get_venue(self, venue_id):
        """Return one venue with every hall and package.

        Args:
            venue_id: The venue's id.

        Returns:
            A dict with location (including ``state``), description, rating, the sales
            ``tax_rate`` used in its quotes, and full ``halls`` and ``packages`` lists.

        Raises:
            BookingError: If there's no such venue.
        """
        v = self.conn.execute("SELECT * FROM venues WHERE id = ?", (venue_id,)).fetchone()
        if v is None:
            raise BookingError(f"No venue with id {venue_id}.")
        return {
            "venue_id": v["id"], "name": v["name"], "neighborhood": v["neighborhood"],
            "city": v["city"], "state": v["state"], "address": v["address"], "style": v["style"],
            "description": v["description"], "rating": v["rating"], "tax_rate": v["tax_rate"],
            "halls": self._halls(venue_id), "packages": self._packages(venue_id),
        }

    @_serialized
    def check_availability(self, hall_id, event_date, alternatives=3):
        """Report whether a hall is free on a date, with the nearest free dates if not.

        A date is taken by a confirmed booking or an unexpired hold. Alternatives are
        searched outward from the date (one day later, one day earlier, two days ...)
        up to 60 days, skipping past dates.

        Args:
            hall_id: The hall's id.
            event_date: ``YYYY-MM-DD``.
            alternatives: How many open dates to suggest.

        Returns:
            ``{hall_id, hall, venue, date, available}`` plus ``nearest_open_dates`` when
            the date is taken.

        Raises:
            BookingError: For an unknown hall or invalid date.
        """
        hall = self._hall(hall_id)
        day = _parse_date(event_date)
        free = self._is_free(hall_id, day)
        result = {"hall_id": hall_id, "hall": hall["name"], "venue": hall["venue_name"],
                  "date": day.isoformat(), "available": free}
        if not free:
            today = self.clock().date()
            nearby = []
            for offset in range(1, 61):
                for candidate in (day + timedelta(days=offset), day - timedelta(days=offset)):
                    if candidate > today and self._is_free(hall_id, candidate):
                        nearby.append(candidate)
                if len(nearby) >= alternatives:
                    break
            result["nearest_open_dates"] = [d.isoformat() for d in sorted(nearby[:alternatives])]
        return result

    @_serialized
    def build_quote(self, hall_id, package_id, event_date, guest_count):
        """Price a hall + catering package for a date and guest count.

        Formula (all rules are the constants at the top of this module):

        * rental = hall fee, plus 25% on Friday/Saturday, plus 15% in peak months
        * food & beverage = package price x max(guests, hall minimum)
        * service charge = 20% of food & beverage
        * tax = venue tax rate x (rental + premiums + food & beverage + service)
        * deposit = 25% of the total

        Args:
            hall_id: The hall's id.
            package_id: A catering package offered by the same venue.
            event_date: ``YYYY-MM-DD``.
            guest_count: Expected guests (at least 1, at most the hall's seated capacity).

        Returns:
            ``{venue, hall, package, state, date, guest_count, line_items, total,
            per_guest, deposit_due, notes}``, with amounts rounded to cents.

        Raises:
            BookingError: For an unknown hall or package, a package from another venue,
                an invalid date, or a guest count out of range.
        """
        hall = self._hall(hall_id)
        package = self._package(package_id)
        if package["venue_id"] != hall["venue_id"]:
            raise BookingError(f"Package {package_id} is not offered at {hall['venue_name']}.")
        day = _parse_date(event_date)
        if guest_count < 1:
            raise BookingError("Guest count must be at least 1.")
        if guest_count > hall["seated_capacity"]:
            raise BookingError(f"{hall['name']} seats at most {hall['seated_capacity']} guests.")

        rental = hall["rental_fee"]
        adjustments = []
        if day.weekday() in (4, 5):
            adjustments.append(("Friday/Saturday premium", rental * WEEKEND_PREMIUM))
        if day.month in PEAK_MONTHS:
            adjustments.append(("Peak-season premium", rental * PEAK_PREMIUM))

        billed_guests = max(guest_count, hall["min_guests"])
        food_beverage = package["per_guest_price"] * billed_guests
        service = food_beverage * SERVICE_CHARGE
        subtotal = rental + sum(a for _, a in adjustments) + food_beverage + service
        tax = subtotal * hall["tax_rate"]
        total = subtotal + tax

        lines = [{"item": f"{hall['name']} rental", "amount": round(rental, 2)}]
        lines += [{"item": label, "amount": round(amount, 2)} for label, amount in adjustments]
        lines.append({
            "item": f"{package['name']} ({billed_guests} × ${package['per_guest_price']:.2f})",
            "amount": round(food_beverage, 2),
        })
        lines.append({"item": f"Service charge ({SERVICE_CHARGE:.0%})", "amount": round(service, 2)})
        lines.append({"item": f"Sales tax ({hall['state']} {hall['tax_rate']:.3%})", "amount": round(tax, 2)})

        notes = []
        if billed_guests > guest_count:
            notes.append(f"{hall['name']} has a {hall['min_guests']}-guest minimum; "
                         f"food & beverage is billed for {billed_guests}.")
        notes.append("Sales tax is an approximate rate; local and meals taxes may differ.")
        return {
            "venue": hall["venue_name"], "hall": hall["name"], "package": package["name"],
            "state": hall["state"], "date": day.isoformat(), "guest_count": guest_count,
            "line_items": lines, "total": round(total, 2),
            "per_guest": round(total / guest_count, 2),
            "deposit_due": round(total * DEPOSIT_RATE, 2), "notes": notes,
        }

    @_serialized
    def get_booking(self, booking_id):
        """Return one booking with its venue, hall and package names.

        An unexpired hold reads as ``"hold"``. A hold past its expiry reads as
        ``"expired"``, computed on read, so no background job is needed.

        Args:
            booking_id: The booking's id.

        Returns:
            The booking as a dict, with ``booking_id`` instead of ``id``.

        Raises:
            BookingError: If there's no such booking.
        """
        row = self.conn.execute(
            "SELECT b.*, h.name AS hall_name, v.name AS venue_name, p.name AS package_name"
            " FROM bookings b JOIN halls h ON h.id = b.hall_id JOIN venues v ON v.id = h.venue_id"
            " LEFT JOIN packages p ON p.id = b.package_id WHERE b.id = ?",
            (booking_id,),
        ).fetchone()
        if row is None:
            raise BookingError(f"No booking with id {booking_id}.")
        booking = dict(row)
        booking["booking_id"] = booking.pop("id")
        if booking["status"] == "hold" and self._hold_expired(booking):
            booking["status"] = "expired"
        return booking

    # ----------------------------------------------------------------- writes

    @_serialized
    def place_hold(self, hall_id, package_id, event_date, guest_count, event_type,
                   customer_name, customer_email):
        """Reserve a hall and date for ``HOLD_HOURS``. A hold is free and non-binding.

        Args:
            hall_id, package_id, event_date, guest_count: As for :meth:`build_quote`.
            event_type: e.g. "wedding".
            customer_name: Required, non-blank.
            customer_email: Required; stored lower-cased and used to prove ownership
                when confirming or cancelling.

        Returns:
            The new booking (status ``"hold"``) plus its ``quote``.

        Raises:
            BookingError: For a past date or one more than two years out, a missing name
                or email, any quote error, or a date that's already taken.

        Side effects:
            Inserts a booking row and commits. The availability check and insert happen
            under the write lock, so two holds can't take the same date.
        """
        day = _parse_date(event_date)
        today = self.clock().date()
        if day <= today:
            raise BookingError("Event date must be in the future.")
        if day > today + timedelta(days=MAX_BOOKING_DAYS_OUT):
            raise BookingError("Bookings open at most two years ahead.")
        if "@" not in customer_email or not customer_name.strip():
            raise BookingError("A customer name and valid email are required to place a hold.")

        quote = self.build_quote(hall_id, package_id, day, guest_count)
        with self._lock:
            if not self._is_free(hall_id, day):
                raise BookingError(f"{quote['hall']} is not available on {day.isoformat()}.")
            now = self.clock()
            booking_id = self.conn.execute(
                "INSERT INTO bookings (hall_id, package_id, event_date, guest_count, event_type,"
                " customer_name, customer_email, status, hold_expires_at, quote_total, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'hold', ?, ?, ?)",
                (hall_id, package_id, day.isoformat(), guest_count, event_type,
                 customer_name.strip(), customer_email.strip().lower(),
                 (now + timedelta(hours=HOLD_HOURS)).isoformat(timespec="seconds"),
                 quote["total"], now.isoformat(timespec="seconds")),
            ).lastrowid
            self.conn.commit()
        return {**self.get_booking(booking_id), "quote": quote}

    @_serialized
    def confirm_booking(self, booking_id, customer_email):
        """Turn a hold into a confirmed booking. Only the customer can do this.

        No agent has a tool for this method: it's reached only through the API endpoint
        or the CLI ``/confirm`` command, after the customer's explicit action.

        Args:
            booking_id: The hold's id.
            customer_email: Must match the email on the hold.

        Returns:
            The updated booking.

        Raises:
            BookingError: For the wrong email, an expired hold, or a booking not on hold.

        Side effects:
            Updates the booking row and commits.
        """
        with self._lock:
            booking = self._owned_booking(booking_id, customer_email)
            if booking["status"] == "expired":
                raise BookingError("This hold has expired. Ask the concierge to place a new one.")
            if booking["status"] != "hold":
                raise BookingError(f"Booking {booking_id} is {booking['status']}, not on hold.")
            self.conn.execute(
                "UPDATE bookings SET status = 'confirmed', hold_expires_at = NULL WHERE id = ?",
                (booking_id,),
            )
            self.conn.commit()
        return self.get_booking(booking_id)

    @_serialized
    def cancel_booking(self, booking_id, customer_email):
        """Cancel a booking or release a hold (customer only, like :meth:`confirm_booking`).

        The row is kept with status ``"cancelled"``; nothing is deleted.

        Args:
            booking_id: The booking's id.
            customer_email: Must match the email on the booking.

        Returns:
            The updated booking.

        Raises:
            BookingError: For the wrong email or a booking that's already cancelled.

        Side effects:
            Updates the booking row and commits.
        """
        with self._lock:
            booking = self._owned_booking(booking_id, customer_email)
            if booking["status"] == "cancelled":
                raise BookingError(f"Booking {booking_id} is already cancelled.")
            self.conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
            self.conn.commit()
        return self.get_booking(booking_id)

    # ---------------------------------------------------------------- helpers

    @_serialized
    def active_holds(self, booking_ids):
        """Count the unexpired holds among some booking ids (used by the hold-limit guardrail).

        Args:
            booking_ids: Booking ids, e.g. the holds placed in one chat session.

        Returns:
            How many of them are still on hold.
        """
        return sum(1 for b in booking_ids if self.get_booking(b)["status"] == "hold")

    def _owned_booking(self, booking_id, customer_email):
        """Return a booking if ``customer_email`` matches its email, else raise ``BookingError``."""
        booking = self.get_booking(booking_id)
        if booking["customer_email"] != (customer_email or "").strip().lower():
            raise BookingError("That email doesn't match the booking.")
        return booking

    def _hold_expired(self, booking):
        """True if a booking has a hold expiry time that has passed."""
        expires = booking.get("hold_expires_at")
        return expires is not None and datetime.fromisoformat(expires) <= self.clock()

    def _is_free(self, hall_id, day):
        """True if no confirmed booking or unexpired hold takes this hall on ``day``."""
        rows = self.conn.execute(
            "SELECT status, hold_expires_at FROM bookings"
            " WHERE hall_id = ? AND event_date = ? AND status IN ('hold', 'confirmed')",
            (hall_id, day.isoformat()),
        ).fetchall()
        return all(r["status"] == "hold" and self._hold_expired(dict(r)) for r in rows)

    def _hall(self, hall_id):
        """Return one hall with its venue's name, state and tax rate, or raise ``BookingError``."""
        row = self.conn.execute(
            "SELECT h.*, v.name AS venue_name, v.state AS state, v.tax_rate AS tax_rate"
            " FROM halls h JOIN venues v ON v.id = h.venue_id WHERE h.id = ?", (hall_id,),
        ).fetchone()
        if row is None:
            raise BookingError(f"No hall with id {hall_id}.")
        return self._hall_dict(row)

    def _halls(self, venue_id):
        """Return every hall of a venue, in id order."""
        rows = self.conn.execute("SELECT * FROM halls WHERE venue_id = ? ORDER BY id", (venue_id,))
        return [self._hall_dict(r) for r in rows]

    @staticmethod
    def _hall_dict(row):
        """Convert a hall row to a dict: ``id`` becomes ``hall_id``, amenities are decoded from JSON."""
        h = dict(row)
        h["hall_id"] = h.pop("id")
        h["amenities"] = json.loads(h["amenities"])
        return h

    def _package(self, package_id):
        """Return one package, or raise ``BookingError``."""
        row = self.conn.execute("SELECT * FROM packages WHERE id = ?", (package_id,)).fetchone()
        if row is None:
            raise BookingError(f"No package with id {package_id}.")
        return self._package_dict(row)

    def _packages(self, venue_id):
        """Return every package of a venue, in id order."""
        rows = self.conn.execute("SELECT * FROM packages WHERE venue_id = ? ORDER BY id", (venue_id,))
        return [self._package_dict(r) for r in rows]

    @staticmethod
    def _package_dict(row):
        """Convert a package row: ``id`` becomes ``package_id``, dietary options are decoded."""
        p = dict(row)
        p["package_id"] = p.pop("id")
        p["dietary_options"] = json.loads(p["dietary_options"])
        return p
