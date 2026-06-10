"""
Shared keyword categories used for post tagging and Polymarket market classification.
"""

import re

KEYWORD_GROUPS = {
    "tariffs_trade": [
        "tariff", "tariffs", "trade war", "trade deal", "import tax",
        "china deal", "trade deficit", "most favored nation", "section 232",
        "section 301", "customs", "duty", "duties", "trade agreement",
    ],
    "personnel_firing": [
        "fired", "resign", "resigns", "resigned", "appointment", "appoint",
        "nominated", "nominee", "secretary of", "attorney general",
        "you're fired", "termination", "removed from",
    ],
    "iran_middle_east": [
        "iran", "tehran", "nuclear deal", "sanctions", "ayatollah",
        "persian gulf", "strait of hormuz", "israel", "hamas", "hezbollah",
        "gaza", "west bank", "middle east", "saudi arabia", "ceasefire",
    ],
    "ukraine_nato": [
        "ukraine", "zelensky", "zelenskyy", "nato", "putin", "russia",
        "kyiv", "moscow", "peace deal", "ceasefire", "war in ukraine",
    ],
    "economy_markets": [
        "stock market", "dow jones", "s&p", "nasdaq", "inflation",
        "interest rate", "federal reserve", "fed chair", "powell",
        "recession", "gdp", "unemployment", "jobs report",
    ],
    "legal_investigation": [
        "witch hunt", "hoax", "rigged", "unfair", "indicted", "indictment",
        "trial", "verdict", "acquitted", "criminal", "corrupt",
        "weaponized", "two-tier", "political persecution", "impeach",
    ],
    "immigration_border": [
        "border", "illegal", "deportation", "deport", "ice", "cbp",
        "migrant", "migrants", "asylum", "invasion", "caravan",
        "remain in mexico", "title 42",
    ],
    "midterms_elections": [
        "midterm", "midterms", "2026", "house seats", "senate seats",
        "republican majority", "democrat", "maga", "america first",
        "vote", "election integrity", "ballot", "election",
    ],
    "executive_actions": [
        "executive order", "e.o.", "proclamation", "veto", "signed",
        "declared", "emergency", "national emergency", "pardon", "pardoned",
    ],
    "health_fitness": [
        "great shape", "perfect health", "doctor", "physical", "cognitive",
        "walter reed", "medical", "strong and healthy",
    ],
    "media_attacks": [
        "fake news", "lamestream", "enemy of the people", "cnn",
        "new york times", "washington post", "msnbc", "mainstream media",
        "corrupt media",
    ],
    "china": [
        "china", "chinese", "xi jinping", "beijing", "ccp",
        "fentanyl", "tiktok", "taiwan", "south china sea",
    ],
}

TRUMP_MARKET_TERMS = [
    "trump", "donald trump", "president trump", "white house",
    "mar-a-lago", "truth social", "maga", "25th amendment",
]


def _keyword_in_text(keyword: str, text: str) -> bool:
    """Match whole words/phrases; avoid substring false positives (e.g. ice in office)."""
    pattern = r"\b" + re.escape(keyword.lower()) + r"\b"
    return re.search(pattern, text) is not None


def _count_matches(text: str, keywords: list[str]) -> int:
    return sum(1 for kw in keywords if _keyword_in_text(kw, text))


def infer_category(question: str, description: str = "") -> str:
    """
    Score each category by keyword match rate and return the best fit.
    Falls back to general_trump when no category keywords match.
    """
    text = f"{question} {description}".lower()
    best_category = "general_trump"
    best_score = 0.0

    for category, keywords in KEYWORD_GROUPS.items():
        matches = _count_matches(text, keywords)
        if matches == 0:
            continue
        score = matches / len(keywords)
        if score > best_score:
            best_score = score
            best_category = category

    # Require a meaningful match; single weak hits stay general_trump
    if best_score < 0.04:
        return "general_trump"

    return best_category


def category_scores(question: str, description: str = "") -> dict[str, float]:
    """Return relevance scores for every category (for debugging/display)."""
    text = f"{question} {description}".lower()
    scores = {}
    for category, keywords in KEYWORD_GROUPS.items():
        matches = _count_matches(text, keywords)
        scores[category] = matches / len(keywords) if keywords else 0.0
    return scores
