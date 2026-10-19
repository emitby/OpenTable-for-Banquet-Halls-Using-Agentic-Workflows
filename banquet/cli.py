"""Chat with the Concierge in the terminal.

    python -m banquet.cli            # chat
    python -m banquet.cli --verbose  # also show what each agent is doing
"""

import argparse

import anthropic

from banquet.agents import ConciergeSession
from banquet.db import DEFAULT_DB_PATH, connect, init_db
from banquet.services import BanquetService, BookingError


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--verbose", action="store_true", help="print agent activity")
    args = parser.parse_args()

    conn = connect(args.db)
    init_db(conn)
    service = BanquetService(conn)
    session = ConciergeSession(anthropic.Anthropic(), service)

    print("Banquet Concierge. Describe your event. Commands: /holds, /confirm <id>, /quit\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text == "/quit":
            return
        if text == "/holds":
            for b in session.holds():
                print(f"  #{b['booking_id']} {b['venue_name']} · {b['hall_name']} · {b['event_date']}"
                      f" · {b['guest_count']} guests · ${b['quote_total']:,.0f} · {b['status']}")
            continue
        if text.startswith("/confirm"):
            _confirm(service, session, text)
            continue

        result = session.send(text)
        if args.verbose:
            for e in result["events"]:
                if e["type"] == "tool_call":
                    print(f"  · {e['agent']} → {e['tool']}")
        print(f"\nconcierge> {result['reply']}\n")


def _confirm(service, session, text):
    parts = text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        print("  usage: /confirm <hold id>")
        return
    booking_id = int(parts[1])
    hold = next((b for b in session.holds() if b["booking_id"] == booking_id), None)
    if hold is None:
        print("  That isn't one of your holds.")
        return
    answer = input(f"  Confirm and commit to ${hold['quote_total']:,.2f}? [y/N] ")
    if answer.strip().lower() != "y":
        return
    try:
        booking = service.confirm_booking(booking_id, hold["customer_email"])
        print(f"  Booking #{booking_id} is {booking['status']}.")
    except BookingError as exc:
        print(f"  {exc}")


if __name__ == "__main__":
    main()
