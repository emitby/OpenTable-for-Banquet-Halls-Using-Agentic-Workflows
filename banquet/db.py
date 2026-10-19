"""SQLite storage and sample data.

All venues are fictional. Seed bookings are placed relative to today so the
demo always has some dates that are already taken.
"""

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "banquet.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    neighborhood  TEXT NOT NULL,
    city          TEXT NOT NULL,
    address       TEXT NOT NULL,
    style         TEXT NOT NULL,
    description   TEXT NOT NULL,
    rating        REAL NOT NULL
);

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


def connect(db_path=DEFAULT_DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn, seed=True, today=None):
    conn.executescript(SCHEMA)
    already_seeded = conn.execute("SELECT COUNT(*) FROM venues").fetchone()[0] > 0
    if seed and not already_seeded:
        _seed(conn, today or date.today())
    conn.commit()


def _seed(conn, today):
    hall_ids = []
    for name, hood, city, address, style, desc, rating, halls, packages in VENUES:
        venue_id = conn.execute(
            "INSERT INTO venues (name, neighborhood, city, address, style, description, rating)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, hood, city, address, style, desc, rating),
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
