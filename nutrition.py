"""Look up nutrition for each dish on today's menu and save it to docs/data/nutrition.json.

Each dish is looked up ONCE, by its exact USC name ("Chicken Mole" and "Adobo Chicken"
are separate entries). After that it's read from the file, never requested again.

Data source: USDA FoodData Central. Needs the FDC_API_KEY secret in GitHub.

Each saved dish looks like:
  "Chicken Mole": {
    "match": "Chicken with mole sauce",     # the USDA entry that fit best
    "grams": 120,                            # assumed Regular serving
    "low":  {"cal": 190, "protein": 20, "carbs": 6,  "fat": 9},   # per Regular serving
    "high": {"cal": 260, "protein": 26, "carbs": 11, "fat": 14},
    "needs_review": false,                   # true = weak match, check it by hand
    "checked": "2026-09-22"
  }

To fix a bad match by hand: edit that dish's numbers and add  "override": true
The script will never touch an entry marked override.
"""
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "docs" / "data"
MENU_FILE = DATA_DIR / "latest.json"
NUTRITION_FILE = DATA_DIR / "nutrition.json"
SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# Prepared/generic foods only (skip branded grocery products).
DATA_TYPES = ["Survey (FNDDS)", "SR Legacy"]
TOP_MATCHES = 3            # how many USDA results feed the low/high range
MAX_LOOKUPS_PER_RUN = 400  # USDA allows 1,000 requests/hour; stay well under it

# USDA nutrient numbers (values are per 100 g)
NUTRIENTS = {"208": "cal", "203": "protein", "205": "carbs", "204": "fat"}

# Assumed Regular serving in grams, by keyword. First match wins, so order matters.
SERVING_GRAMS = [
    (r"soup|stew|chili|pozole|menudo|broth|ramen|pho", 240),
    (r"smoothie|juice|milk|latte|drink", 240),
    (r"salad", 150),
    (r"rice|pasta|noodle|spaghetti|penne|mac|quinoa|couscous|grits|oatmeal|potato|fries", 150),
    (r"cookie|brownie|cake|pie|muffin|donut|dessert|pudding", 70),
    (r"bread|roll|biscuit|tortilla|toast|bagel|croissant", 60),
    (r"sauce|salsa|dressing|gravy|hummus|dip", 30),
    (r"chicken|beef|steak|pork|turkey|fish|salmon|tuna|shrimp|tofu|lamb|carnitas|asada|meatball", 120),
    (r"egg", 100),
    (r"broccoli|carrot|green bean|spinach|vegetable|veggie|squash|zucchini|corn|pea|cauliflower", 100),
    (r"bean|lentil|chickpea|dal", 130),
    (r"fruit|apple|banana|melon|berr|orange|pineapple", 120),
]
DEFAULT_GRAMS = 130

STOPWORDS = {"with", "and", "the", "a", "of", "in", "on", "style", "house", "fresh", "our"}


def serving_grams(name):
    for pattern, grams in SERVING_GRAMS:
        if re.search(pattern, name, re.I):
            return grams
    return DEFAULT_GRAMS


def clean_query(name):
    """Turn a menu name into a better search: 'Chicken Mole w/ Rice (GF)' -> 'chicken mole with rice'."""
    q = re.sub(r"\(.*?\)", " ", name)          # drop (GF), (Vegan), etc.
    q = re.sub(r"\bw/\s*", "with ", q, flags=re.I)
    q = re.sub(r"'s\b", "", q)                  # Chef's -> Chef
    q = re.sub(r"[^A-Za-z\s]", " ", q)
    return re.sub(r"\s+", " ", q).strip().lower()


def words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS and len(w) > 2}


def overlap(dish, usda_description):
    """Share of the dish's words that appear in the USDA description (0 to 1)."""
    d = words(dish)
    if not d:
        return 0
    u = words(usda_description)
    hits = sum(1 for w in d if any(w in x or x in w for x in u))
    return hits / len(d)


def search(query, key):
    resp = requests.post(
        SEARCH_URL,
        params={"api_key": key},
        json={"query": query, "dataType": DATA_TYPES, "pageSize": 10},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("foods", [])


def per_100g(food):
    out = {}
    for n in food.get("foodNutrients", []):
        field = NUTRIENTS.get(str(n.get("nutrientNumber")))
        if field and n.get("value") is not None:
            if field == "cal" and str(n.get("unitName", "")).upper() != "KCAL":
                continue
            out[field] = n["value"]
    return out if len(out) == 4 else None


def look_up(name, key):
    query = clean_query(name)
    foods = search(query, key)
    if not foods and len(query.split()) > 2:          # try a shorter search
        foods = search(" ".join(query.split()[:2]), key)

    scored = []
    for f in foods:
        nutrients = per_100g(f)
        if nutrients:
            scored.append((overlap(name, f["description"]), f["description"], nutrients))
    if not scored:
        return None

    scored.sort(key=lambda s: s[0], reverse=True)
    # Only let close matches into the range, so "Beef with mole" can't skew "Chicken Mole".
    top = scored[0][0]
    best = [s for s in scored if s[0] >= top - 0.2][:TOP_MATCHES]
    grams = serving_grams(name)
    scale = grams / 100

    def pick(fn):
        return {k: round(fn(b[2][k] for b in best) * scale) for k in ("cal", "protein", "carbs", "fat")}

    return {
        "match": best[0][1],
        "grams": grams,
        "low": pick(min),
        "high": pick(max),
        "needs_review": best[0][0] < 0.5,
        "checked": date.today().isoformat(),
    }


def main():
    key = os.environ.get("FDC_API_KEY", "").strip()
    if not key:
        sys.exit("FDC_API_KEY is missing. Add it under Settings > Secrets and variables > Actions.")
    if not MENU_FILE.exists():
        sys.exit("No latest.json yet. Run the menu scrape first.")

    menu = json.loads(MENU_FILE.read_text())
    table = json.loads(NUTRITION_FILE.read_text()) if NUTRITION_FILE.exists() else {}

    dishes = sorted({i["name"].strip() for h in menu["halls"] for m in h["meals"]
                     for s in m["stations"] for i in s["items"]})
    new = [d for d in dishes if d not in table]
    print(f"{len(dishes)} dishes today, {len(new)} not looked up yet.")

    added = missed = 0
    for name in new[:MAX_LOOKUPS_PER_RUN]:
        try:
            entry = look_up(name, key)
        except Exception as err:
            print(f"  Error on {name!r}: {err} (will retry next run)")
            continue
        if entry:
            table[name] = entry
            added += 1
            flag = "  <- needs review" if entry["needs_review"] else ""
            print(f"  {name} -> {entry['match']}{flag}")
        else:
            # Save an empty entry so we don't search for it every day; fill it in by hand.
            table[name] = {"match": None, "grams": serving_grams(name), "low": None, "high": None,
                           "needs_review": True, "checked": date.today().isoformat()}
            missed += 1
            print(f"  {name} -> no match found")
        time.sleep(0.3)

    NUTRITION_FILE.write_text(json.dumps(dict(sorted(table.items())), indent=2))
    print(f"Added {added}, no match {missed}. Table now has {len(table)} dishes.")


if __name__ == "__main__":
    main()
