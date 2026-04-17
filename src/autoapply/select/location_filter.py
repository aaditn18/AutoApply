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

_US_EXPLICIT_MARKERS: tuple[str, ...] = (
    "united states", "u.s.", "u.s", " us ", " us,", " usa", "(us)", "(u.s.)",
    "remote - us", "remote (us)", "remote us", "us remote", "remote, us",
    "remote, united states", "remote - united states",
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

    - "US" if the string contains a US city/state marker or an explicit US tag
    - "XX" (country code) if a non-US country/region marker is found
    - None otherwise (ambiguous — caller decides policy)
    """
    if not raw:
        return None
    norm = _normalize(raw)

    # Non-US first — if Berlin and "Remote" both appear, we want Berlin to win.
    for pattern, code in _NON_US_COUNTRY_MARKERS:
        if pattern.search(norm):
            return code

    # Explicit US markers
    for marker in _US_EXPLICIT_MARKERS:
        if marker in norm:
            return "US"

    # US cities
    for city in _US_CITY_MARKERS:
        if f" {city} " in norm or f" {city}," in norm:
            return "US"

    # US state abbreviations like "Austin, TX" or "Boston, MA, USA"
    # Matches `\b, ST\b` or `\b(ST)\b` where ST is a 2-letter state code
    abbr_match = re.search(r",\s*([a-z]{2})(?:\b|,)", norm)
    if abbr_match and abbr_match.group(1) in _US_STATE_ABBR:
        return "US"

    # Full state names
    for state in _US_STATE_FULL:
        if f" {state} " in norm or f" {state}," in norm:
            return "US"

    return None


def is_us_location(raw: str) -> bool:
    """True if the location is US-resolvable or ambiguous-remote.

    Policy: unambiguous non-US → False, US markers → True, bare "Remote"
    with no country marker → True (most postings default to US).
    """
    if not raw:
        return True  # empty = no location constraint; accept
    norm = _normalize(raw)
    country = country_from_location(raw)
    if country is None:
        # Bare "Remote" without any country signal → accept as likely US.
        if "remote" in norm:
            return True
        # Nothing at all matched — be permissive, downstream scoring will
        # handle quality filtering. A pure garbage string is rare.
        return True
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
