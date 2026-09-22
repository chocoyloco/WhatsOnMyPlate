"""Estimate nutrition for each dish on today's menu and save it to docs/data/nutrition.json.

Estimates come from Claude (Anthropic API), based on the dish name and its station.
Each dish is estimated ONCE. After that it's read from the file, never requested again,
so the cost doesn't grow with the number of people using the site.

Needs the ANTHROPIC_API_KEY secret in GitHub.

Each saved dish looks like:
  "Chicken Mole": {
    "portion": "1 entree scoop",                                # what Regular means
    "grams": 150,                                               # a Regular serving
    "low":  {"cal": 230, "protein": 22, "carbs": 8,  "fat": 11},
    "high": {"cal": 320, "protein": 30, "carbs": 14, "fat": 18},
    "kind": "dish",                  # dish | topping | not_a_dish
    "needs_review": false,           # true = numbers didn't add up, check by hand
    "checked": "2026-09-22",
    "v": 4
  }

To fix a dish by hand: edit its numbers and add  "override": true
The script will never touch an entry marked override.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import requests

VERSION = 4          # bump when the estimating method changes; older entries get redone
MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1.00, 5.00     # dollars per million tokens, for the cost printout
BATCH_SIZE = 60                      # dishes per request
MAX_DISHES_PER_RUN = 400

DATA_DIR = Path(__file__).parent / "docs" / "data"
MENU_FILE = DATA_DIR / "latest.json"
NUTRITION_FILE = DATA_DIR / "nutrition.json"
API_URL = "https://api.anthropic.com/v1/messages"
MACROS = ("cal", "protein", "carbs", "fat")

INSTRUCTIONS = """You estimate nutrition for items on a university dining hall menu (USC, Los Angeles).
Recipes and portions are not published, so give realistic estimates for how a large campus
dining hall typically makes and serves each item.

For each item, estimate ONE regular serving as a student would get it at that station, and describe
it in "portion" (a short label shown to students, like "1 slice", "1 link", "1 egg", "1 scoop", "1 cup",
"2 tbsp", "1 sandwich"):
- Items that come in pieces (pizza, sausage links, eggs, taquitos, tacos, sandwiches, bagels, muffins,
  slices of bread/cheese/ham, cookies): Regular = ONE piece, even if the name is plural
  ("Assorted Bagels" = 1 bagel, "Turkey Sausage Links" = 1 link, "Eggs" = 1 egg, "Boiled Eggs" = 1 egg).
  Pizza = 1 slice of a large dining-hall pizza.
- Scooped main dishes and sides: one standard scoop/portion (e.g. chicken entree ~120-170 g,
  rice ~150 g, soup ~1 cup).
- Toppings, sauces, dressings, spreads, and salad-bar add-ons: one typical portion
  (e.g. dressing 2 tbsp, shredded cheese 1/4 cup, oil 1 tbsp).
- Things like "Make Your Own Waffle Bar", "Daily Grill Specials", or "available upon request"
  notes are not a single food: mark kind "not_a_dish" and set portion, grams, low and high to null.
Use the same conventions for similar items so they are consistent with each other, and anchor
to these reference portions:
  1 slice large cheese pizza ~120 g; 1 pork sausage link ~45 g; 1 large egg ~50 g;
  scrambled eggs 1 scoop ~100 g (about 2 eggs); 1 bagel ~95 g; 1 slice sandwich bread ~40 g;
  1 slice deli meat ~15 g (Regular = 2 slices); 1 taquito ~40 g; 1 taco ~100 g;
  cooked rice/pasta 1 scoop ~150 g; protein entree 1 scoop ~140 g; cooked vegetables 1 scoop ~100 g;
  soup or chili 1 cup ~240 g; yogurt 1 cup ~170 g; dressing 2 tbsp ~30 g.

Give a LOW and HIGH estimate for calories, protein (g), carbs (g), and fat (g). The range should
reflect real uncertainty in recipe and portion, not be artificially narrow or wide. Low and high
should each be internally consistent (calories roughly = 4*protein + 4*carbs + 9*fat).

Reply with ONLY a JSON array, no other text, one object per item, using the item name exactly as given:
[{"name": "...", "kind": "dish" | "topping" | "not_a_dish", "portion": "1 scoop", "grams": 150,
  "low": {"cal": 0, "protein": 0, "carbs": 0, "fat": 0},
  "high": {"cal": 0, "protein": 0, "carbs": 0, "fat": 0}}]"""


def normalize(name):
    """So 'Scrambled Egg' and 'Scrambled Eggs ' count as the same dish."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\(.*?\)", " ", text)
    words = []
    for w in re.findall(r"[a-z]+", text):
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "y"
        elif w.endswith("oes"):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def needs_lookup(entry):
    return entry is None or (not entry.get("override") and entry.get("v", 1) < VERSION)


def ask_claude(items, key):
    """items = [(name, station), ...]. Returns (list of estimates, cost in dollars)."""
    listing = "\n".join(f"- {name}  (station: {station})" for name, station in items)
    resp = requests.post(
        API_URL,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={
            "model": MODEL,
            "max_tokens": 16000,
            "temperature": 0,
            "system": INSTRUCTIONS,
            "messages": [{"role": "user", "content": f"Items:\n{listing}"}],
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    text = "".join(block.get("text", "") for block in data.get("content", []))
    if data.get("stop_reason") == "max_tokens":
        raise RuntimeError("reply was cut off")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError("reply had no JSON list")
    text = text[start:end + 1]
    usage = data.get("usage", {})
    cost = usage.get("input_tokens", 0) / 1e6 * PRICE_IN + usage.get("output_tokens", 0) / 1e6 * PRICE_OUT
    return json.loads(text), cost


def clean(estimate):
    """Check one estimate. Returns a saved entry, or None if it's unusable."""
    kind = estimate.get("kind", "dish")
    entry = {"kind": kind, "portion": None, "grams": None, "low": None, "high": None,
             "needs_review": False, "checked": date.today().isoformat(), "v": VERSION}
    if kind == "not_a_dish":
        return entry
    try:
        grams = round(float(estimate["grams"]))
        low = {k: round(float(estimate["low"][k])) for k in MACROS}
        high = {k: round(float(estimate["high"][k])) for k in MACROS}
    except (KeyError, TypeError, ValueError):
        return None
    for k in MACROS:                              # make sure low really is the lower one
        if low[k] > high[k]:
            low[k], high[k] = high[k], low[k]
    entry.update(portion=str(estimate.get("portion") or f"{grams} g"), grams=grams, low=low, high=high)

    # Sanity checks: flag for review instead of trusting numbers that don't add up.
    for side in (low, high):
        from_macros = 4 * side["protein"] + 4 * side["carbs"] + 9 * side["fat"]
        if abs(from_macros - side["cal"]) > max(20, 0.3 * side["cal"]):
            entry["needs_review"] = True
    if grams <= 0 or grams > 700 or high["cal"] > 1500 or any(v < 0 for v in low.values()):
        entry["needs_review"] = True
    return entry


def main():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        sys.exit("ANTHROPIC_API_KEY is missing. Add it under Settings > Secrets and variables > Actions.")
    if not MENU_FILE.exists():
        sys.exit("No latest.json yet. Run the menu scrape first.")

    menu = json.loads(MENU_FILE.read_text())
    table = json.loads(NUTRITION_FILE.read_text()) if NUTRITION_FILE.exists() else {}

    # Every dish on today's menu, with the station it's served at (helps the estimate).
    station_of = {}
    for h in menu["halls"]:
        for m in h["meals"]:
            for s in m["stations"]:
                for i in s["items"]:
                    station_of.setdefault(i["name"].strip(), s["station"])

    # Old entries from the USDA version (or any older version) get redone,
    # except ones you fixed by hand.
    for name in [n for n, e in table.items() if needs_lookup(e)]:
        del table[name]

    # Reuse saved estimates for tiny name differences ("Scrambled Egg" vs "Scrambled Eggs").
    by_normal = {normalize(n): n for n in table}
    reused = 0
    for name in station_of:
        if name not in table and normalize(name) in by_normal:
            table[name] = dict(table[by_normal[normalize(name)]], same_as=by_normal[normalize(name)])
            reused += 1

    todo = [n for n in sorted(station_of) if n not in table][:MAX_DISHES_PER_RUN]
    print(f"{len(station_of)} dishes today: {len(station_of) - len(todo) - reused} already saved, "
          f"{reused} matched a saved name, {len(todo)} to estimate.")

    total_cost = added = review = 0
    queue = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    while queue:
        batch = queue.pop(0)
        try:
            estimates, cost = ask_claude([(n, station_of[n]) for n in batch], key)
        except Exception as err:
            if len(batch) > 5 and "API error 4" not in str(err):
                print(f"  Batch of {len(batch)} failed ({err}); trying it in two halves")
                half = len(batch) // 2
                queue[:0] = [batch[:half], batch[half:]]
            else:
                print(f"  Batch of {len(batch)} failed: {err} (will retry next run)")
            continue
        total_cost += cost
        by_name = {e.get("name", "").strip(): e for e in estimates if isinstance(e, dict)}
        for name in batch:
            est = by_name.get(name) or next((e for n, e in by_name.items() if normalize(n) == normalize(name)), None)
            entry = clean(est) if est else None
            if not entry:
                print(f"  {name} -> no usable estimate (will retry next run)")
                continue
            table[name] = entry
            added += 1
            review += entry["needs_review"]
            if entry["kind"] == "not_a_dish":
                print(f"  {name} -> not a single dish, skipped")
            else:
                flag = "  <- needs review" if entry["needs_review"] else ""
                print(f"  {name} -> {entry['portion']} ({entry['grams']} g), {entry['low']['cal']}-{entry['high']['cal']} cal, "
                      f"{entry['low']['protein']}-{entry['high']['protein']} g protein{flag}")

    NUTRITION_FILE.write_text(json.dumps(dict(sorted(table.items())), indent=2))
    print(f"Added {added} ({review} need review). Table has {len(table)} dishes. "
          f"This run cost about ${total_cost:.4f}.")


if __name__ == "__main__":
    main()
