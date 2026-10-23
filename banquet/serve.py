"""Start the web app on 127.0.0.1 (use the launcher: ``.\\run.ps1 serve``).

Options:
    --db PATH       database file inside the project (default data/banquet-v2.db)
    --port N        port (default 8000)
    --simulated     use the scripted SimulatedClient instead of Claude: no API key, no
                    cost; it plays the "texas-wedding" scenario script whatever you type

The server binds to 127.0.0.1 only, so it isn't reachable from other machines.
"""

import argparse

import uvicorn

from banquet.api import create_app
from banquet.db import DEFAULT_DB_PATH

HOST = "127.0.0.1"


def main(argv=None):
    """Parse the options and run uvicorn until stopped (Ctrl+C).

    Args:
        argv: Command-line arguments (default: ``sys.argv[1:]``).
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--simulated", action="store_true")
    args = parser.parse_args(argv)
    client = None
    if args.simulated:
        from banquet.harness.simulated import SimulatedClient
        from banquet.harness.scenarios import SCENARIOS
        client = SimulatedClient(SCENARIOS["texas-wedding"])
    uvicorn.run(create_app(args.db, client=client), host=HOST, port=args.port)


if __name__ == "__main__":
    main()
