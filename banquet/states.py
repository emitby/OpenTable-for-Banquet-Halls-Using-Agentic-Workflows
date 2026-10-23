"""US states (plus DC): names, codes, sample cities and approximate sales-tax rates.

Used for two things:

* **Search by state.** :func:`normalize_state` turns "TX", "tx", "Texas" or
  "texas" into the code ``"TX"`` so customers and agents can name a state either way.
* **Fictional venues everywhere.** ``db.py`` builds sample venues in the cities listed
  here, so a search in any state returns results.

Tax rates are each state's **base** sales-tax rate, rounded and approximate. Real bills
also include local (county/city) taxes and, in some places, special meals or
hospitality taxes, which this prototype does not model. That's stated in every quote
note, the docs and the PDF guide. States with no general sales tax use 0.
"""

#: code -> (name, approximate state base sales-tax rate, two sample cities)
STATES = {
    "AL": ("Alabama", 0.04, ("Birmingham", "Mobile")),
    "AK": ("Alaska", 0.0, ("Anchorage", "Fairbanks")),
    "AZ": ("Arizona", 0.056, ("Phoenix", "Tucson")),
    "AR": ("Arkansas", 0.065, ("Little Rock", "Fayetteville")),
    "CA": ("California", 0.0725, ("Los Angeles", "San Francisco")),
    "CO": ("Colorado", 0.029, ("Denver", "Colorado Springs")),
    "CT": ("Connecticut", 0.0635, ("Hartford", "New Haven")),
    "DE": ("Delaware", 0.0, ("Wilmington", "Dover")),
    "DC": ("District of Columbia", 0.06, ("Washington", "Washington")),
    "FL": ("Florida", 0.06, ("Miami", "Orlando")),
    "GA": ("Georgia", 0.04, ("Atlanta", "Savannah")),
    "HI": ("Hawaii", 0.04, ("Honolulu", "Kahului")),
    "ID": ("Idaho", 0.06, ("Boise", "Coeur d'Alene")),
    "IL": ("Illinois", 0.0625, ("Chicago", "Springfield")),
    "IN": ("Indiana", 0.07, ("Indianapolis", "Fort Wayne")),
    "IA": ("Iowa", 0.06, ("Des Moines", "Cedar Rapids")),
    "KS": ("Kansas", 0.065, ("Wichita", "Overland Park")),
    "KY": ("Kentucky", 0.06, ("Louisville", "Lexington")),
    "LA": ("Louisiana", 0.05, ("New Orleans", "Baton Rouge")),
    "ME": ("Maine", 0.055, ("Portland", "Bangor")),
    "MD": ("Maryland", 0.06, ("Baltimore", "Annapolis")),
    "MA": ("Massachusetts", 0.0625, ("Boston", "Worcester")),
    "MI": ("Michigan", 0.06, ("Detroit", "Grand Rapids")),
    "MN": ("Minnesota", 0.06875, ("Minneapolis", "Duluth")),
    "MS": ("Mississippi", 0.07, ("Jackson", "Biloxi")),
    "MO": ("Missouri", 0.04225, ("St. Louis", "Kansas City")),
    "MT": ("Montana", 0.0, ("Billings", "Missoula")),
    "NE": ("Nebraska", 0.055, ("Omaha", "Lincoln")),
    "NV": ("Nevada", 0.0685, ("Las Vegas", "Reno")),
    "NH": ("New Hampshire", 0.0, ("Manchester", "Portsmouth")),
    "NJ": ("New Jersey", 0.06625, ("Newark", "Princeton")),
    "NM": ("New Mexico", 0.04875, ("Albuquerque", "Santa Fe")),
    "NY": ("New York", 0.04, ("Buffalo", "Albany")),
    "NC": ("North Carolina", 0.0475, ("Charlotte", "Asheville")),
    "ND": ("North Dakota", 0.05, ("Fargo", "Bismarck")),
    "OH": ("Ohio", 0.0575, ("Columbus", "Cleveland")),
    "OK": ("Oklahoma", 0.045, ("Oklahoma City", "Tulsa")),
    "OR": ("Oregon", 0.0, ("Portland", "Bend")),
    "PA": ("Pennsylvania", 0.06, ("Philadelphia", "Pittsburgh")),
    "RI": ("Rhode Island", 0.07, ("Providence", "Newport")),
    "SC": ("South Carolina", 0.06, ("Charleston", "Greenville")),
    "SD": ("South Dakota", 0.042, ("Sioux Falls", "Rapid City")),
    "TN": ("Tennessee", 0.07, ("Nashville", "Memphis")),
    "TX": ("Texas", 0.0625, ("Austin", "Houston")),
    "UT": ("Utah", 0.061, ("Salt Lake City", "Park City")),
    "VT": ("Vermont", 0.06, ("Burlington", "Stowe")),
    "VA": ("Virginia", 0.053, ("Richmond", "Virginia Beach")),
    "WA": ("Washington", 0.065, ("Seattle", "Spokane")),
    "WV": ("West Virginia", 0.06, ("Charleston", "Morgantown")),
    "WI": ("Wisconsin", 0.05, ("Milwaukee", "Madison")),
    "WY": ("Wyoming", 0.04, ("Cheyenne", "Jackson")),
}

_BY_NAME = {name.lower(): code for code, (name, _, _) in STATES.items()}


def normalize_state(value):
    """Turn a state code or name into its two-letter code.

    Accepts any case, surrounding spaces, periods and commas ("Washington, D.C."), and a
    trailing "state" ("Washington state", "New York State"). "Washington DC" and "D.C."
    mean DC; "Washington" alone means the state (WA), as in an address.

    Args:
        value: e.g. ``"TX"``, ``"texas"``, ``" New York "``.

    Returns:
        The code, e.g. ``"TX"``.

    Raises:
        ValueError: If the value isn't a US state or DC. The message lists how to
            write it, so an agent receiving it as a tool error can correct itself.
    """
    text = " ".join(str(value).replace(".", "").replace(",", " ").split()).lower()
    if text.endswith(" state") and text[:-6] in _BY_NAME:
        text = text[:-6]
    if text.upper() in STATES:
        return text.upper()
    if text in ("washington dc", "dc", "district of columbia"):
        return "DC"
    if text in _BY_NAME:
        return _BY_NAME[text]
    raise ValueError(f"Unknown state {value!r}. Use a US state name or two-letter code, e.g. 'Texas' or 'TX'.")


def state_name(code):
    """Return the full name for a state code, e.g. ``"TX"`` -> ``"Texas"``."""
    return STATES[code][0]


def state_tax_rate(code):
    """Return the approximate state base sales-tax rate for a code (see the module notes)."""
    return STATES[code][1]
