#!/usr/bin/env python3
"""
Fetch Malaysian Nacaduba species from iNaturalist and write data/species.json.

Usage:
    pip install requests
    python scripts/fetch_species.py

The script writes to data/species.json relative to the repository root.
Run it whenever you want to refresh the species list and photos.
"""

import json
import time
from datetime import date
from pathlib import Path

import requests

INAT_BASE = "https://api.inaturalist.org/v1"
MALAYSIA_PLACE_ID = 7155
OUTPUT = Path(__file__).parent.parent / "data" / "species.json"

session = requests.Session()
session.headers.update({"User-Agent": "Nacaduba-ID/1.0 (github.com/chancsc/Nacaduba_id)"})


def get_nacaduba_taxon_id() -> int:
    print("Resolving Nacaduba genus taxon ID…")
    r = session.get(f"{INAT_BASE}/taxa", params={"q": "Nacaduba", "rank": "genus", "per_page": 20})
    r.raise_for_status()
    for taxon in r.json()["results"]:
        if taxon["name"] == "Nacaduba":
            print(f"  Found: Nacaduba = taxon_id {taxon['id']}")
            return taxon["id"]
    raise RuntimeError("Could not find Nacaduba genus in iNaturalist taxa search")


def fetch_all_species(taxon_id: int) -> list:
    print(f"Fetching species counts for Malaysia (place_id={MALAYSIA_PLACE_ID})…")
    all_results = []
    page = 1
    while True:
        r = session.get(
            f"{INAT_BASE}/observations/species_counts",
            params={
                "taxon_id": taxon_id,
                "place_id": MALAYSIA_PLACE_ID,
                "verifiable": "true",
                "per_page": 500,
                "page": page,
            },
        )
        r.raise_for_status()
        data = r.json()
        total = data["total_results"]
        all_results.extend(data["results"])
        print(f"  Page {page}: got {len(data['results'])} entries (total so far: {len(all_results)}/{total})")
        if len(all_results) >= total:
            break
        page += 1
        time.sleep(0.5)
    return all_results


def extract_photos(taxon: dict, max_photos: int = 5) -> list:
    photos = []
    for tp in taxon.get("taxon_photos", [])[:max_photos]:
        p = tp.get("photo", {})
        url = p.get("url", "")
        if url:
            url = url.replace("/square.", "/medium.")
            photos.append({"url": url, "attribution": p.get("attribution", "")})
    return photos


def build_entry(item: dict) -> dict:
    t = item["taxon"]
    return {
        "id": t["id"],
        "name": t["name"],
        "common_name": t.get("preferred_common_name", ""),
        "inat_url": f"https://www.inaturalist.org/taxa/{t['id']}",
        "taxon_photos": extract_photos(t),
        "observation_count": item["count"],
        "wikipedia_url": t.get("wikipedia_url", ""),
    }


def main():
    taxon_id = get_nacaduba_taxon_id()
    raw = fetch_all_species(taxon_id)

    species = sorted([build_entry(item) for item in raw], key=lambda s: s["name"])

    output = {
        "generated": str(date.today()),
        "place": "Malaysia",
        "place_id": MALAYSIA_PLACE_ID,
        "species": species,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(species)} species to {OUTPUT}")


if __name__ == "__main__":
    main()
