#!/usr/bin/env python3
"""
feed_check.py
=============

Tests every feed in uk_media_digest.py and reports which are alive.

Run this whenever the digest looks thin, or every few months as
housekeeping. Outlets move and retire RSS paths without announcing it,
and a dead feed fails silently inside the main script.

    python feed_check.py

It always exits successfully, even when feeds are dead, because a couple of
dead feeds is routine housekeeping rather than a broken build. Add --strict
if you would rather it fail:

    python feed_check.py --strict

Columns:
  ITEMS     how many entries the feed returned
  7 DAYS    how many of those were published in the last week
  ON TOPIC  how many recent ones score highly enough to reach the digest
"""

import sys
from datetime import datetime, timedelta, timezone

from uk_media_digest import (FEEDS, MIN_RELEVANCE_SCORE, REGION_SHORT,
                             clean_text, fetch_feed, score_article)


def check(name, url, region):
    result = {"name": name, "url": url, "region": region, "ok": False,
              "note": "", "entries": 0, "recent": 0, "relevant": 0,
              "newest": None, "undated": 0}

    # Reuse the main script's fetcher so the headers, and therefore the
    # results, match exactly what the real run will see.
    parsed = fetch_feed(name, url)
    if parsed is None:
        result["note"] = "request failed, see the line above"
        return result

    entries = parsed.entries or []
    result["entries"] = len(entries)

    if not entries:
        result["note"] = "responded but contained no items"
        return result

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    newest = None
    for entry in entries:
        stamp = None
        for attr in ("published_parsed", "updated_parsed"):
            parsed_time = getattr(entry, attr, None)
            if parsed_time:
                stamp = datetime(*parsed_time[:6], tzinfo=timezone.utc)
                break
        is_recent = False
        if stamp:
            if newest is None or stamp > newest:
                newest = stamp
            if stamp >= cutoff:
                result["recent"] += 1
                is_recent = True
        else:
            result["undated"] += 1

        # Only score recent items. Scoring the whole back catalogue made this
        # column misleading: a feed carrying months of archive could report
        # dozens of on-topic articles while offering almost nothing new.
        if is_recent:
            title = clean_text(getattr(entry, "title", ""))
            summary = clean_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            score, _region = score_article(title, summary, region)
            if score >= MIN_RELEVANCE_SCORE:
                result["relevant"] += 1

    result["newest"] = newest
    result["ok"] = True
    if newest:
        age = (datetime.now(timezone.utc) - newest).days
        if age > 21:
            result["note"] = f"stale, newest item is {age} days old"
    return result


def main():
    print(f"Checking {len(FEEDS)} feeds\n")
    print(f"{'FEED':<22} {'REGION':<9} {'STATUS':<7} {'ITEMS':>6} {'7 DAYS':>7} "
          f"{'ON TOPIC':>9}  NOTE")
    print("-" * 92)

    failures = []
    warnings = []

    for name, url, region in FEEDS:
        result = check(name, url, region)
        if result["ok"]:
            status = "OK"
            if result["note"]:
                status = "STALE"
                warnings.append(result)
        else:
            status = "DEAD"
            failures.append(result)

        note = result["note"]
        if result["ok"] and result["undated"] and not note:
            note = f"{result['undated']} items have no date and will be skipped"

        print(f"{result['name']:<22} {REGION_SHORT.get(result['region'], result['region']):<9} "
              f"{status:<7} {result['entries']:>6} {result['recent']:>7} "
              f"{result['relevant']:>9}  {note}")

    print("-" * 92)
    working = len(FEEDS) - len(failures)
    print(f"\n{working}/{len(FEEDS)} feeds responded.")

    if failures:
        print("\nThese need fixing or removing from FEEDS in uk_media_digest.py:")
        for result in failures:
            print(f"  {result['name']}")
            print(f"    {result['url']}")
            print(f"    {result['note']}")

    if warnings:
        print("\nThese work but look stale, worth a look:")
        for result in warnings:
            print(f"  {result['name']}: {result['note']}")

    # Deliberately exits 0 even when feeds are dead. A couple of dead feeds
    # is normal housekeeping, not a broken build, and a red cross on the
    # Actions tab would suggest otherwise. Pass --strict to fail instead.
    if "--strict" in sys.argv and failures:
        return 1
    if failures:
        print("\nThis is not a build failure. The digest still works without "
              "these feeds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
