#!/usr/bin/env python3
"""
backfill.py
===========

Builds editions for dates that have already passed, so the archive is not
empty the first time someone visits.

    python backfill.py 2026-08-03 2026-08-06

Important limitation
--------------------
This does NOT recover the news as it stood on those days. RSS feeds only
carry their most recent items, usually the last ten to twenty-five. Anything
that has scrolled off is gone for good. So a backfilled edition is built
from whatever is still in the feeds today and happens to fall inside that
date's window.

In practice that means:
  - Backfilling the last few days usually works well.
  - Backfilling weeks or months ago will produce a thin or empty edition.

Backfilled editions are written to editions/ and added to manifest.json,
exactly like a normal run. index.html is left alone, so the newest real
edition stays on the landing page.
"""

import os
import sys
from datetime import datetime, timedelta
from uk_media_digest import (
    OUTPUT_DIR, REGION_ORDER, REGION_SHORT, UK, build_payload,
    collect_articles, generate_digest, load_manifest, log, manifest_entry,
    previous_scheduled_run, save_manifest, save_payload, tidy_citations,
    write_edition_html,
)


def build_for_date(target_date):
    """target_date is a date object. Returns a manifest entry, or None."""
    # Treat the target as if the run happened at 02:00 UK time that morning.
    as_of = datetime(
        target_date.year, target_date.month, target_date.day, 2, 5, tzinfo=UK
    )
    window_start = previous_scheduled_run(as_of)

    log("")
    log(f"=== Backfilling {as_of:%A %d %B %Y} ===")
    log(f"Window: {window_start:%a %d %b %H:%M} to {as_of:%a %d %b %H:%M}")

    articles = collect_articles(window_start, as_of)
    if len(articles) < 3:
        log(f"Only {len(articles)} articles are still available for that "
            f"window. Skipping this date.")
        return None

    digest, model = generate_digest(articles, window_start, as_of)
    digest, cited = tidy_citations(digest, articles)
    if not cited:
        log("No valid sources were cited. Skipping this date.")
        return None

    iso = f"{as_of:%Y-%m-%d}"
    date_line = (
        f"{as_of:%A %d %B %Y} \u00b7 covering "
        f"{window_start:%a %d %b} to {as_of - timedelta(days=1):%a %d %b}"
    )
    headline = digest.get("headline") or "Media and technology round-up"

    counts = {r: sum(1 for a in cited if a.get("region") == r) for r in REGION_ORDER}
    spread = ", ".join(f"{REGION_SHORT[r]} {counts[r]}" for r in REGION_ORDER if counts[r])
    footer_note = (
        f"Backfilled from feed archives, so it may be less complete than a "
        f"live run. Built from {len(cited)} cited articles ({spread}). "
        f"Written by {model}."
    )

    payload = build_payload(digest, cited, iso, date_line, headline,
                            footer_note, model, backfilled=True)
    data_path = save_payload(payload)
    # is_current stays False, so a backfilled edition never takes over the
    # landing page from the genuinely newest one.
    page_path = write_edition_html(payload, is_current=False)
    log(f"Wrote {page_path}")
    log(f"Wrote {data_path}")

    return manifest_entry(payload)


def main():
    dates = sys.argv[1:]
    if not dates:
        print(__doc__)
        print("No dates given. Example:")
        print("  python backfill.py 2026-08-03 2026-08-06")
        return 1

    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set.")
        return 1

    parsed_dates = []
    for value in dates:
        try:
            parsed_dates.append(datetime.strptime(value, "%Y-%m-%d").date())
        except ValueError:
            print(f"'{value}' is not a date. Use the form 2026-08-03.")
            return 1

    manifest_path = os.path.join(OUTPUT_DIR, "manifest.json")
    manifest = load_manifest(manifest_path)
    existing = {entry.get("iso") for entry in manifest}

    added = 0
    for target in sorted(parsed_dates):
        iso = target.strftime("%Y-%m-%d")
        if iso in existing:
            log(f"{iso} already exists in the manifest. Skipping.")
            continue
        entry = build_for_date(target)
        if entry:
            manifest.append(entry)
            added += 1

    if not added:
        log("")
        log("Nothing was added. Most likely those dates are too far back for "
            "the feeds to still carry them.")
        return 0

    save_manifest(manifest)

    log("")
    log(f"Added {added} backfilled edition(s). "
        f"{len(manifest)} editions in the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
