#!/usr/bin/env python3
"""
diagnose.py
===========

Works out why a digest came out thin. Reports what the feeds are actually
offering for the current window, what got filtered out and why, and whether
the feeds are even capable of covering the window.

    python diagnose.py

Costs nothing. It does not call Gemini and does not write any files.

Optional argument, to look at a different span:

    python diagnose.py 7        # pretend the window is the last 7 days

Read the output in this order:

  1. FEED DEPTH   - can each feed physically cover the window?
  2. REGION SPLIT - is a whole region about to be dropped?
  3. NEAR MISSES  - is the relevance filter throwing away good stories?
"""

import sys
from datetime import datetime, timedelta

from uk_media_digest import (
    FEEDS, MIN_RELEVANCE_SCORE, MAX_ARTICLES_PER_REGION, REGION_ORDER,
    REGION_SHORT, UK, clean_text, entry_time, fetch_feed,
    previous_scheduled_run, score_article,
)


def gather(window_start, now, days_override=None):
    rows = []
    all_articles = []
    near_misses = []

    for name, url, feed_region in FEEDS:
        parsed = fetch_feed(name, url)
        row = {
            "name": name, "region": feed_region, "ok": parsed is not None,
            "items": 0, "undated": 0, "in_window": 0, "passed": 0,
            "best": None, "oldest_age_days": None,
        }
        if parsed is None:
            rows.append(row)
            continue

        entries = parsed.entries or []
        row["items"] = len(entries)
        oldest = None

        for entry in entries[:40]:
            stamp = entry_time(entry)
            if stamp is None:
                row["undated"] += 1
                continue
            if oldest is None or stamp < oldest:
                oldest = stamp
            if not (window_start <= stamp <= now):
                continue
            row["in_window"] += 1

            title = clean_text(getattr(entry, "title", ""))
            if len(title) < 12:
                continue
            summary = clean_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            score, region = score_article(title, summary, feed_region)
            if row["best"] is None or score > row["best"]:
                row["best"] = score

            if score >= MIN_RELEVANCE_SCORE:
                row["passed"] += 1
                all_articles.append(
                    {"title": title, "outlet": name, "score": score, "region": region}
                )
            elif score >= MIN_RELEVANCE_SCORE - 6:
                near_misses.append(
                    {"title": title, "outlet": name, "score": score, "region": region}
                )

        if oldest is not None:
            row["oldest_age_days"] = (now - oldest).total_seconds() / 86400

        rows.append(row)

    return rows, all_articles, near_misses


def main():
    now = datetime.now(UK)

    days_override = None
    if len(sys.argv) > 1:
        try:
            days_override = float(sys.argv[1])
        except ValueError:
            print(f"'{sys.argv[1]}' is not a number of days.")
            return 1

    if days_override:
        window_start = now - timedelta(days=days_override)
        print(f"Using an override window of the last {days_override} days.\n")
    else:
        window_start = previous_scheduled_run(now)

    window_days = (now - window_start).total_seconds() / 86400
    print(f"WINDOW: {window_start:%a %d %b %H:%M} to {now:%a %d %b %H:%M} "
          f"({window_days:.1f} days)\n")

    rows, articles, near_misses = gather(window_start, now, days_override)

    # ---------- 1. Feed depth ----------
    print("=" * 78)
    print("1. FEED DEPTH")
    print("=" * 78)
    print(f"{'FEED':<22}{'REG':<5}{'ITEMS':>6}{'UNDATED':>8}{'WINDOW':>7}"
          f"{'PASSED':>7}{'BEST':>6}  COVERS WINDOW?")
    print("-" * 78)

    truncating = []
    dead = []
    for r in rows:
        if not r["ok"]:
            dead.append(r["name"])
            print(f"{r['name']:<22}{REGION_SHORT.get(r['region'], r['region'])[:4]:<5}"
                  f"{'DEAD':>6}")
            continue

        covers = "-"
        if r["oldest_age_days"] is not None:
            if r["oldest_age_days"] < window_days * 0.95:
                covers = f"NO, only {r['oldest_age_days']:.1f}d deep"
                truncating.append((r["name"], r["oldest_age_days"]))
            else:
                covers = "yes"

        best = "-" if r["best"] is None else str(r["best"])
        print(f"{r['name']:<22}{REGION_SHORT.get(r['region'], r['region'])[:4]:<5}"
              f"{r['items']:>6}{r['undated']:>8}{r['in_window']:>7}"
              f"{r['passed']:>7}{best:>6}  {covers}")

    print("-" * 78)
    total_undated = sum(r["undated"] for r in rows)
    print(f"{len(rows) - len(dead)}/{len(rows)} feeds responded. "
          f"{sum(r['passed'] for r in rows)} articles passed the filter.")

    if total_undated:
        print(f"\n{total_undated} entries were skipped for having no publication "
              f"date. They cannot be placed in the window, so they are dropped. "
              f"If that number is large, those feeds are contributing nothing.")

    if truncating:
        print(f"\nWARNING: {len(truncating)} feed(s) do not go back far enough to "
              f"cover this {window_days:.1f} day window:")
        for name, depth in sorted(truncating, key=lambda x: x[1]):
            print(f"  {name}: only {depth:.1f} days deep")
        print("  Stories older than that have already scrolled off and are")
        print("  gone. Harvesting the feeds daily would capture them.")

    # ---------- 2. Region split ----------
    print()
    print("=" * 78)
    print("2. REGION SPLIT")
    print("=" * 78)
    for region in REGION_ORDER:
        in_region = [a for a in articles if a["region"] == region]
        label = REGION_SHORT[region]
        if len(in_region) >= 2:
            capped = min(len(in_region), MAX_ARTICLES_PER_REGION)
            note = "will be written"
            if len(in_region) > MAX_ARTICLES_PER_REGION:
                note += f", capped at {MAX_ARTICLES_PER_REGION}"
            print(f"  {label:<10} {len(in_region):>3} articles  ->  {note} ({capped} used)")
        else:
            print(f"  {label:<10} {len(in_region):>3} articles  ->  DROPPED ENTIRELY")
            print("             A region needs at least 2 articles to get its own")
            print("             section. This is why a deeper dive can disappear.")

    if len([r for r in REGION_ORDER
            if len([a for a in articles if a["region"] == r]) >= 2]) < 2:
        print("\n  Only one region qualifies, so there will be no regional")
        print("  headings and no second deep dive.")

    # ---------- 3. Near misses ----------
    print()
    print("=" * 78)
    print(f"3. NEAR MISSES (scored just under the threshold of {MIN_RELEVANCE_SCORE})")
    print("=" * 78)
    if not near_misses:
        print("  None. The filter is not the bottleneck.")
    else:
        near_misses.sort(key=lambda a: a["score"], reverse=True)
        print(f"  {len(near_misses)} article(s) were rejected narrowly. If these look")
        print("  relevant, the weighting needs widening, not the feed list.\n")
        for a in near_misses[:20]:
            print(f"  score {a['score']:>3}  ({a['outlet']}) {a['title'][:60]}")
        if len(near_misses) > 20:
            print(f"  ... and {len(near_misses) - 20} more")

    # ---------- Verdict ----------
    print()
    print("=" * 78)
    print("LIKELY EXPLANATION")
    print("=" * 78)
    passed = sum(r["passed"] for r in rows)
    if passed < 6:
        print("  Very little got through. Check whether the feeds above are")
        print("  mostly DEAD, or whether the window genuinely had little news.")
    elif truncating and len(truncating) >= 3:
        print("  Several feeds cannot cover the window, so real stories are being")
        print("  lost before the script ever sees them. Daily harvesting would")
        print("  be the biggest single improvement.")
    elif len(near_misses) > passed / 2:
        print("  Plenty of articles are being rejected narrowly. The relevance")
        print("  weighting is probably too strict for the new subject areas.")
    else:
        print("  The pipeline looks healthy. If the digest still reads thin, it")
        print("  was most likely a quiet few days.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
