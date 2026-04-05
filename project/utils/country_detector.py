"""
GCC country detector — enrich article ``countries`` from free text.

For each article, the detector scans the **title**, **description**, and
**category** fields for explicit mentions of the six GCC countries and also
maps common abbreviations / government body names to their country.

Rules
-----
* If **one or more** GCC countries are detected in the text, ``countries`` is
  set to *exactly* those detected countries (overriding the spider default).
* ``primaryCountry`` is set to the first detected country (in the canonical
  order below).
* If **no** GCC country is detected the existing ``countries`` / ``primaryCountry``
  values from the source config are left unchanged.  This avoids incorrectly
  removing a source's own country when an article doesn't name it explicitly
  (e.g. an official government press release from Bahrain NBR).
* ``isMultiCountry`` is set to ``True`` when more than one country is detected.
* ``jurisdictions`` is derived from ``countries`` using the ISO mapping below.

Detection approach
------------------
All matching is case-insensitive, word-boundary-aware, applied to the
concatenated plain text of title + description + category.  The patterns use
``re.search`` with pre-compiled ``re.Pattern`` objects for performance.

Country patterns cover:
  - English names (full and common alternatives)
  - Arabic transliterations used in English text (e.g. "KSA", "UAE")
  - Key government / tax authority names that are country-unique
    (e.g. "ZATCA" → Saudi Arabia, "FTA" → UAE, "NBR" → Bahrain)
  - Common adjective forms (e.g. "Emirati", "Saudi", "Qatari")
"""
import re

# ------------------------------------------------------------------ #
# Canonical definitions                                               #
# ------------------------------------------------------------------ #

#: Internal ID → (display name, ISO 3166-1 alpha-2, ordered list of regex patterns)
#  Patterns are matched case-insensitively; the first matching pattern wins
#  for order-of-detection purposes, but ALL patterns are checked.
_GCC: list[tuple[str, str, list[str]]] = [
    (
        "United Arab Emirates",
        "AE",
        [
            r"\bUAE\b",
            r"\bU\.A\.E\b",
            r"\bUnited Arab Emirates\b",
            r"\bEmirate[s]?\b",
            r"\bEmirati\b",
            r"\bAbu Dhabi\b",
            r"\bDubai\b",
            r"\bSharjah\b",
            r"\bAjman\b",
            r"\bFujairah\b",
            r"\bRas al[- ]Khaimah\b",
            r"\bUmm al[- ]Quwain\b",
            # UAE tax authorities / laws
            r"\bFTA\b",                      # Federal Tax Authority
            r"\bMoF\b",                      # Ministry of Finance UAE context
            r"\bDTA\b",                      # UAE Double Tax Agreement context
            r"\bCIT\b",                      # Corporate Income Tax (UAE)
        ],
    ),
    (
        "Kingdom of Saudi Arabia",
        "SA",
        [
            r"\bKSA\b",
            r"\bSaudi\b",
            r"\bSaudi Arabia\b",
            r"\bKingdom of Saudi Arabia\b",
            r"\bRiyadh\b",
            r"\bJeddah\b",
            r"\bMakkah\b",
            r"\bMedina\b",
            r"\bDammam\b",
            r"\bNEOM\b",
            # Saudi tax authorities
            r"\bZATCA\b",                    # Zakat, Tax and Customs Authority
            r"\bGAZAT\b",
            r"\bGACA\b",
            r"\bSASMO\b",
        ],
    ),
    (
        "Bahrain",
        "BH",
        [
            r"\bBahrain\b",
            r"\bBahraini\b",
            r"\bManama\b",
            r"\bNBR\b",                      # National Bureau for Revenue
            r"\bEDB\b",                      # Bahrain Economic Development Board
        ],
    ),
    (
        "Oman",
        "OM",
        [
            r"\bOman\b",
            r"\bOmani\b",
            r"\bMuscat\b",
            r"\bSalalah\b",
            r"\bTax Authority of Oman\b",
            r"\bOTA\b",                      # Oman Tax Authority
            r"\bITA\b",                      # Oman Income Tax Authority
        ],
    ),
    (
        "Qatar",
        "QA",
        [
            r"\bQatar\b",
            r"\bQatari\b",
            r"\bDoha\b",
            r"\bGTA\b",                      # General Tax Authority Qatar
            r"\bDhareeba\b",
        ],
    ),
    (
        "Kuwait",
        "KW",
        [
            r"\bKuwait\b",
            r"\bKuwaiti\b",
            r"\bKuwait City\b",
            r"\bMOF Kuwait\b",
            r"\bNRA Kuwait\b",
        ],
    ),
]

# Pre-compile all patterns once
_COMPILED: list[tuple[str, str, list[re.Pattern]]] = [
    (country, iso, [re.compile(p, re.IGNORECASE) for p in patterns])
    for country, iso, patterns in _GCC
]

# Quick lookup: canonical name → ISO code
_ISO: dict[str, str] = {country: iso for country, iso, _ in _GCC}


# ------------------------------------------------------------------ #
# Public API                                                          #
# ------------------------------------------------------------------ #

def detect_countries(article: dict) -> list[str]:
    """Return list of GCC country names found in the article's text fields.

    Returns an empty list if no GCC country is detected (meaning the caller
    should leave the existing ``countries`` value unchanged).
    """
    haystack = " ".join([
        article.get("title") or "",
        article.get("description") or "",
        article.get("category") or "",
    ])
    detected: list[str] = []
    for country, _iso, patterns in _COMPILED:
        for pat in patterns:
            if pat.search(haystack):
                detected.append(country)
                break  # one match for this country is enough
    return detected


def enrich_countries(item: dict) -> dict:
    """Update ``countries``, ``primaryCountry``, ``isMultiCountry``, and
    ``jurisdictions`` on *item* in-place, based on country detection.

    If no GCC country is detected in the text, the item is left unchanged.
    Returns the (mutated) item for chaining.
    """
    detected = detect_countries(item)
    if not detected:
        return item

    item["countries"] = detected
    item["primaryCountry"] = detected[0]
    item["country"] = detected[0]
    item["isMultiCountry"] = len(detected) > 1
    item["jurisdictions"] = [_ISO[c] for c in detected if c in _ISO]
    return item
