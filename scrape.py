"""Pull today's USC residential dining menus and save them as JSON for the website."""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://hospitality.usc.edu/wp-json/hsp-api/v1/get-res-dining-menus/"
HALLS = {
    "evk": "Everybody's Kitchen",
    "parkside": "Parkside",
    "university-village": "USC Village",
}
SKIP_STATIONS = ["Allergen Awareness Zone"]  # registered students only
DATA_DIR = Path(__file__).parent / "docs" / "data"
LA = ZoneInfo("America/Los_Angeles")
HEADERS = {"User-Agent": "Mozilla/5.0 (student menu planner; daily fetch)"}


def fetch_hall(code, today):
    params = {"y": today.year, "m": f"{today.month:02d}", "d": f"{today.day:02d}"}
    resp = requests.get(BASE + code, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    raw = resp.json()

    meals = []
    for meal in raw.get("meals", []):
        stations = []
        for st in meal.get("stations", []):
            if any(skip in st["station"] for skip in SKIP_STATIONS):
                continue
            items = [
                {
                    "name": x["item"],
                    "allergens": x.get("allergens", []),
                    "tags": x.get("preferences", []),
                }
                for x in st.get("menu", [])
                if not x["item"].isupper()  # drop section headers like "POKE BOWL BAR"
            ]
            if items:
                stations.append({"station": st["station"], "items": items})
        if stations:
            meals.append({"meal": meal["name"], "stations": stations})
    return {"code": code, "name": HALLS[code], "meals": meals}


def main():
    now = datetime.now(LA)
    today_file = DATA_DIR / f"{now:%Y-%m-%d}.json"
    # The workflow fires at two UTC times to cover daylight saving. Run on the first one
    # that lands at or after 6 AM Los Angeles time, once per day. (GitHub sometimes starts
    # scheduled runs late, so this doesn't require it to be exactly 6 AM.)
    if "--force" not in sys.argv:
        if now.hour < 6:
            print(f"Skipping: it's {now:%H:%M} in Los Angeles, before 6 AM.")
            return
        if today_file.exists():
            print(f"Skipping: today's menu was already saved.")
            return

    halls = []
    for code in HALLS:
        try:
            halls.append(fetch_hall(code, now))
        except Exception as err:
            print(f"Failed on {code}: {err}")
            halls.append({"code": code, "name": HALLS[code], "meals": [], "error": str(err)})

    payload = {"date": now.strftime("%Y-%m-%d"), "updated": now.isoformat(), "halls": halls}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in (f"{payload['date']}.json", "latest.json"):
        (DATA_DIR / name).write_text(json.dumps(payload, indent=2))
    count = sum(len(s["items"]) for h in halls for m in h["meals"] for s in m["stations"])
    print(f"Saved {count} items for {payload['date']}.")


if __name__ == "__main__":
    main()
