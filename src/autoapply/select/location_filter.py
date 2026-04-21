"""US-only location filter + NYC bonus.

Used as a hard filter during `select.dedup.filter_hard()` and as a weighted
signal in `select.scorer.final_rank()`. Deterministic — no LLM.

Contract:
  is_us_location(raw) -> bool            # "US, 'Remote', 'Remote US'" → True
  country_from_location(raw) -> str|None # best-effort country canonicalizer
  nyc_bonus(raw) -> float                # 0.15 if NYC-adjacent, else 0.0
"""

from __future__ import annotations

import re


# Ordered — most specific first. Every entry is a (pattern, country_code) pair.
# Country codes follow ISO-3166-1 alpha-2 where practical; "US" is the only
# one that matters for filtering.

_NON_US_COUNTRY_MARKERS: list[tuple[re.Pattern[str], str]] = [
    # Explicit country names (word-bounded)
    (re.compile(r"\bunited\s+kingdom\b|\buk\b|\bengland\b|\bscotland\b|\bwales\b"), "GB"),
    (re.compile(r"\bireland\b|\bdublin\b"), "IE"),
    (re.compile(r"\bcanada\b|\btoronto\b|\bvancouver\b|\bmontreal\b|\bottawa\b|\bcalgary\b"), "CA"),
    (re.compile(r"\bmexico\b|\bmexico\s+city\b|\bcdmx\b"), "MX"),
    (re.compile(r"\bgermany\b|\bberlin\b|\bmunich\b|\bhamburg\b|\bfrankfurt\b"), "DE"),
    (re.compile(r"\bfrance\b|\bparis\b|\blyon\b"), "FR"),
    (re.compile(r"\bspain\b|\bmadrid\b|\bbarcelona\b"), "ES"),
    (re.compile(r"\bitaly\b|\brome\b|\bmilan\b"), "IT"),
    (re.compile(r"\bnetherlands\b|\bamsterdam\b|\brotterdam\b"), "NL"),
    (re.compile(r"\bswitzerland\b|\bzurich\b|\bgeneva\b"), "CH"),
    (re.compile(r"\bsweden\b|\bstockholm\b"), "SE"),
    (re.compile(r"\bnorway\b|\boslo\b"), "NO"),
    (re.compile(r"\bdenmark\b|\bcopenhagen\b"), "DK"),
    (re.compile(r"\bfinland\b|\bhelsinki\b"), "FI"),
    (re.compile(r"\bpoland\b|\bwarsaw\b"), "PL"),
    (re.compile(r"\bportugal\b|\blisbon\b"), "PT"),
    (re.compile(r"\bbelgium\b|\bbrussels\b"), "BE"),
    (re.compile(r"\baustria\b|\bvienna\b"), "AT"),
    (re.compile(r"\bromania\b|\bbucharest\b"), "RO"),
    (re.compile(r"\bczech\b|\bprague\b"), "CZ"),
    (re.compile(r"\bhungary\b|\bbudapest\b"), "HU"),
    (re.compile(r"\bindia\b|\bbangalore\b|\bbengaluru\b|\bhyderabad\b|\bmumbai\b|\bdelhi\b|\bpune\b|\bchennai\b|\bnoida\b|\bgurgaon\b"), "IN"),
    (re.compile(r"\bsingapore\b"), "SG"),
    (re.compile(r"\bhong\s*kong\b"), "HK"),
    (re.compile(r"\bchina\b|\bbeijing\b|\bshanghai\b|\bshenzhen\b|\bguangzhou\b"), "CN"),
    (re.compile(r"\btaiwan\b|\btaipei\b"), "TW"),
    (re.compile(r"\bjapan\b|\btokyo\b|\bosaka\b"), "JP"),
    (re.compile(r"\bsouth\s+korea\b|\bkorea\b|\bseoul\b"), "KR"),
    (re.compile(r"\baustralia\b|\bsydney\b|\bmelbourne\b"), "AU"),
    (re.compile(r"\bnew\s+zealand\b|\bauckland\b"), "NZ"),
    (re.compile(r"\bbrazil\b|\bsao\s+paulo\b|\brio\s+de\s+janeiro\b"), "BR"),
    (re.compile(r"\bargentina\b|\bbuenos\s+aires\b"), "AR"),
    (re.compile(r"\bcolombia\b|\bbogota\b|\bmedellin\b"), "CO"),
    (re.compile(r"\bchile\b|\bsantiago\b"), "CL"),
    (re.compile(r"\buae\b|\bdubai\b|\babu\s+dhabi\b"), "AE"),
    (re.compile(r"\bisrael\b|\btel\s+aviv\b|\bjerusalem\b"), "IL"),
    (re.compile(r"\begypt\b|\bcairo\b"), "EG"),
    (re.compile(r"\bsouth\s+africa\b|\bjohannesburg\b|\bcape\s+town\b"), "ZA"),
    (re.compile(r"\bnigeria\b|\blagos\b"), "NG"),
    (re.compile(r"\bkenya\b|\bnairobi\b"), "KE"),
    (re.compile(r"\bphilippines\b|\bmanila\b"), "PH"),
    (re.compile(r"\bthailand\b|\bbangkok\b"), "TH"),
    (re.compile(r"\bvietnam\b|\bhanoi\b|\bho\s+chi\s+minh\b"), "VN"),
    (re.compile(r"\bindonesia\b|\bjakarta\b"), "ID"),
    (re.compile(r"\bmalaysia\b|\bkuala\s+lumpur\b"), "MY"),
    (re.compile(r"\bpakistan\b|\bkarachi\b|\blahore\b|\bislamabad\b"), "PK"),
    (re.compile(r"\bturkey\b|\bistanbul\b|\bankara\b"), "TR"),
    (re.compile(r"\brussia\b|\bmoscow\b|\bst\.?\s+petersburg\b"), "RU"),
    (re.compile(r"\bukraine\b|\bkyiv\b|\bkiev\b"), "UA"),
    # Regional buckets — treat as non-US when paired with "remote".
    (re.compile(r"\bemea\b|\bapac\b|\bla(?:tam|c)\b"), "REGION"),
    (re.compile(r"\beurope\b|\bnordics?\b"), "REGION"),
]

_US_STATE_ABBR = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga",
    "hi", "id", "il", "in", "ia", "ks", "ky", "la", "me", "md",
    "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc",
    "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy",
    "dc",
})

_US_STATE_FULL = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
})

_US_CITY_MARKERS = frozenset({
    "new york", "nyc", "manhattan", "brooklyn", "queens", "jersey city",
    "san francisco", "sf", "bay area", "palo alto", "mountain view",
    "sunnyvale", "menlo park", "san jose", "oakland", "berkeley",
    "seattle", "bellevue", "redmond",
    "boston", "cambridge", "somerville",
    "austin", "dallas", "houston", "san antonio",
    "chicago", "evanston",
    "atlanta", "nashville", "charlotte", "raleigh", "durham",
    "washington dc", "arlington", "bethesda", "college park",
    "denver", "boulder",
    "los angeles", "la", "santa monica", "pasadena", "irvine", "san diego",
    "miami", "orlando", "tampa",
    "philadelphia", "pittsburgh",
    "minneapolis", "st. paul",
    "portland",
    "phoenix", "scottsdale", "tempe",
    "salt lake city",
})

# Flexible US-token regex: matches anywhere in the string, word-bounded.
# A single compiled regex replaces three separate matchers (explicit
# substring list, comma-prefixed abbrev search, space-padded full-state
# check). Any of the following hits → US:
#
#   • Country variants: "United States", "United States of America",
#     "U.S.", "U.S.A.", "USA", "US"
#   • All 50 state full names + DC + Puerto Rico (word-bounded,
#     whitespace-flexible so "new york" and "new  york" both match)
#   • All 50 state 2-letter abbreviations + DC (word-bounded so "MD"
#     in "Remote MD" matches the same as "Washington, DC" does)
#
# Word-boundaries (`\b`) mean "MD" matches but "md" inside "mdivine"
# does not; "US" matches at end/start of tokens and inside parens
# "(US)" because `(` is non-word.

_US_COUNTRY_PATTERN = (
    r"\bunited\s+states(?:\s+of\s+america)?\b"
    r"|\bu\.s\.(?:a\.?)?"          # U.S. / U.S.A.
    r"|\busa?\b"                    # US / USA
)

_US_STATE_FULL_ALT = "|".join(
    # Longest first so "new york" beats a hypothetical "york" prefix in
    # a future addition. Whitespace inside multi-word names is flexible.
    re.escape(s).replace(r"\ ", r"\s+")
    for s in sorted(_US_STATE_FULL | {"puerto rico"}, key=len, reverse=True)
)

_US_STATE_ABBR_ALT = "|".join(sorted(_US_STATE_ABBR))

_US_ACCEPT_REGEX = re.compile(
    r"(?:"
    + _US_COUNTRY_PATTERN
    + r"|\b(?:" + _US_STATE_FULL_ALT + r")\b"
    + r"|\b(?:" + _US_STATE_ABBR_ALT + r")\b"
    + r")",
)

_NYC_MARKERS = frozenset({
    "new york", "nyc", "new york city", "manhattan", "brooklyn", "queens",
    "bronx", "staten island", "jersey city", "hoboken", "ny metro",
    "new york, ny", "ny, ny", "new york metro",
})


def _normalize(raw: str) -> str:
    """Lowercase, collapse whitespace, pad ends so `\b` works uniformly."""
    s = raw.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return f" {s} "


def country_from_location(raw: str) -> str | None:
    """Best-effort country ISO code. Returns:

    - "US" if the string contains any US country/state/abbrev token
      matched word-bounded anywhere (see ``_US_ACCEPT_REGEX``)
    - "XX" (country code) if a non-US country/region marker is found
    - None otherwise (ambiguous — caller decides policy)
    """
    if not raw:
        return None
    norm = _normalize(raw)

    # Non-US first — if Berlin and "Remote" both appear, we want Berlin
    # to win. Same if "Bulgaria" and "MD" both appeared (hypothetical).
    for pattern, code in _NON_US_COUNTRY_MARKERS:
        if pattern.search(norm):
            return code

    # US: any state full name, 2-letter abbrev, or country variant
    # matched word-bounded anywhere. "Remote MD" → US, "MD Remote" → US,
    # "Maryland" → US, "United States" → US, "(US)" → US.
    if _US_ACCEPT_REGEX.search(norm):
        return "US"

    # City-only fallback — catches markers like "Manhattan", "Bay Area",
    # "NYC" that aren't state names.
    for city in _US_CITY_MARKERS:
        if f" {city} " in norm or f" {city}," in norm:
            return "US"

    return None


def is_us_location(raw: str) -> bool:
    """True only if a positive US marker is present.

    Strict whitelist policy: a non-US marker list can never be
    exhaustive (Bulgaria, Greece, Serbia, Baltic states, etc. keep
    sneaking through). Instead, require positive evidence — a US
    state (full or abbreviated), a US city, or an explicit "United
    States" / "US" / "USA" tag. Anything else is rejected, including
    bare "Remote" with no country signal.

    Exception: empty / whitespace-only strings are accepted — an
    unset location field isn't evidence of non-US.
    """
    if not raw or not raw.strip():
        return True  # empty = no location constraint; accept
    country = country_from_location(raw)
    # Explicit non-US → reject. Explicit US → accept. Everything else
    # (including "Remote" with no country info) → reject.
    return country == "US"


def nyc_bonus(raw: str) -> float:
    """+0.15 for NYC-adjacent postings; 0.0 otherwise."""
    if not raw:
        return 0.0
    norm = _normalize(raw)
    for m in _NYC_MARKERS:
        if f" {m} " in norm or f" {m}," in norm:
            return 0.15
    return 0.0
