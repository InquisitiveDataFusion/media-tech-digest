#!/usr/bin/env python3
"""
harvest.py
==========

Collects the feeds regularly and accumulates what it finds, so the digest
can see stories that have already scrolled off by the time it runs.

Why this exists
---------------
RSS feeds only carry their most recent items. A diagnostic run found that
17 of 28 feeds could not reach back far enough to cover a single digest
window. Variety's feed held under a day of news. Anything older was simply
gone before the digest ever looked.

So this runs every few hours, adds anything new to data/harvest.json, and
the digest reads that store instead of relying on whatever happens to be in
the feeds at 2am on a Monday.

    python harvest.py

Deliberately stores the raw title, summary and outlet rather than a score.
Scoring happens at digest time, so a change to the relevance weighting
applies to everything already harvested rather than only to new items.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

from uk_media_digest import (
    FEEDS, OUTPUT_DIR, UK, clean_text, entry_time, fetch_feed, log,
)

# How long to keep harvested items. The digest never looks back more than
# about four days, so ten gives a healthy margin without the file bloating.
RETAIN_DAYS = 10

# A safety ceiling, in case a feed misbehaves and floods the store.
MAX_ITEMS = 6000

HARVEST_PATH = "data/harvest.json"


def store_path(output_dir=None):
    return os.path.join(output_dir or OUTPUT_DIR, HARVEST_PATH)


def normalise(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())[:70]


def load_store(output_dir=None):
    path = store_path(output_dir)
    if not os.path.exists(path):
        return {"updated": None, "items": []}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data.get("items"), list):
            raise ValueError("items is not a list")
        return data
    except Exception as exc:
        log(f"Harvest store unreadable ({exc}). Starting a fresh one.")
        return {"updated": None, "items": []}


def save_store(store, output_dir=None):
    path = store_path(output_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=1, ensure_ascii=False)
    return path


def harvest(output_dir=None):
    now = datetime.now(UK)
    store = load_store(output_dir)
    existing = {item["key"] for item in store["items"] if item.get("key")}

    added = 0
    per_feed = []
    failed = []

    for name, url, feed_region in FEEDS:
        parsed = fetch_feed(name, url)
        if parsed is None:
            failed.append(name)
            continue

        new_here = 0
        for entry in (parsed.entries or [])[:60]:
            stamp = entry_time(entry)
            if stamp is None:
                continue
            if stamp > now + timedelta(hours=6):
                continue        # a badly set clock somewhere upstream
            if stamp < now - timedelta(days=RETAIN_DAYS):
                continue

            title = clean_text(getattr(entry, "title", ""))
            if len(title) < 12:
                continue

            key = normalise(title)
            if key in existing:
                continue

            summary = clean_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            if len(summary) > 320:
                summary = summary[:317].rsplit(" ", 1)[0] + "..."

            existing.add(key)
            store["items"].append({
                "key": key,
                "title": title,
                "summary": summary,
                "link": getattr(entry, "link", ""),
                "outlet": name,
                "feed_region": feed_region,
                "published": stamp.isoformat(timespec="seconds"),
            })
            new_here += 1
            added += 1

        if new_here:
            per_feed.append((name, new_here))

    # Prune anything past the retention window
    cutoff = now - timedelta(days=RETAIN_DAYS)
    before = len(store["items"])
    kept = []
    for item in store["items"]:
        try:
            when = datetime.fromisoformat(item["published"])
        except Exception:
            continue          # unparseable, drop it
        if when >= cutoff:
            kept.append(item)
    pruned = before - len(kept)

    kept.sort(key=lambda i: i["published"], reverse=True)
    if len(kept) > MAX_ITEMS:
        log(f"Store hit the {MAX_ITEMS} item ceiling, keeping the newest.")
        kept = kept[:MAX_ITEMS]

    store["items"] = kept
    store["updated"] = now.isoformat(timespec="seconds")

    path = save_store(store, output_dir)

    log(f"Harvest at {now:%a %d %b %H:%M %Z}")
    if failed:
        log(f"  {len(failed)} feed(s) did not respond: {', '.join(failed)}")
    for name, count in sorted(per_feed, key=lambda x: -x[1]):
        log(f"  +{count:<3} {name}")
    log(f"  Added {added}, pruned {pruned}, store now holds {len(kept)} items "
        f"covering the last {RETAIN_DAYS} days.")
    log(f"  {path}")

    return added


def items_in_window(window_start, window_end, output_dir=None):
    """Harvested items falling inside a digest window."""
    store = load_store(output_dir)
    out = []
    for item in store["items"]:
        try:
            when = datetime.fromisoformat(item["published"])
        except Exception:
            continue
        if window_start <= when <= window_end:
            out.append(dict(item, published_dt=when))
    return out


if __name__ == "__main__":
    try:
        harvest()
    except Exception as exc:
        log(f"Harvest failed: {exc}")
        sys.exit(1)
