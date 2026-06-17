#!/usr/bin/env python3
"""
Query the Butterflies of Peninsular Malaysia notebook for Nacaduba species.
Saves each response to nacaduba_data/{species_name}.txt
Respects the 200 queries/day limit — skips species already queried.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import date
from html.parser import HTMLParser

BASE_URL = os.environ.get("BUTTERFLY_NOTEBOOK_URL", "https://calling-lanka-fixtures-merry.trycloudflare.com")
DATA_DIR = os.path.join(os.path.dirname(__file__), "nacaduba_data")
LOG_FILE = os.path.join(DATA_DIR, "_query_log.json")
DAILY_LIMIT = 200
TIMEOUT = 120
RETRIES = 2


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def get_text(self):
        return "".join(self.parts)


def html_to_text(html: str) -> str:
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|li|h[1-6]|tr)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<hr\s*/?>", "\n---\n", html, flags=re.IGNORECASE)
    s = _Stripper()
    s.feed(html)
    text = s.get_text()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _load_log() -> dict:
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            return json.load(f)
    return {"date": str(date.today()), "count": 0, "queried": []}


def _save_log(log: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def _today_log() -> dict:
    log = _load_log()
    if log.get("date") != str(date.today()):
        log = {"date": str(date.today()), "count": 0, "queried": []}
        _save_log(log)
    return log


def safe_filename(species: str) -> str:
    return re.sub(r"[^\w\s-]", "", species).strip().replace(" ", "_").lower()


def query_species(species: str, prompt_override: str | None = None) -> str | None:
    log = _today_log()
    fname = safe_filename(species)
    out_path = os.path.join(DATA_DIR, f"{fname}.txt")

    if os.path.exists(out_path):
        print(f"[SKIP] {species} — file already exists: {out_path}")
        return None

    if fname in log["queried"]:
        print(f"[SKIP] {species} — already queried today.")
        return None

    if log["count"] >= DAILY_LIMIT:
        print(f"[LIMIT] Daily limit of {DAILY_LIMIT} reached. Stopping.")
        return None

    question = prompt_override or (
        f"{species} - share the underside ID keys, including tail info"
    )

    print(f"[QUERY] {species} ...", flush=True)
    payload = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    raw = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode()
            break
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[RETRY {attempt}/{RETRIES}] {species}: {e}", flush=True)
            if attempt < RETRIES:
                time.sleep(5)
    if raw is None:
        print(f"[ERROR] Failed after {RETRIES} attempts for {species}.")
        return None

    try:
        data = json.loads(raw)
        html_answer = data.get("answer", "")
    except json.JSONDecodeError:
        print(f"[ERROR] Non-JSON response for {species}.")
        return None

    text = html_to_text(html_answer)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"Species: {species}\n")
        f.write(f"Query: {question}\n")
        f.write(f"Date: {date.today()}\n")
        f.write("=" * 60 + "\n\n")
        f.write(text)
    print(f"[SAVED] {out_path}")

    log["count"] += 1
    log["queried"].append(fname)
    _save_log(log)

    return text


def query_list(species_list: list[str], delay: float = 2.0):
    for i, sp in enumerate(species_list):
        result = query_species(sp.strip())
        if result is None:
            log = _today_log()
            if log["count"] >= DAILY_LIMIT:
                print("[LIMIT] Daily limit hit — stopping batch.")
                break
        if i < len(species_list) - 1:
            time.sleep(delay)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Single species:  python query_nacaduba.py 'nacaduba kurava'")
        print("  List from file:  python query_nacaduba.py --file nacaduba_species_list.txt")
        print("  Status:          python query_nacaduba.py --status")
        sys.exit(0)

    if sys.argv[1] == "--status":
        log = _today_log()
        print(f"Date      : {log['date']}")
        print(f"Queries   : {log['count']} / {DAILY_LIMIT}")
        print(f"Queried   : {', '.join(log['queried']) or 'none'}")
        remaining = DAILY_LIMIT - log["count"]
        print(f"Remaining : {remaining}")
        sys.exit(0)

    if sys.argv[1] == "--file":
        if len(sys.argv) < 3:
            print("Provide a file path: --file nacaduba_species_list.txt")
            sys.exit(1)
        with open(sys.argv[2]) as f:
            species = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        print(f"Loaded {len(species)} species from {sys.argv[2]}")
        query_list(species)
    else:
        species_name = " ".join(sys.argv[1:])
        result = query_species(species_name)
        if result:
            print("\n--- ANSWER ---")
            print(result)
