"""Chat with the Concierge in the terminal.

    .\\run.ps1 cli               # chat
    .\\run.ps1 cli --verbose     # also show what each agent is doing and the tokens used

Commands while chatting: ``/holds``, ``/confirm <id>`` (asks y/N first), ``/quit``.
"""

import argparse

import anthropic

from banquet.agents import ConciergeSession
from banquet.db import DEFAULT_DB_PATH, connect, init_db
from banquet.services import BanquetService, BookingError


def main(argv=None):
    """Run the chat loop until ``/quit`` or end of input.

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).

    Side effects:
        Reads from stdin, prints to stdout, and may place or confirm holds in the database.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--verbose", action="store_true", help="print agent activity and token usage")
    args = parser.parse_args(argv)

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
                elif e["type"] == "model_call":
                    u = e["usage"]
                    print(f"  · {e['agent']} model call: {u['input'] + u['cache_read'] + u['cache_write']:,} in"
                          f" / {u['output']:,} out tokens")
                elif e["type"] == "guardrail":
                    print(f"  · guardrail {e['guardrail']}: {e['reason']}")
        print(f"\nconcierge> {result['reply']}\n")


def _confirm(service, session, text):
    """Handle ``/confirm <id>``: only this session's holds, and only after the customer types y.

    Args:
        service: The booking service.
        session: The chat session (its holds are the only ones that can be confirmed here).
        text: The command line the customer typed.
    """
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
