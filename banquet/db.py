"""SQLite storage and sample data.

All venues are **fictional**. There are two groups:

* The original 10 hand-written venues around New York City (:data:`VENUES`).
* Four generated venues in every state and DC, two in each of two sample cities
  (:func:`generated_venues`),
  built deterministically from :mod:`banquet.states`, so a search in any state has
  results and every run of the app sees the same data.

Each venue stores its ``state`` and the ``tax_rate`` used in quotes. Seed bookings are
placed relative to today, so the demo always has some dates that are already taken.

Database file: :data:`DEFAULT_DB_PATH` (``data/banquet-v2.db``). The file name changed
when the ``state`` and ``tax_rate`` columns were added, so an older ``data/banquet.db``
is left untouched instead of being migrated in place or deleted (CLAUDE.md: never
delete files). Every path goes through :func:`banquet.paths.confine`.
"""

import json
import sqlite3
from datetime import date, timedelta

from banquet.paths import DATA_DIR, confine
from banquet.states import STATES

DEFAULT_DB_PATH = DATA_DIR / "banquet-v2.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    neighborhood  TEXT NOT NULL,
    city          TEXT NOT NULL,
    state         TEXT NOT NULL,      -- two-letter code, see banquet/states.py
    address       TEXT NOT NULL,
    style         TEXT NOT NULL,
    description   TEXT NOT NULL,
    rating        REAL NOT NULL,
    tax_rate      REAL NOT NULL       -- sales tax applied to the whole bill in quotes
);
CREATE INDEX IF NOT EXISTS idx_venues_state ON venues(state);

CREATE TABLE IF NOT EXISTS halls (
    id                 INTEGER PRIMARY KEY,
    venue_id           INTEGER NOT NULL REFERENCES venues(id),
    name               TEXT NOT NULL,
    min_guests         INTEGER NOT NULL,
    seated_capacity    INTEGER NOT NULL,
    standing_capacity  INTEGER NOT NULL,
    rental_fee         REAL NOT NULL,
    amenities          TEXT NOT NULL   -- JSON list
);

CREATE TABLE IF NOT EXISTS packages (
    id               INTEGER PRIMARY KEY,
    venue_id         INTEGER NOT NULL REFERENCES venues(id),
    name             TEXT NOT NULL,
    cuisine          TEXT NOT NULL,
    per_guest_price  REAL NOT NULL,
    includes         TEXT NOT NULL,
    dietary_options  TEXT NOT NULL   -- JSON list
);

CREATE TABLE IF NOT EXISTS bookings (
    id               INTEGER PRIMARY KEY,
    hall_id          INTEGER NOT NULL REFERENCES halls(id),
    package_id       INTEGER REFERENCES packages(id),
    event_date       TEXT NOT NULL,
    guest_count      INTEGER NOT NULL,
    event_type       TEXT NOT NULL,
    customer_name    TEXT NOT NULL,
    customer_email   TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('hold', 'confirmed', 'cancelled')),
    hold_expires_at  TEXT,
    quote_total      REAL NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bookings_hall_date ON bookings(hall_id, event_date);
"""

# (name, neighborhood, city, address, style, description, rating, halls, packages)
# halls:    (name, min, seated, standing, rental_fee, amenities)
# packages: (name, cuisine, per_guest, includes, dietary)
VENUES = [
    (
        "Larkspur Hall", "Midtown", "New York", "412 W 44th St, New York, NY",
        "classic ballroom",
        "Gilded 1920s ballroom with crystal chandeliers and a sweeping mezzanine.",
        4.8,
        [
            ("Grand Ballroom", 150, 400, 550, 12000, ["dance floor", "stage", "AV system", "bridal suite", "coat check"]),
            ("Mezzanine Salon", 40, 90, 140, 4500, ["AV system", "private bar", "balcony view"]),
        ],
        [
            ("Classic Plated", "American", 165, "3-course plated dinner, 4-hour open bar, cake cutting", ["vegetarian", "gluten-free"]),
            ("Black Tie", "French", 235, "Cocktail hour, 5-course tasting, premium open bar, champagne toast", ["vegetarian", "gluten-free", "vegan"]),
        ],
    ),
    (
        "The Ironbridge Loft", "DUMBO", "Brooklyn", "88 Water St, Brooklyn, NY",
        "industrial loft",
        "Exposed brick and steel loft with floor-to-ceiling windows facing the bridge.",
        4.7,
        [
            ("Main Loft", 60, 180, 280, 7500, ["skyline view", "dance floor", "AV system", "freight elevator"]),
            ("Rooftop Deck", 30, 80, 150, 5000, ["skyline view", "heaters", "tent option"]),
        ],
        [
            ("Family Style", "Italian", 120, "Shared platters, beer and wine bar, dessert station", ["vegetarian", "gluten-free"]),
            ("Chef's Stations", "New American", 150, "Five chef-attended stations, full open bar", ["vegetarian", "vegan", "gluten-free"]),
        ],
    ),
    (
        "Marigold Manor", "Jamaica Estates", "Queens", "17-30 Hillside Ave, Queens, NY",
        "banquet hall",
        "Large-format banquet hall built for weddings, sangeets and big family celebrations.",
        4.6,
        [
            ("Royal Hall", 200, 600, 800, 9000, ["dance floor", "stage", "LED wall", "bridal suite", "valet parking", "baraat-friendly entrance"]),
            ("Garden Room", 50, 150, 200, 3500, ["garden access", "dance floor", "valet parking"]),
        ],
        [
            ("Royal Feast", "Indian", 95, "Live chaat counter, 3 curries, tandoor, biryani, dessert bar", ["vegetarian", "jain", "halal"]),
            ("Pan-Asian", "Asian fusion", 110, "Dim sum station, wok station, sushi display", ["vegetarian", "halal"]),
        ],
    ),
    (
        "Bayview Terrace", "Bay Ridge", "Brooklyn", "9100 Shore Rd, Brooklyn, NY",
        "waterfront",
        "Waterfront terrace and ballroom with Verrazzano Bridge sunsets.",
        4.5,
        [
            ("Terrace Ballroom", 100, 300, 400, 8000, ["water view", "dance floor", "outdoor ceremony lawn", "parking"]),
        ],
        [
            ("Seaside Classic", "Seafood", 140, "Raw bar cocktail hour, plated surf & turf, open bar", ["gluten-free", "vegetarian"]),
            ("Mediterranean", "Greek", 125, "Mezze cocktail hour, family-style mains, open bar", ["vegetarian", "vegan", "halal"]),
        ],
    ),
    (
        "The Copperleaf", "Flatiron", "New York", "29 W 21st St, New York, NY",
        "modern restaurant buyout",
        "Chef-driven restaurant with a private dining room and full buyout option.",
        4.9,
        [
            ("Private Dining Room", 12, 40, 60, 1500, ["private entrance", "sommelier", "AV screen"]),
            ("Full Buyout", 50, 120, 180, 9500, ["open kitchen", "sommelier", "AV system"]),
        ],
        [
            ("Chef's Tasting", "Contemporary", 210, "7-course tasting with wine pairing", ["vegetarian", "pescatarian"]),
            ("Reception", "Contemporary", 145, "Passed canapés, two stations, open bar", ["vegetarian", "vegan", "gluten-free"]),
        ],
    ),
    (
        "Stonegate Estate", "Glen Cove", "Long Island", "5 Stonegate Rd, Glen Cove, NY",
        "estate & gardens",
        "Gilded Age estate with formal gardens, a glass conservatory and overnight suites.",
        4.8,
        [
            ("Conservatory", 80, 220, 300, 11000, ["garden ceremony site", "dance floor", "bridal suite", "overnight suites", "parking"]),
            ("Library Room", 20, 60, 90, 3000, ["fireplace", "private bar"]),
        ],
        [
            ("Estate Dinner", "American", 185, "Cocktail hour, plated dinner, premium bar, late-night snack", ["vegetarian", "gluten-free", "vegan"]),
        ],
    ),
    (
        "Harbor Lantern Room", "Jersey City", "New Jersey", "1 Harborside Pl, Jersey City, NJ",
        "waterfront",
        "Glass-walled event space on the Hudson with direct Manhattan skyline views.",
        4.4,
        [
            ("Lantern Hall", 75, 250, 350, 6500, ["skyline view", "dance floor", "AV system", "parking", "PATH nearby"]),
        ],
        [
            ("Skyline Buffet", "International", 98, "Carving station, pasta station, salads, beer & wine bar", ["vegetarian", "halal", "gluten-free"]),
            ("Skyline Plated", "American", 130, "Plated 3-course, open bar, cocktail hour", ["vegetarian", "gluten-free"]),
        ],
    ),
    (
        "Willowmere Pavilion", "Riverdale", "Bronx", "4600 Palisade Ave, Bronx, NY",
        "garden pavilion",
        "Open-air pavilion in a wooded garden, enclosed and heated for winter events.",
        4.5,
        [
            ("Pavilion", 60, 200, 260, 5500, ["garden ceremony site", "heaters", "dance floor", "parking"]),
        ],
        [
            ("Garden Party", "Farm-to-table", 115, "Grazing tables, family-style dinner, beer & wine", ["vegetarian", "vegan", "gluten-free"]),
            ("Kosher Celebration", "Kosher", 160, "Glatt kosher plated dinner under supervision, open bar", ["kosher", "vegetarian"]),
        ],
    ),
    (
        "The Gilded Pear", "Upper West Side", "New York", "250 W 72nd St, New York, NY",
        "intimate salon",
        "Townhouse salon with parlor floor and garden, ideal for showers and rehearsal dinners.",
        4.7,
        [
            ("Parlor Floor", 15, 50, 80, 2500, ["fireplace", "piano", "garden access"]),
        ],
        [
            ("Afternoon Tea", "English", 75, "Tea service, sandwiches, scones, pastries", ["vegetarian", "gluten-free"]),
            ("Rehearsal Dinner", "Italian", 135, "Family-style dinner, wine pairing", ["vegetarian", "gluten-free"]),
        ],
    ),
    (
        "Brightwater Club", "Astoria", "Queens", "31-10 Ditmars Blvd, Queens, NY",
        "banquet hall",
        "Family-run banquet hall known for Greek and Mediterranean celebrations.",
        4.3,
        [
            ("Aegean Ballroom", 120, 350, 450, 5000, ["dance floor", "stage", "AV system", "parking"]),
            ("Olive Room", 30, 100, 130, 1800, ["private bar", "AV screen"]),
        ],
        [
            ("Greek Feast", "Greek", 89, "Meze, lamb or fish entrée, baklava, open bar", ["vegetarian", "gluten-free", "halal"]),
            ("Classic Banquet", "American", 79, "Buffet dinner, soft drinks, cake cutting", ["vegetarian"]),
        ],
    ),
]

# (venue index, hall index, days from today, guests, event_type)
SEED_BOOKINGS = [
    (0, 0, 30, 300, "wedding"),
    (0, 0, 44, 250, "gala"),
    (1, 0, 30, 150, "corporate party"),
    (2, 0, 37, 450, "wedding"),
    (2, 0, 51, 500, "wedding"),
    (3, 0, 30, 220, "wedding"),
    (5, 0, 58, 180, "wedding"),
    (6, 0, 44, 200, "corporate party"),
    (9, 0, 37, 300, "anniversary"),
]


#: State and tax rate for the original venues. Rates are approximate combined state +
#: local rates: New York City 8.875%, Nassau County (Glen Cove) 8.625%, New Jersey 6.625%.
ORIGINAL_LOCATION = {
    "Stonegate Estate": ("NY", 0.08625),
    "Harbor Lantern Room": ("NJ", 0.06625),
}
NYC = ("NY", 0.08875)

# ----------------------------------------------------------------- generated venues
# Building blocks for the fictional venues in every state. Venue k (0, 1, 2, ...) takes
# word k % 22 and kind (k // 22 + k) % 10. Venues k and k + 22m share a word, and their
# kinds differ by 23m mod 10 = 3m mod 10, which is non-zero for m < 10. So the pair
# (word, kind) is unique for k < 220, and no two of the 204 generated venues share a name.
NAME_WORDS = ["Magnolia", "Juniper", "Silver Oak", "Harvest", "Cedar", "Lantern", "Willow",
              "Golden Hour", "Copper Kettle", "Bluebird", "Ironwood", "Wildflower",
              "Stone Mill", "Riverbend", "Highland", "Laurel", "Saltbox", "Briarwood",
              "Larkfield", "Sycamore", "Hearthstone", "Meadowlark"]
VENUES_PER_CITY = 2
# (name suffix, style, size, amenities, description)
KINDS = [
    ("Ballroom", "classic ballroom", "large", ["dance floor", "stage", "AV system", "bridal suite", "coat check"],
     "Chandeliered ballroom with a sprung dance floor and a separate reception salon."),
    ("Hall", "banquet hall", "large", ["dance floor", "stage", "AV system", "parking"],
     "Large-format banquet hall built for weddings and big family celebrations."),
    ("Loft", "industrial loft", "medium", ["skyline view", "dance floor", "AV system", "freight elevator"],
     "Converted warehouse loft with exposed brick, steel beams and tall windows."),
    ("Pavilion", "garden pavilion", "medium", ["garden ceremony site", "heaters", "dance floor", "parking"],
     "Open-air pavilion in landscaped gardens, enclosed and heated for cooler months."),
    ("Estate", "estate & gardens", "large", ["garden ceremony site", "bridal suite", "overnight suites", "parking"],
     "Historic-style estate with formal gardens and a glass-walled reception room."),
    ("Club", "banquet hall", "large", ["dance floor", "private bar", "AV system", "parking"],
     "Members'-club ballroom available for private events, with a full-service bar."),
    ("Gardens", "garden venue", "medium", ["garden access", "outdoor ceremony lawn", "tent option", "parking"],
     "Botanical garden venue with a ceremony lawn and a tented reception terrace."),
    ("House", "intimate salon", "small", ["fireplace", "piano", "garden access"],
     "Restored townhouse parlor for showers, rehearsal dinners and small receptions."),
    ("Terrace", "rooftop terrace", "medium", ["skyline view", "heaters", "AV system", "private bar"],
     "Rooftop terrace and glass-enclosed lounge with views over the city."),
    ("Barn", "rustic barn", "medium", ["dance floor", "outdoor ceremony lawn", "string lights", "parking"],
     "Restored timber barn with string lights, a ceremony meadow and a fire pit."),
]
NEIGHBORHOODS = ["Downtown", "Old Town", "Riverside", "Uptown", "Arts District", "Midtown"]
STREETS = ["Main", "Oak", "Market", "Elm", "River", "Park", "Lake", "Union"]
CUISINES = ["American", "Italian", "Mexican", "Southern", "BBQ", "Indian", "Mediterranean",
            "Chinese", "Seafood", "Kosher", "Farm-to-table", "Korean", "Caribbean", "French"]
#: A regional cuisine used for the first package of the first venue in these states.
REGIONAL = {"TX": "Tex-Mex", "LA": "Cajun & Creole", "NM": "New Mexican", "HI": "Hawaiian",
            "ME": "New England seafood", "MA": "New England seafood", "TN": "Southern BBQ",
            "MD": "Chesapeake seafood", "CA": "Californian", "WI": "Midwestern"}
DIETARY = [["vegetarian", "gluten-free"], ["vegetarian", "vegan", "gluten-free"], ["vegetarian", "halal"],
           ["vegetarian", "kosher"], ["vegetarian", "vegan", "halal", "gluten-free"], ["vegetarian"]]


def _halls_for(size, k):
    """Return the halls for a generated venue of a given size, varied by ``k``.

    Args:
        size: ``"large"``, ``"medium"`` or ``"small"``.
        k: The venue's index, used to vary fees and capacities deterministically.

    Returns:
        A list of ``(name, min_guests, seated, standing, rental_fee, amenities)``, the
        same shape as the hand-written :data:`VENUES`. Amenities are filled in by the caller.
    """
    fee = 2000 + (k * 1370) % 8000 // 500 * 500           # $2,000-$9,500 in $500 steps
    if size == "large":
        seated = 300 + (k * 30) % 180                      # 300-450 seated
        return [("Grand Hall", 100, seated, int(seated * 1.3), fee + 3000),
                ("Salon", 30, 90, 130, max(1500, fee // 2))]
    if size == "medium":
        seated = 160 + (k * 20) % 100                      # 160-240 seated
        return [("Main Room", 60, seated, int(seated * 1.4), fee)]
    return [("Parlor", 15, 60, 90, max(1500, fee // 3))]


def generated_venues():
    """Build fictional venues for every state and DC: ``VENUES_PER_CITY`` in each sample city.

    Everything is derived from the venue's index ``k``, so the data is identical on
    every run, and tests can rely on it. Addresses are marked "(fictional)".

    Returns:
        A list of tuples ``(name, neighborhood, city, state, address, style, description,
        rating, tax_rate, halls, packages)`` in the same layout :func:`_seed` inserts.
    """
    out, k = [], 0
    for code, (_, tax_rate, cities) in STATES.items():
        for i, city in enumerate(cities * VENUES_PER_CITY):
            word = NAME_WORDS[k % len(NAME_WORDS)]
            suffix, style, size, amenities, description = KINDS[(k // len(NAME_WORDS) + k) % len(KINDS)]
            name = f"The {word} {suffix}"
            hood = NEIGHBORHOODS[k % len(NEIGHBORHOODS)]
            address = f"{100 + (k * 37) % 800} {STREETS[k % len(STREETS)]} St (fictional), {city}, {code}"
            rating = round(4.1 + (k * 17 % 80) / 100, 1)   # 4.1-4.9
            halls = [h + (amenities,) for h in _halls_for(size, k)]
            first = REGIONAL.get(code) if i == 0 else None
            c1 = first or CUISINES[k % len(CUISINES)]
            c2 = CUISINES[(k + 5) % len(CUISINES)]
            base = 70 + (k * 13) % 90                      # $70-$159 per guest
            packages = [
                ("Signature Dinner", c1, base + 25, "Cocktail hour, plated dinner, open bar",
                 DIETARY[k % len(DIETARY)]),
                ("Celebration Buffet", c2, base, "Buffet dinner, beer and wine bar, dessert table",
                 DIETARY[(k + 2) % len(DIETARY)]),
            ]
            out.append((name, hood, city, code, address, style, description, rating, tax_rate,
                        halls, packages))
            k += 1
    return out


def all_venues():
    """Every venue to seed: the original 10 (with their state and tax) plus the generated ones.

    Returns:
        A list in the tuple layout documented in :func:`generated_venues`.
    """
    originals = []
    for name, hood, city, address, style, desc, rating, halls, packages in VENUES:
        state, tax = ORIGINAL_LOCATION.get(name, NYC)
        originals.append((name, hood, city, state, address, style, desc, rating, tax, halls, packages))
    return originals + generated_venues()


# ----------------------------------------------------------------- connection and seeding

def connect(db_path=DEFAULT_DB_PATH):
    """Open (creating if needed) the SQLite database.

    Args:
        db_path: A path inside the project, or ``":memory:"`` for a throwaway database
            (used by the tests, so they never write to the system temp folder).

    Returns:
        A :class:`sqlite3.Connection` with ``Row`` results and foreign keys enabled. It
        may be shared across threads; :class:`~banquet.services.BanquetService` serializes
        writes with a lock.

    Raises:
        banquet.paths.PathError: If ``db_path`` is outside the project folder.

    Side effects:
        Creates the parent folder of a file database.
    """
    path = confine(db_path)
    if path != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn, seed=True, today=None):
    """Create the tables, and seed sample data if the database is empty.

    Args:
        conn: An open connection from :func:`connect`.
        seed: Whether to insert the sample venues and bookings.
        today: The date seed bookings are placed relative to (default: today). Tests
            pass a fixed date so "booked in 30 days" is predictable.

    Side effects:
        Creates tables and indexes, inserts rows, commits.
    """
    conn.executescript(SCHEMA)
    already_seeded = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0] > 0
    if seed and not already_seeded:
        _seed(conn, today or date.today())
    conn.commit()


def _seed(conn, today):
    """Insert every venue, hall and package, then the seed bookings.

    Seed bookings refer to the original venues by their position in :data:`VENUES`,
    which is why the originals are inserted first.

    Args:
        conn: An open connection.
        today: Base date for the seed bookings.
    """
    hall_ids = []
    for name, hood, city, state, address, style, desc, rating, tax, halls, packages in all_venues():
        venue_id = conn.execute(
            "INSERT INTO venues (name, neighborhood, city, state, address, style, description, rating,"
            " tax_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, hood, city, state, address, style, desc, rating, tax),
        ).lastrowid
        venue_halls = []
        for hall_name, min_g, seated, standing, fee, amenities in halls:
            venue_halls.append(conn.execute(
                "INSERT INTO halls (venue_id, name, min_guests, seated_capacity, standing_capacity,"
                " rental_fee, amenities) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (venue_id, hall_name, min_g, seated, standing, fee, json.dumps(amenities)),
            ).lastrowid)
        hall_ids.append(venue_halls)
        for pkg_name, cuisine, price, includes, dietary in packages:
            conn.execute(
                "INSERT INTO packages (venue_id, name, cuisine, per_guest_price, includes, dietary_options)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (venue_id, pkg_name, cuisine, price, includes, json.dumps(dietary)),
            )

    for v_idx, h_idx, days_out, guests, event_type in SEED_BOOKINGS:
        conn.execute(
            "INSERT INTO bookings (hall_id, event_date, guest_count, event_type, customer_name,"
            " customer_email, status, quote_total, created_at)"
            " VALUES (?, ?, ?, ?, 'Existing client', 'client@example.com', 'confirmed', 0, ?)",
            (hall_ids[v_idx][h_idx], (today + timedelta(days=days_out)).isoformat(),
             guests, event_type, today.isoformat()),
        )
