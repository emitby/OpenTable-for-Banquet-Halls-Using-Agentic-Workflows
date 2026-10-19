"""Booking domain logic: search, availability, quotes and holds.

This module knows nothing about agents. The agents call it through tools, and
the web API calls it directly for the human-only actions (confirm, cancel).
"""

import json
import threading
from datetime import date, datetime, timedelta

# Pricing rules
WEEKEND_PREMIUM = 0.25          # Friday and Saturday rental premium
PEAK_MONTHS = {5, 6, 9, 10, 12}
PEAK_PREMIUM = 0.15             # applied to rental in peak months
SERVICE_CHARGE = 0.20           # on food & beverage
SALES_TAX = 0.08875             # NYC rate, applied to the whole bill
DEPOSIT_RATE = 0.25
HOLD_HOURS = 72
MAX_BOOKING_DAYS_OUT = 730


class BookingError(ValueError):
    """A request the booking rules don't allow. The message is safe to show users."""


def _parse_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise BookingError(f"Invalid date {value!r}; use YYYY-MM-DD.") from None


class BanquetService:
    def __init__(self, conn, clock=datetime.now):
        self.conn = conn
        self.clock = clock
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ reads

    def search_venues(self, guest_count=None, city=None, style=None, cuisine=None,
                      dietary=None, amenities=None, max_price_per_guest=None,
                      event_date=None):
        """Return venues with at least one hall that fits every given filter."""
        event_date = _parse_date(event_date) if event_date else None
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
                "location": f"{v['neighborhood']}, {v['city']}",
                "style": v["style"],
                "rating": v["rating"],
                "halls": [{k: h[k] for k in ("hall_id", "name", "min_guests", "seated_capacity",
                                              "rental_fee", "available") if k in h} for h in halls],
                "packages": [{k: p[k] for k in ("package_id", "name", "cuisine", "per_guest_price")}
                             for p in packages],
            })
        return results

    def get_venue(self, venue_id):
        v = self.conn.execute("SELECT * FROM venues WHERE id = ?", (venue_id,)).fetchone()
        if v is None:
            raise BookingError(f"No venue with id {venue_id}.")
        return {
            "venue_id": v["id"], "name": v["name"], "neighborhood": v["neighborhood"],
            "city": v["city"], "address": v["address"], "style": v["style"],
            "description": v["description"], "rating": v["rating"],
            "halls": self._halls(venue_id), "packages": self._packages(venue_id),
        }

    def check_availability(self, hall_id, event_date, alternatives=3):
        """Is the hall free that day? If not, suggest the nearest free dates."""
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

    def build_quote(self, hall_id, package_id, event_date, guest_count):
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
        tax = subtotal * SALES_TAX
        total = subtotal + tax

        lines = [{"item": f"{hall['name']} rental", "amount": round(rental, 2)}]
        lines += [{"item": label, "amount": round(amount, 2)} for label, amount in adjustments]
        lines.append({
            "item": f"{package['name']} ({billed_guests} × ${package['per_guest_price']:.2f})",
            "amount": round(food_beverage, 2),
        })
        lines.append({"item": f"Service charge ({SERVICE_CHARGE:.0%})", "amount": round(service, 2)})
        lines.append({"item": f"Sales tax ({SALES_TAX:.3%})", "amount": round(tax, 2)})

        notes = []
        if billed_guests > guest_count:
            notes.append(f"{hall['name']} has a {hall['min_guests']}-guest minimum; "
                         f"food & beverage is billed for {billed_guests}.")
        return {
            "venue": hall["venue_name"], "hall": hall["name"], "package": package["name"],
            "date": day.isoformat(), "guest_count": guest_count,
            "line_items": lines, "total": round(total, 2),
            "per_guest": round(total / guest_count, 2),
            "deposit_due": round(total * DEPOSIT_RATE, 2), "notes": notes,
        }

    def get_booking(self, booking_id):
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

    def place_hold(self, hall_id, package_id, event_date, guest_count, event_type,
                   customer_name, customer_email):
        """Reserve a date for HOLD_HOURS. A hold is free and non-binding."""
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

    def confirm_booking(self, booking_id, customer_email):
        """Turn a hold into a confirmed booking. Only the customer can do this."""
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

    def cancel_booking(self, booking_id, customer_email):
        with self._lock:
            booking = self._owned_booking(booking_id, customer_email)
            if booking["status"] == "cancelled":
                raise BookingError(f"Booking {booking_id} is already cancelled.")
            self.conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
            self.conn.commit()
        return self.get_booking(booking_id)

    # ---------------------------------------------------------------- helpers

    def _owned_booking(self, booking_id, customer_email):
        booking = self.get_booking(booking_id)
        if booking["customer_email"] != (customer_email or "").strip().lower():
            raise BookingError("That email doesn't match the booking.")
        return booking

    def _hold_expired(self, booking):
        expires = booking.get("hold_expires_at")
        return expires is not None and datetime.fromisoformat(expires) <= self.clock()

    def _is_free(self, hall_id, day):
        rows = self.conn.execute(
            "SELECT status, hold_expires_at FROM bookings"
            " WHERE hall_id = ? AND event_date = ? AND status IN ('hold', 'confirmed')",
            (hall_id, day.isoformat()),
        ).fetchall()
        return all(r["status"] == "hold" and self._hold_expired(dict(r)) for r in rows)

    def _hall(self, hall_id):
        row = self.conn.execute(
            "SELECT h.*, v.name AS venue_name FROM halls h JOIN venues v ON v.id = h.venue_id"
            " WHERE h.id = ?", (hall_id,),
        ).fetchone()
        if row is None:
            raise BookingError(f"No hall with id {hall_id}.")
        return self._hall_dict(row)

    def _halls(self, venue_id):
        rows = self.conn.execute("SELECT * FROM halls WHERE venue_id = ? ORDER BY id", (venue_id,))
        return [self._hall_dict(r) for r in rows]

    @staticmethod
    def _hall_dict(row):
        h = dict(row)
        h["hall_id"] = h.pop("id")
        h["amenities"] = json.loads(h["amenities"])
        return h

    def _package(self, package_id):
        row = self.conn.execute("SELECT * FROM packages WHERE id = ?", (package_id,)).fetchone()
        if row is None:
            raise BookingError(f"No package with id {package_id}.")
        return self._package_dict(row)

    def _packages(self, venue_id):
        rows = self.conn.execute("SELECT * FROM packages WHERE venue_id = ? ORDER BY id", (venue_id,))
        return [self._package_dict(r) for r in rows]

    @staticmethod
    def _package_dict(row):
        p = dict(row)
        p["package_id"] = p.pop("id")
        p["dietary_options"] = json.loads(p["dietary_options"])
        return p
