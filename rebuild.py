#!/usr/bin/env python3
"""
rebuild.py
==========

Re-renders every edition from its saved data, so a change to the look of
the site applies to the whole archive rather than only to future editions.

    python rebuild.py

Use it after changing anything that affects appearance:

  - the colours, fonts or layout in template.py
  - SITE_TITLE or SITE_SUBTITLE in uk_media_digest.py
  - the section names or tag labels

It does NOT contact Gemini and it does NOT fetch any news. Nothing is
rewritten, reworded or re-summarised. It only rebuilds the pages around
words that were already published, so it is free to run and safe to repeat.

What it touches:
  data/*.json        read only, never modified
  editions/*.html    rewritten, one per saved edition
  index.html         rewritten from the newest edition
  manifest.json      rebuilt, keeping any older entries it cannot rebuild

Editions published before the data files existed cannot be re-rendered,
because the finished page is the only record of them. Those pages are left
untouched and stay in the sidebar with their original look.
"""

import glob
import json
import os
import sys

from uk_media_digest import (
    OUTPUT_DIR, load_manifest, log, manifest_entry, save_manifest,
    write_edition_html,
)


def load_payloads(output_dir):
    """Every saved edition, newest last."""
    pattern = os.path.join(output_dir, "data", "*.json")
    payloads = []
    broken = []

    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            broken.append((os.path.basename(path), str(exc)))
            continue

        missing = [
            key for key in ("iso", "headline", "date_line", "digest", "articles")
            if key not in payload
        ]
        if missing:
            broken.append((os.path.basename(path), f"missing {', '.join(missing)}"))
            continue

        payload.setdefault("footer_note", "")
        payloads.append(payload)

    payloads.sort(key=lambda p: p["iso"])
    return payloads, broken


def find_orphan_pages(output_dir, known_isos):
    """Editions that exist as HTML but have no saved data behind them."""
    orphans = []
    pattern = os.path.join(output_dir, "editions", "*.html")
    for path in sorted(glob.glob(pattern)):
        iso = os.path.splitext(os.path.basename(path))[0]
        if iso not in known_isos:
            orphans.append(iso)
    return orphans


def main():
    output_dir = OUTPUT_DIR
    payloads, broken = load_payloads(output_dir)

    if broken:
        log("These data files could not be read and were skipped:")
        for name, reason in broken:
            log(f"  {name}: {reason}")
        log("")

    if not payloads:
        log("No edition data found in data/.")
        log("")
        log("Nothing has been changed. This is expected if no edition has been "
            "published since the data files were introduced. Build one edition "
            "normally, then this will have something to work with.")
        return 0

    newest = payloads[-1]["iso"]
    log(f"Rebuilding {len(payloads)} edition(s). Newest is {newest}.")

    rebuilt = 0
    failures = []
    entries = []

    for payload in payloads:
        try:
            is_current = payload["iso"] == newest
            path = write_edition_html(payload, output_dir, is_current=is_current)
            entries.append(manifest_entry(payload))
            rebuilt += 1
            marker = "  (also index.html)" if is_current else ""
            log(f"  {payload['iso']} -> {path}{marker}")
        except Exception as exc:
            failures.append((payload["iso"], str(exc)))
            log(f"  {payload['iso']} FAILED: {exc}")

    if not entries:
        log("")
        log("Every edition failed to render, so manifest.json has been left "
            "alone rather than emptied.")
        return 1

    # Editions published before the data files existed cannot be rebuilt, but
    # they must not vanish from the sidebar either. Keep their existing
    # manifest entries and merge them back in.
    rebuilt_isos = {entry["iso"] for entry in entries}
    preserved = [
        entry for entry in load_manifest(os.path.join(output_dir, "manifest.json"))
        if entry.get("iso") and entry["iso"] not in rebuilt_isos
    ]
    if preserved:
        log("")
        log(f"Kept {len(preserved)} older edition(s) in the sidebar that have "
            f"no saved data. Their pages keep the previous look.")
        for entry in preserved:
            log(f"  {entry['iso']}")

    save_manifest(entries + preserved, output_dir)
    log("")
    log(f"Rebuilt {rebuilt} edition(s). "
        f"manifest.json now lists {len(entries) + len(preserved)}.")

    orphans = find_orphan_pages(output_dir, {p["iso"] for p in payloads})
    unlisted = [iso for iso in orphans
                if iso not in {e.get("iso") for e in preserved}]
    if unlisted:
        log("")
        log("These editions have a page on disk but appear in neither the data "
            "files nor the manifest, so they are unreachable from the sidebar:")
        for iso in unlisted:
            log(f"  {iso}")

    if failures:
        log("")
        log("These editions failed to rebuild:")
        for iso, reason in failures:
            log(f"  {iso}: {reason}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
