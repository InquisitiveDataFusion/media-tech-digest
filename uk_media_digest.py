#!/usr/bin/env python3
"""
uk_media_digest.py
==================

Builds a UK media and broadcast news digest as a static HTML page, ready to
be served by GitHub Pages.

What it does, in order:
  1. Checks it is really 02:00 UK time (the workflow fires twice to cover
     GMT and BST, so one of the two triggers exits here doing nothing).
  2. Works out the window to cover, back to the previous scheduled run.
  3. Fetches the RSS feeds and scores each article for UK media relevance.
  4. Sends the survivors to Gemini, which returns structured JSON.
  5. Checks citation integrity, renumbers references, drops uncited sources.
  6. Writes editions/YYYY-MM-DD.html, refreshes index.html, updates
     manifest.json (which every page reads to build its own sidebar).

Environment variables:
  Required : GEMINI_API_KEY
  Optional : FORCE_RUN ("true" skips the 02:00 clock check, for manual runs)
             OUTPUT_DIR (defaults to the repo root)
"""

import html
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import feedparser
import requests

from template import PAGE_TEMPLATE

# ============================================================
# 1. CONFIGURATION
# ============================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FORCE_RUN = os.getenv("FORCE_RUN", "").lower() in ("1", "true", "yes")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")

UK = ZoneInfo("Europe/London")
SCHEDULED_DAYS = {0, 3}      # Monday, Thursday
SCHEDULED_HOUR = 2

# ----- What the page calls itself -------------------------------------
# Change these two lines to rename the digest. Every page, old and new,
# picks up the change the next time it is built. Write them as plain
# text; ampersands and accents are handled for you.
#
# Keep the title short. It has to fit in the bar at the top of a phone
# screen alongside the menu button and the reading controls.
SITE_TITLE = "Media & Tech Digest"
SITE_SUBTITLE = "UK and Americas · Summarised with AI"
# Used instead on narrow phone screens, where the full one would wrap.
SITE_SUBTITLE_SHORT = "Summarised with AI"

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TEXT_MODELS = [
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
]

HTTP_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

MAX_ARTICLES_PER_REGION = 26
MIN_RELEVANCE_SCORE = 4

# Above this many articles in one region, the digest is written in separate
# calls per region and stitched together, rather than one overloaded prompt.
SINGLE_PROMPT_LIMIT = 30

# Feeds, each tagged with the region it mostly covers.
#   "uk"       - British media, broadcast and radio
#   "americas" - US and Canadian media and tech
#   "global"   - mixed; region is worked out per article from its wording
#
# Verified working on 07 Aug 2026 are marked OK. Everything marked NEW is a
# candidate that has NOT been tested. Run feed_check.py after any change.
FEEDS = [
    # ---------- UK media and broadcast ----------
    ("Broadband TV News", "https://www.broadbandtvnews.com/feed/", "uk"),        # OK
    ("Advanced Television", "https://www.advanced-television.com/feed", "uk"),   # OK
    ("C21Media", "https://www.c21media.net/feed/", "uk"),                        # OK
    ("VideoWeek", "https://videoweek.com/feed/", "uk"),                          # OK
    ("Guardian Media", "https://www.theguardian.com/media/rss", "uk"),           # OK
    ("BBC Entertainment",
     "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml", "uk"),      # OK
    ("The Media Leader", "https://the-media-leader.com/feed/", "uk"),            # OK
    ("RadioToday", "https://radiotoday.co.uk/feed/", "uk"),                      # OK
    ("Podnews", "https://podnews.net/rss", "global"),                            # OK

    # Replacements for feeds that returned 403 or were empty.
    ("Press Gazette", "https://pressgazette.co.uk/rss", "uk"),                   # NEW
    ("Digital TV Europe", "https://www.digitaltveurope.com/rss", "uk"),          # NEW
    ("Broadcast Now", "https://www.broadcastnow.co.uk/XmlServers/navsectionRSS.aspx?navsectioncode=1000", "uk"),  # NEW
    ("Deadline UK", "https://deadline.com/vcategory/international/feed/", "uk"), # NEW

    # ---------- UK and global technology ----------
    ("The Verge", "https://www.theverge.com/rss/index.xml", "global"),           # NEW
    ("TechCrunch", "https://techcrunch.com/feed/", "global"),                    # NEW
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", "global"),  # NEW
    ("Engadget", "https://www.engadget.com/rss.xml", "global"),                  # NEW
    ("BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml", "uk"),   # NEW

    # ---------- United States media, advertising and tech ----------
    ("Variety", "https://variety.com/feed/", "americas"),                        # NEW
    ("Hollywood Reporter", "https://www.hollywoodreporter.com/feed/", "americas"),  # NEW
    ("TVNewsCheck", "https://tvnewscheck.com/feed/", "americas"),                # NEW
    ("Digiday", "https://digiday.com/feed/", "americas"),                        # NEW
    ("Nieman Lab", "https://www.niemanlab.org/feed/", "americas"),               # NEW
    ("Adweek", "https://www.adweek.com/feed/", "americas"),                      # NEW

    # ---------- Canada media and tech ----------
    ("Playback", "https://playbackonline.ca/feed/", "americas"),                 # NEW
    ("Media in Canada", "https://mediaincanada.com/feed/", "americas"),          # NEW
    ("Broadcast Dialogue", "https://broadcastdialogue.com/feed/", "americas"),   # NEW
    ("Cartt.ca", "https://cartt.ca/feed/", "americas"),                          # NEW
]

# Relevance scoring. Ported from the original script, with radio and audio
# promoted to primary alongside the broadcasters.
PRIMARY_TERMS = [
    "bbc", "itv", "channel 4", "channel4", "channel 5", "sky", "sky uk",
    "uktv", "stv", "s4c", "bbc studios", "itv studios", "itvx", "bbc iplayer",
    "all4", "my5", "now tv", "freeview", "freely", "youview",
    "global radio", "global media", "bauer media", "bauer", "wireless group",
    "capital fm", "heart fm", "lbc", "classic fm", "kiss fm", "absolute radio",
    "talksport", "times radio", "radio 1", "radio 2", "radio 4", "radio 5 live",
    "ofcom", "channel 4 racing", "boom radio", "greatest hits radio",
]

SECONDARY_TERMS = [
    "broadcaster", "broadcasting", "streaming", "bvod", "vod", "linear tv",
    "commissioning", "commissioner", "indie producer", "production company",
    "advertising revenue", "ad revenue", "audience share", "ratings", "barb",
    "rajar", "licence fee", "public service broadcasting", "psb",
    "dab", "digital radio", "podcast", "audio advertising", "on demand",
    "media regulator", "media bill", "content deal", "distribution deal",
    "carriage deal", "viewing figures", "media nations",
]

TERTIARY_TERMS = [
    "television", "media", "radio", "audience", "viewers", "listeners",
    "advertising", "subscription", "programming", "documentary", "drama",
    "channel", "network", "platform",
]

# Weighted down or excluded. These are what let celebrity and non-UK noise
# through in earlier versions.
EXCLUDE_TERMS = [
    "celebrity", "gossip", "red carpet", "dating", "romance", "feud",
    "reality star", "love island contestant", "strictly star", "spotted",
    "wedding", "baby news", "recipe", "horoscope",
]

# Technology. Aimed at the tech companies a market research team actually
# covers, rather than developer tooling or gadget-review noise.
TECH_TERMS = [
    "samsung", "apple", "google", "alphabet", "meta", "microsoft", "amazon",
    "netflix", "spotify", "tiktok", "bytedance", "openai", "anthropic",
    "nvidia", "qualcomm", "intel", "sony", "lg electronics", "huawei",
    "xiaomi", "oneplus", "motorola", "lenovo",
    "smartphone", "wearable", "smart tv", "connected tv", "set-top",
    "streaming device", "voice assistant", "smart speaker", "tablet",
    "artificial intelligence", "generative ai", "large language model",
    "cloud computing", "data centre", "data center", "semiconductor",
    "chipmaker", "operating system", "app store", "digital advertising",
    "adtech", "ad tech", "programmatic", "first-party data",
    "subscriber growth", "device sales", "handset", "5g", "broadband",
]

# Americas media and tech organisations. These score like UK primaries,
# because a Comcast or CBC story matters as much to the US and Canadian
# half of the team as an ITV story does to the UK half.
AMERICAS_ORGS = [
    "nbc", "cbs", "abc network", "fox news", "fox corporation", "cnn",
    "msnbc", "pbs", "npr", "hbo", "warner bros", "warner bros discovery",
    "paramount", "disney", "comcast", "nbcuniversal", "peacock", "hulu",
    "roku", "sinclair", "nexstar", "tegna", "gray television", "amc networks",
    "cbc", "radio-canada", "bell media", "rogers communications", "corus",
    "quebecor", "telus", "shaw", "crave", "cineplex",
    "crtc", "fcc", "nielsen", "comscore",
]

# Places and events that indicate the Americas but are not organisations,
# so they help decide the region without inflating the relevance score.
AMERICAS_GEO = [
    "united states", "america", "american", "canada", "canadian",
    "toronto", "vancouver", "montreal", "ottawa", "quebec",
    "new york", "los angeles", "hollywood", "silicon valley",
    "super bowl", "upfronts",
]
AMERICAS_TERMS = AMERICAS_ORGS + AMERICAS_GEO

# Signals that a story is British. Used to pull items from mixed feeds back
# into the UK bucket when they are really about a UK business.
UK_SIGNAL_TERMS = [
    "uk", "britain", "british", "england", "scotland", "wales",
    "northern ireland", "london", "manchester", "salford", "ofcom",
    "bbc", "itv", "channel 4", "sky", "bauer", "global radio", "rajar",
    "barb", "licence fee",
]

# Regions that are genuinely out of scope for this team.
OUT_OF_SCOPE_TERMS = [
    "australia", "australian", "new zealand", "india", "brazil",
    "thailand", "singapore", "uae", "saudi", "nigeria", "japan domestic",
]

REGION_LABELS = {
    "uk": "United Kingdom",
    "americas": "United States & Canada",
}
REGION_SHORT = {"uk": "UK", "americas": "Americas"}
REGION_ORDER = ["uk", "americas"]

SECTION_ORDER = [
    ("deals", "Deals & business", "wire"),
    ("results", "Results & money", "signal"),
    ("regulation", "Regulation & policy", "wire"),
    ("audience", "Audience & ratings", "signal"),
    ("tech", "Technology & AI", "slate"),
    ("radio", "Radio & audio", "slate"),
]
SECTION_LOOKUP = {sid: (label, tone) for sid, label, tone in SECTION_ORDER}

# Short tag labels used on the At a glance rundown.
TAG_LABELS = {
    "deals": "Deals",
    "results": "Results",
    "regulation": "Regulation",
    "audience": "Audience",
    "tech": "Tech",
    "radio": "Radio",
    "people": "People",
}
TAG_TONES = {
    "deals": "wire",
    "results": "signal",
    "regulation": "wire",
    "audience": "signal",
    "tech": "slate",
    "radio": "slate",
    "people": "slate",
}


def log(msg):
    print(msg, flush=True)


# ============================================================
# 2. SCHEDULE
# ============================================================

def previous_scheduled_run(now_uk):
    """
    The most recent scheduled run strictly before today. Starting from
    yesterday guarantees we never return today's own run, which would
    collapse the window to a few minutes.
    """
    candidate = (now_uk - timedelta(days=1)).replace(
        hour=SCHEDULED_HOUR, minute=0, second=0, microsecond=0
    )
    for _ in range(14):
        if candidate.weekday() in SCHEDULED_DAYS:
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError("Could not find a previous scheduled run.")


def check_clock():
    """
    The workflow fires at both 01:00 and 02:00 UTC so that one of them is
    02:00 in London whether or not BST is in effect. Whichever trigger is
    wrong exits here immediately, having done nothing.
    """
    now_uk = datetime.now(UK)
    if FORCE_RUN:
        log(f"FORCE_RUN set, skipping the clock check. UK time is {now_uk:%H:%M %Z}.")
        return now_uk
    if now_uk.hour != SCHEDULED_HOUR:
        log(f"UK time is {now_uk:%H:%M %Z}, not {SCHEDULED_HOUR:02d}:00. "
            f"This is the duplicate trigger, exiting quietly.")
        sys.exit(0)
    if now_uk.weekday() not in SCHEDULED_DAYS:
        log(f"Today is {now_uk:%A}, not a scheduled day. Exiting quietly.")
        sys.exit(0)
    return now_uk


# ============================================================
# 3. FEEDS AND RELEVANCE
# ============================================================

def clean_text(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def entry_time(entry):
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime(*parsed[:6], tzinfo=ZoneInfo("UTC")).astimezone(UK)
    return None


def count_terms(text, terms):
    hits = 0
    for term in terms:
        if " " in term:
            if term in text:
                hits += 1
        elif re.search(rf"\b{re.escape(term)}\b", text):
            hits += 1
    return hits


def classify_region(text, feed_region):
    """
    Decide whether a story belongs in the UK or Americas group.

    A feed that only ever covers one region wins outright. Mixed feeds
    (global tech sites, podcasting) are decided on the wording: an explicit
    UK signal beats an Americas signal, because a story about Samsung's UK
    launch matters more to the UK half of the team.
    """
    if feed_region in ("uk", "americas"):
        return feed_region

    uk_hits = count_terms(text, UK_SIGNAL_TERMS)
    us_hits = count_terms(text, AMERICAS_TERMS)
    if uk_hits and uk_hits >= us_hits:
        return "uk"
    if us_hits:
        return "americas"
    # Unsigned global tech story. Most of this team's tech coverage is US
    # centred, so that is the safer default.
    return "americas"


def score_article(title, summary, feed_region="global"):
    """
    Weighted relevance score plus a region. Title matches count double,
    because a term in the headline is a far stronger signal than one buried
    in a summary.
    """
    t = f" {title.lower()} "
    s = f" {summary.lower()} "
    both = t + s

    score = 0
    score += count_terms(t, PRIMARY_TERMS) * 10
    score += count_terms(s, PRIMARY_TERMS) * 5
    score += count_terms(t, AMERICAS_ORGS) * 10
    score += count_terms(s, AMERICAS_ORGS) * 5
    score += count_terms(t, TECH_TERMS) * 8
    score += count_terms(s, TECH_TERMS) * 4
    score += count_terms(t, SECONDARY_TERMS) * 4
    score += count_terms(s, SECONDARY_TERMS) * 2
    score += count_terms(t, TERTIARY_TERMS) * 2
    score += count_terms(s, TERTIARY_TERMS) * 1

    score -= count_terms(both, EXCLUDE_TERMS) * 8

    region = classify_region(both, feed_region)

    # Genuinely out-of-scope territories, unless an organisation the team
    # follows is named in the story.
    anchored = (
        count_terms(both, PRIMARY_TERMS)
        + count_terms(both, AMERICAS_ORGS)
        + count_terms(both, TECH_TERMS)
    ) > 0
    if not anchored and count_terms(both, OUT_OF_SCOPE_TERMS) > 0:
        score -= 15

    return score, region


def fetch_feed(name, url):
    """
    Some publishers sit behind bot protection that rejects a bare request.
    Sending the headers a real browser would send fixes a few of them. Where
    the block is by IP range, nothing here will help and the feed simply
    reports as dead in feed_check.py.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*",
        "Accept-Language": "en-GB,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT, headers=headers)
        response.raise_for_status()
        return feedparser.parse(response.content)
    except Exception as exc:
        log(f"  Feed failed, {name}: {exc}")
        return None


def collect_articles(window_start, now_uk):
    seen = set()
    articles = []
    feeds_ok = 0
    undated = 0

    for name, url, feed_region in FEEDS:
        parsed = fetch_feed(name, url)
        if not parsed:
            continue
        feeds_ok += 1
        taken = 0
        for entry in parsed.entries[:40]:
            published = entry_time(entry)
            # An undated entry cannot be placed in the window. Including it
            # would let a feed's whole back catalogue leak into one edition.
            if published is None:
                undated += 1
                continue
            if not (window_start <= published <= now_uk):
                continue

            title = clean_text(getattr(entry, "title", ""))
            if len(title) < 12:
                continue

            key = re.sub(r"[^a-z0-9]", "", title.lower())[:70]
            if key in seen:
                continue

            summary = clean_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            score, region = score_article(title, summary, feed_region)
            if score < MIN_RELEVANCE_SCORE:
                continue

            seen.add(key)
            if len(summary) > 320:
                summary = summary[:317].rsplit(" ", 1)[0] + "..."

            articles.append({
                "title": title,
                "summary": summary,
                "link": getattr(entry, "link", ""),
                "outlet": name,
                "published": published,
                "score": score,
                "region": region,
            })
            taken += 1
        if taken:
            log(f"  {name}: {taken} relevant")

    if undated:
        log(f"  Skipped {undated} entries with no publication date.")

    # Quota per region, so a busy US feed cannot crowd the UK out of its own
    # digest, or the other way round.
    kept = []
    for region in REGION_ORDER:
        in_region = [a for a in articles if a["region"] == region]
        in_region.sort(key=lambda a: a["score"], reverse=True)
        kept.extend(in_region[:MAX_ARTICLES_PER_REGION])

    kept.sort(key=lambda a: a["score"], reverse=True)
    for index, article in enumerate(kept, start=1):
        article["ref"] = index

    counts = {r: sum(1 for a in kept if a["region"] == r) for r in REGION_ORDER}
    log(f"{feeds_ok}/{len(FEEDS)} feeds responded. "
        f"Kept {len(kept)} articles: "
        + ", ".join(f"{REGION_SHORT[r]} {counts[r]}" for r in REGION_ORDER))
    return kept


# ============================================================
# 4. GEMINI
# ============================================================

SCHEMA_NOTE = """
Return ONLY a JSON object, no markdown fences and no preamble, exactly in
this shape:

{
  "headline": "max 12 words, the single biggest story in this region",
  "lead": "one or two sentences, max 45 words",
  "glance": [
    {
      "text": "one self-contained sentence, max 24 words",
      "section": "deals|results|regulation|audience|tech|radio|people",
      "refs": [1, 4]
    }
  ],
  "sections": [
    {
      "id": "deals|results|regulation|audience|tech|radio",
      "paragraphs": ["2 to 3 short sentences", "2 to 3 short sentences"]
    }
  ],
  "people_moves": [
    { "text": "who, what role, which organisation", "refs": [3] }
  ],
  "coming_up": [
    { "date": "23 Sep", "text": "what happens on that date", "refs": [2] }
  ]
}
"""


def build_prompt(articles, region, window_start, now_uk, corrections=None):
    listing = []
    for article in articles:
        line = f"[{article['ref']}] ({article['outlet']}) {article['title']}"
        if article["summary"]:
            line += f" :: {article['summary']}"
        listing.append(line)

    region_name = REGION_LABELS[region]
    if region == "uk":
        scope = ("British media, broadcast, radio and the technology companies "
                 "that matter to those markets")
    else:
        scope = ("United States and Canadian media, broadcast and technology")

    rules = [
        "Base the digest only on the headlines and excerpts above. Do not add "
        "figures, deals, appointments or affiliations from your own memory.",
        "Every claim must cite at least one reference number in its refs array. "
        "Never cite a number that is not in the list above.",
        "Write in British English even when covering American stories. Never use "
        "em dashes or en dashes.",
        "Plain, concrete language. Short sentences. Active voice.",
        "Only include a section in \"sections\" if you actually have material for "
        "it. An empty or padded section is worse than no section.",
        "\"coming_up\" must contain only dates explicitly stated in the articles, "
        "such as a consultation closing or results being published. Never "
        "speculate. If no dates are stated, return an empty list.",
        "Give 3 to 5 items in \"glance\", each a different story.",
        f"Everything you write must relate to {scope}. Ignore anything else, "
        "including celebrity and personal-life stories.",
        "Do not mention any employer, agency or brand as the publisher of this "
        "digest. It is an independent personal project.",
    ]

    correction_block = ""
    if corrections:
        correction_block = (
            "\nCORRECTION REQUIRED. Your previous attempt had these problems:\n"
            + "\n".join(f"- {c}" for c in corrections)
            + "\nRewrite it and do not repeat them.\n"
        )

    return (
        f"You are an experienced media and technology industry journalist writing "
        f"the {region_name} half of a twice-weekly digest covering "
        f"{window_start:%A %d %B} to {now_uk:%A %d %B %Y}. Readers work in market "
        f"research across media and technology clients.\n\n"
        f"ARTICLES:\n" + "\n".join(listing) + "\n\nRULES:\n"
        + "\n".join(f"{i}. {r}" for i, r in enumerate(rules, start=1))
        + correction_block + "\n" + SCHEMA_NOTE
    )


SYNTHESIS_SCHEMA = """
Return ONLY a JSON object, no fences, no preamble:

{
  "headline": "max 12 words, the single biggest story across both regions",
  "lead": "two sentences, max 55 words, covering both regions"
}
"""


def build_synthesis_prompt(parts, window_start, now_uk):
    blocks = []
    for region, payload in parts.items():
        bullets = "\n".join(
            f"- {item.get('text', '')}" for item in payload.get("glance", [])
        )
        blocks.append(
            f"{REGION_LABELS[region]}\n"
            f"Headline: {payload.get('headline', '')}\n"
            f"Lead: {payload.get('lead', '')}\n{bullets}"
        )

    return (
        f"Two regional digests have been written for the period "
        f"{window_start:%A %d %B} to {now_uk:%A %d %B %Y}. Write one headline and "
        f"one lead that introduce both.\n\n" + "\n\n".join(blocks) + "\n\n"
        "Rules:\n"
        "1. Use only what is above. Add no new facts.\n"
        "2. British English. No em dashes or en dashes.\n"
        "3. The headline names the single most significant development overall.\n"
        "4. The lead mentions both regions.\n"
        + SYNTHESIS_SCHEMA
    )


def strip_fences(text):
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def scan_completed_values(s):
    in_string = False
    escape = False
    stack = []
    safe_points = [0]
    for i, ch in enumerate(s):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                j = i + 1
                while j < len(s) and s[j] in " \t\n\r":
                    j += 1
                if not (j < len(s) and s[j] == ":"):
                    safe_points.append(i + 1)
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            safe_points.append(i + 1)
        elif ch == ",":
            safe_points.append(i + 1)
    return safe_points, stack, in_string


def close_truncated(text):
    """
    Repair JSON cut off mid-generation: cut back to the last complete value,
    drop any dangling key, then close whatever is still open.
    """
    safe_points, stack, in_string = scan_completed_values(text)
    if not stack and not in_string:
        return text

    cut = safe_points[-1] if safe_points else 0
    truncated = re.sub(r",\s*$", "", text[:cut].rstrip())

    stack = []
    in_string = False
    escape = False
    for ch in truncated:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()

    closers = {"{": "}", "[": "]"}
    return truncated + "".join(closers[c] for c in reversed(stack))


def parse_json_response(text):
    candidates = [text.strip()]
    fenced = strip_fences(text.strip())
    if fenced != candidates[0]:
        candidates.append(fenced)
    match = re.search(r"\{.*\}", fenced, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    last_error = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc

    for candidate in reversed(candidates):
        repaired = close_truncated(candidate)
        if repaired == candidate:
            continue
        try:
            result = json.loads(repaired)
            log("Recovered a truncated JSON response. The digest may be missing "
                "its last item.")
            return result
        except json.JSONDecodeError:
            continue

    raise last_error


def call_gemini(model, prompt, config):
    url = f"{GEMINI_BASE}/{model}:generateContent"
    working = dict(config)
    dropped_json_mode = False

    for attempt in range(2):
        response = requests.post(
            url,
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": GEMINI_API_KEY},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": working},
            timeout=180,
        )
        body = response.json()

        if "error" in body:
            code = body["error"].get("code")
            message = body["error"].get("message", "")
            if (code == 400 and "responseMimeType" in working
                    and not dropped_json_mode
                    and ("mime" in message.lower() or "json" in message.lower())):
                log(f"  {model}: JSON mode rejected, retrying without it.")
                working.pop("responseMimeType", None)
                dropped_json_mode = True
                continue
            error = RuntimeError(f"{model}: {code} {message}")
            error.retryable = code in (404, 429, 500, 503)
            raise error

        candidates = body.get("candidates") or []
        if not candidates:
            error = RuntimeError(f"{model}: empty response, possibly blocked")
            error.retryable = True
            raise error

        if candidates[0].get("finishReason") == "MAX_TOKENS":
            log(f"  {model}: hit the token limit, output may be truncated.")
        return candidates[0]

    raise RuntimeError(f"{model}: exhausted retries")


def candidate_text(candidate):
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts).strip()


def call_models(prompt, config):
    """Try each model in turn until one answers."""
    errors = []
    for model in TEXT_MODELS:
        try:
            candidate = call_gemini(model, prompt, config)
            return parse_json_response(candidate_text(candidate)), model
        except Exception as exc:
            log(f"  {exc}")
            errors.append(str(exc))
            if not getattr(exc, "retryable", True):
                raise
    raise RuntimeError("Every model failed:\n" + "\n".join(errors))


def generate_digest(articles, window_start, now_uk):
    """
    Write the digest, one Gemini call per region.

    Splitting by region keeps each prompt focused and well under the token
    limit, which matters now the feed list covers two continents and two
    subject areas. When only one region has material, the extra synthesis
    call is skipped and its own headline is used.
    """
    by_region = {
        region: [a for a in articles if a["region"] == region]
        for region in REGION_ORDER
    }
    by_region = {r: items for r, items in by_region.items() if len(items) >= 2}

    if not by_region:
        raise RuntimeError("No region had enough articles to write about.")

    config = {
        "temperature": 0.3,
        "maxOutputTokens": 5120,
        "responseMimeType": "application/json",
    }

    total = sum(len(v) for v in by_region.values())
    log(f"Writing {len(by_region)} region(s), {total} articles, "
        f"{'one call each' if len(by_region) > 1 else 'single call'}.")

    parts = {}
    models_used = []
    for region, items in by_region.items():
        prompt = build_prompt(items, region, window_start, now_uk)
        payload, model = call_models(prompt, config)
        parts[region] = payload
        models_used.append(model)
        log(f"  {REGION_SHORT[region]}: {len(payload.get('glance', []))} glance items, "
            f"{len(payload.get('sections', []))} sections, via {model}")

    # Overall headline and lead
    if len(parts) > 1:
        try:
            synth, model = call_models(
                build_synthesis_prompt(parts, window_start, now_uk),
                {"temperature": 0.3, "maxOutputTokens": 512,
                 "responseMimeType": "application/json"},
            )
            headline = synth.get("headline") or ""
            lead = synth.get("lead") or ""
            models_used.append(model)
        except Exception as exc:
            log(f"  Synthesis call failed ({exc}), falling back to the busiest region.")
            headline, lead = "", ""
    else:
        headline, lead = "", ""

    if not headline:
        busiest = max(by_region, key=lambda r: len(by_region[r]))
        headline = parts[busiest].get("headline") or "Media and technology round-up"
        lead = lead or parts[busiest].get("lead", "")

    # Merge the regional parts into one digest, keeping region on each piece
    # so the page can group them under regional headings.
    merged = {
        "headline": headline,
        "lead": lead,
        "glance": [],
        "sections": [],
        "people_moves": [],
        "coming_up": [],
    }
    for region in REGION_ORDER:
        payload = parts.get(region)
        if not payload:
            continue
        for item in payload.get("glance", []):
            item["region"] = region
            merged["glance"].append(item)
        for section in payload.get("sections", []):
            section["region"] = region
            merged["sections"].append(section)
        for item in payload.get("people_moves", []):
            item["region"] = region
            merged["people_moves"].append(item)
        for item in payload.get("coming_up", []):
            item["region"] = region
            merged["coming_up"].append(item)

    return merged, ", ".join(sorted(set(models_used)))


# ============================================================
# 5. CITATION INTEGRITY
# ============================================================

def tidy_citations(digest, articles):
    """
    Keep only references that exist, renumber them in order of first
    appearance, and drop any article that ends up uncited. This is what
    stops the orphan references the earlier version produced.
    """
    valid = {a["ref"] for a in articles}
    by_old_ref = {a["ref"]: a for a in articles}

    order = []

    def clean(refs):
        out = []
        for ref in refs or []:
            try:
                ref = int(ref)
            except (TypeError, ValueError):
                continue
            if ref in valid and ref not in out:
                out.append(ref)
                if ref not in order:
                    order.append(ref)
        return out

    for item in digest.get("glance", []):
        item["refs"] = clean(item.get("refs"))
    for item in digest.get("people_moves", []):
        item["refs"] = clean(item.get("refs"))
    for item in digest.get("coming_up", []):
        item["refs"] = clean(item.get("refs"))

    # Inline [n] markers inside section paragraphs.
    for section in digest.get("sections", []):
        for paragraph in section.get("paragraphs", []):
            for found in re.findall(r"\[(\d+)\]", paragraph):
                ref = int(found)
                if ref in valid and ref not in order:
                    order.append(ref)

    renumber = {old: new for new, old in enumerate(order, start=1)}

    def remap(refs):
        return [renumber[r] for r in refs if r in renumber]

    for key in ("glance", "people_moves", "coming_up"):
        for item in digest.get(key, []):
            item["refs"] = remap(item["refs"])

    def remap_inline(text):
        def swap(match):
            ref = int(match.group(1))
            return f"[{renumber[ref]}]" if ref in renumber else ""
        return re.sub(r"\[(\d+)\]", swap, text)

    for section in digest.get("sections", []):
        section["paragraphs"] = [
            re.sub(r"\s+([.,;])", r"\1", remap_inline(p)).strip()
            for p in section.get("paragraphs", [])
        ]

    cited = [dict(by_old_ref[old], ref=new) for old, new in renumber.items()]
    cited.sort(key=lambda a: a["ref"])

    dropped = len(articles) - len(cited)
    if dropped > 0:
        log(f"Dropped {dropped} uncited articles from the source list.")
    return digest, cited


# ============================================================
# 6. RENDERING
# ============================================================

def esc(value):
    return html.escape(str(value or ""), quote=True)


def link_refs(refs):
    if not refs:
        return ""
    links = "".join(
        f'<a href="#ref{r}">[{r}]</a>' for r in refs
    )
    return f" <span class=\"cites\">{links}</span>"


def inline_refs(text):
    def swap(match):
        ref = match.group(1)
        return f'<a href="#ref{ref}">[{ref}]</a>'
    return re.sub(r"\[(\d+)\]", swap, esc(text))


def render_edition(digest, articles, meta):
    glance_rows = []
    for index, item in enumerate(digest.get("glance", []), start=1):
        section = (item.get("section") or "deals").lower()
        label = TAG_LABELS.get(section, "News")
        tone = TAG_TONES.get(section, "slate")
        region = item.get("region")
        region_chip = ""
        if region in REGION_SHORT:
            region_chip = f'<span class="tag region">{esc(REGION_SHORT[region])}</span>'
        glance_rows.append(f"""
        <div class="rundown-item">
          <span class="rundown-num">{index:02d}</span>
          <div>{region_chip}<span class="tag {tone}">{esc(label)}</span>{esc(item.get('text', ''))}{link_refs(item.get('refs'))}</div>
        </div>""")

    # Everything below the rundown is grouped by region, then by theme.
    regions_present = [
        r for r in REGION_ORDER
        if any(s.get("region") == r for s in digest.get("sections", []))
        or any(p.get("region") == r for p in digest.get("people_moves", []))
        or any(c.get("region") == r for c in digest.get("coming_up", []))
    ]
    show_region_headings = len(regions_present) > 1

    blocks = []
    for region in regions_present:
        region_blocks = []

        provided = {
            s.get("id"): s for s in digest.get("sections", [])
            if s.get("region") == region
        }
        for sid, label, _tone in SECTION_ORDER:
            section = provided.get(sid)
            if not section:
                continue
            paragraphs = [p for p in section.get("paragraphs", []) if p.strip()]
            if not paragraphs:
                continue
            body = "".join(f"<p>{inline_refs(p)}</p>" for p in paragraphs)
            region_blocks.append(f"""
    <section class="block">
      <div class="block-title">{esc(label)}</div>
      {body}
    </section>""")

        people = [p for p in digest.get("people_moves", [])
                  if p.get("region") == region]
        if people:
            pieces = []
            for index, person in enumerate(people):
                first_class = ' class="first"' if index == 0 else ""
                pieces.append(
                    "<li" + first_class + ">" + esc(person.get("text", ""))
                    + link_refs(person.get("refs")) + "</li>"
                )
            region_blocks.append(f"""
    <section class="block">
      <div class="block-title">People moves</div>
      <ul class="people-list">{''.join(pieces)}</ul>
    </section>""")

        coming = [c for c in digest.get("coming_up", [])
                  if c.get("region") == region]
        if coming:
            pieces = "".join(
                f'<li><span class="coming-up-date">{esc(c.get("date", ""))}</span>'
                f'<span>{esc(c.get("text", ""))}{link_refs(c.get("refs"))}</span></li>'
                for c in coming
            )
            region_blocks.append(f"""
    <section class="block">
      <div class="block-title">Coming up</div>
      <ul class="coming-up-list">{pieces}</ul>
    </section>""")

        if not region_blocks:
            continue
        if show_region_headings:
            blocks.append(
                f'\n    <div class="region-head">{esc(REGION_LABELS[region])}</div>'
            )
        blocks.extend(region_blocks)

    sources = "".join(
        f'<li id="ref{a["ref"]}"><a href="{esc(a["link"])}" rel="noopener">'
        f'{esc(a["title"])}</a><br><span class="source-outlet">{esc(a["outlet"])}'
        f' &middot; {esc(REGION_SHORT.get(a.get("region", ""), ""))}</span></li>'
        for a in articles
    )

    values = {
        "TITLE": esc(meta["headline"]),
        "SITE_TITLE": esc(SITE_TITLE),
        "SITE_SUBTITLE": esc(SITE_SUBTITLE),
        "SITE_SUBTITLE_SHORT": esc(SITE_SUBTITLE_SHORT),
        "ROOT": meta["root"],
        "STATUS_LABEL": meta["status_label"],
        "TALLY_CLASS": meta["tally_class"],
        "HEADLINE": esc(meta["headline"]),
        "DATE_LINE": esc(meta["date_line"]),
        "LEAD": esc(digest.get("lead", "")),
        "GLANCE": "".join(glance_rows),
        "SECTIONS": "".join(blocks),
        "PEOPLE": "",
        "COMING": "",
        "SOURCES": sources,
        "FOOTER_NOTE": esc(meta["footer_note"]),
        "CURRENT_DATE": esc(meta["iso"]),
    }
    page = PAGE_TEMPLATE
    for key, value in values.items():
        page = page.replace(f"%%{key}%%", value)

    remaining = re.findall(r"%%[A-Z_]+%%", page)
    if remaining:
        raise RuntimeError(f"Template placeholders left unfilled: {set(remaining)}")
    return page


# ============================================================
# 7. MAIN
# ============================================================

def load_manifest(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except Exception as exc:
            log(f"Manifest unreadable ({exc}), starting a fresh one.")
    return []


def build_payload(digest, cited, iso, date_line, headline, footer_note,
                  model, backfilled=False):
    """
    Everything needed to re-render this edition later, saved alongside the
    HTML. Without it, a change to the look of the site could only ever
    apply to future editions, because the rendered page is the only record
    of what was written.
    """
    return {
        "schema": 1,
        "iso": iso,
        "headline": headline,
        "date_line": date_line,
        "footer_note": footer_note,
        "model": model,
        "backfilled": backfilled,
        "generated_at": datetime.now(UK).isoformat(timespec="seconds"),
        "digest": digest,
        # Only the fields the page actually renders. Datetimes are dropped
        # because they are not JSON friendly and are not needed again.
        "articles": [
            {
                "ref": a["ref"],
                "title": a["title"],
                "link": a["link"],
                "outlet": a["outlet"],
                "region": a.get("region", ""),
            }
            for a in cited
        ],
    }


def save_payload(payload, output_dir=None):
    output_dir = output_dir or OUTPUT_DIR
    data_dir = os.path.join(output_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, f"{payload['iso']}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return path


def render_payload(payload, root, status_label, tally_class):
    return render_edition(payload["digest"], payload["articles"], {
        "root": root,
        "iso": payload["iso"],
        "headline": payload["headline"],
        "date_line": payload["date_line"],
        "status_label": status_label,
        "tally_class": tally_class,
        "footer_note": payload["footer_note"],
    })


def write_edition_html(payload, output_dir=None, is_current=False):
    """Writes editions/<iso>.html, and index.html too when it is the newest."""
    output_dir = output_dir or OUTPUT_DIR
    editions_dir = os.path.join(output_dir, "editions")
    os.makedirs(editions_dir, exist_ok=True)

    edition_path = os.path.join(editions_dir, f"{payload['iso']}.html")
    with open(edition_path, "w", encoding="utf-8") as handle:
        handle.write(render_payload(payload, "../", "Archived edition", ""))

    if is_current:
        index_path = os.path.join(output_dir, "index.html")
        with open(index_path, "w", encoding="utf-8") as handle:
            handle.write(render_payload(payload, "", "Current edition", "live"))

    return edition_path


def manifest_entry(payload):
    """Sidebar entry, derived from the payload so it can always be rebuilt."""
    stamp = datetime.strptime(payload["iso"], "%Y-%m-%d")
    teaser = payload["digest"].get("lead", "")
    if len(teaser) > 90:
        teaser = teaser[:87].rsplit(" ", 1)[0] + "..."
    return {
        "iso": payload["iso"],
        "label": f"{stamp:%a %d %b}",
        "month": f"{stamp:%B %Y}",
        "headline": payload["headline"],
        "teaser": teaser,
        "path": f"editions/{payload['iso']}.html",
    }


def save_manifest(entries, output_dir=None):
    output_dir = output_dir or OUTPUT_DIR
    entries.sort(key=lambda e: e["iso"], reverse=True)
    path = os.path.join(output_dir, "manifest.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2, ensure_ascii=False)
    return path


def main():
    if not GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY is not set.")

    now_uk = check_clock()
    window_start = previous_scheduled_run(now_uk)
    log(f"Building the edition for {now_uk:%A %d %B %Y}.")
    log(f"Covering {window_start:%a %d %b %H:%M} to {now_uk:%a %d %b %H:%M}.")

    articles = collect_articles(window_start, now_uk)
    if len(articles) < 3:
        log("Too few relevant articles to build a digest. Exiting without "
            "publishing, so the last edition stays up.")
        return

    digest, model = generate_digest(articles, window_start, now_uk)
    digest, cited = tidy_citations(digest, articles)

    if not cited:
        log("The digest cited no valid sources. Refusing to publish.")
        return

    iso = f"{now_uk:%Y-%m-%d}"
    date_line = (
        f"{now_uk:%A %d %B %Y} \u00b7 covering "
        f"{window_start:%a %d %b} to {now_uk - timedelta(days=1):%a %d %b}"
    )
    headline = digest.get("headline") or "Media and technology round-up"
    footer_note = (
        f"Built from {len(cited)} cited articles across "
        f"{len({a['outlet'] for a in cited})} outlets. Written by {model}."
    )

    payload = build_payload(digest, cited, iso, date_line, headline,
                            footer_note, model)
    data_path = save_payload(payload)
    edition_path = write_edition_html(payload, is_current=True)

    manifest_path = os.path.join(OUTPUT_DIR, "manifest.json")
    manifest = [e for e in load_manifest(manifest_path) if e.get("iso") != iso]
    manifest.append(manifest_entry(payload))
    save_manifest(manifest)

    log(f"Published {iso}: {headline}")
    log(f"  {edition_path}")
    log(f"  {data_path}")
    log(f"  {len(manifest)} editions in the manifest.")


if __name__ == "__main__":
    main()
