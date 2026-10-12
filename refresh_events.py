#!/usr/bin/env python3
"""Weekly refresh of the Mount Maunganui events list for the Pacific Apartments signage.

Reads the public event listing on bayofplentynz.com (all pages), keeps the events held in
Mount Maunganui, tidies the wording, and writes events.json. Standard library only.

If anything looks wrong (site down, too few events) this exits with an error WITHOUT touching
events.json, so the screens keep showing the previous good list.
"""
import html
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = "https://bayofplentynz.com"
LISTING = BASE + "/filter-events/listing?page={}"
UA = {"User-Agent": "Mozilla/5.0 (PacificApartmentsSignage weekly refresh)"}
MAX_PAGES = 30
MAX_EVENTS = 30
LOOKAHEAD_DAYS = 200
MIN_EVENTS = 6

MONTHS = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}

try:
    from zoneinfo import ZoneInfo
    NZ = ZoneInfo("Pacific/Auckland")
except Exception:  # no tz database available
    NZ = timezone(timedelta(hours=13))


def get(url, tries=3):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 + 3 * n)
    raise RuntimeError(f"could not fetch {url}: {last}")


def unescape_all(text):
    for _ in range(4):
        t = html.unescape(text)
        if t == text:
            break
        text = t
    return text


def clean(text):
    text = unescape_all(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"[𐀀-􏿿☀-➿️‍]", "", text)  # emoji
    text = re.sub(r"\?{2,}", " ", text)  # emoji that arrived as question marks
    text = text.replace(" ", " ").replace("™", "").replace("®", "")
    return re.sub(r"\s+", " ", text).strip()


# Sentences containing any of these are never shown on the lobby screens.
SENSITIVE = re.compile(r"tragic|tragedy|died|death|deaths|killed|landslide|mauao in february|memorial|fatal", re.I)
PROMO_START = re.compile(r"^(get ready|calling all|tickets?|book now|don.t miss|join us at your local|looking for)", re.I)


def shorten(text, limit=170):
    text = re.sub(r"\s*(\.\.\.|…)\s*$", "", clean(text))
    if "|" in text or "•" in text:
        return ""  # a flyer-style blurb, not a sentence - caller falls back
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if x.strip()]
    sentences = [x for x in sentences if not SENSITIVE.search(x)]
    good = [x for x in sentences if not PROMO_START.match(x)] or sentences
    out = ""
    for x in good:
        if len(out) + len(x) + 1 > limit:
            break
        out = (out + " " + x).strip()
    if not out and good:
        cut = good[0][:limit]
        out = cut[: cut.rfind(" ")].rstrip(",;:- ") + "…"
    return out


def by_substring(table, title):
    t = title.lower()
    for key, value in table.items():
        if key.lower() in t:
            return value
    return None


def today_nz():
    return datetime.now(NZ).date()


def parse_when(raw, today):
    """'Sat 17 Oct, 12:00pm - 4:00pm' -> (iso_start, display)"""
    raw = clean(raw).lstrip("● ").strip()
    m = re.match(r"(?:(\w{3})\s+)?(\d{1,2})\s+([A-Za-z]{3})(?:\s+(\d{4}))?(?:,\s*(.*))?$", raw)
    if not m:
        return None
    dow, day, mon, year, times = m.groups()
    mon_n = MONTHS.get(mon.title())
    if not mon_n:
        return None
    if year:
        y = int(year)
    else:
        y = today.year
        if date(y, mon_n, int(day)) < today - timedelta(days=60):
            y += 1
    d = date(y, mon_n, int(day))
    def t(s):
        s = s.strip().lower()
        return re.sub(r":00(?=[ap]m)", "", s)
    span = ""
    if times:
        parts = [t(p) for p in re.split(r"\s*-\s*", times)]
        span = "–".join(parts[:2])
    label = f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
    if y != today.year:
        label += f" {y}"
    if span:
        label += f" · {span}"
    return d.isoformat(), label


def parse_listing(page_html):
    out = []
    for a in re.findall(r"<article.*?</article>", page_html, re.S):
        def g(p):
            m = re.search(p, a, re.S)
            return m.group(1) if m else ""
        title = clean(g(r"<h3[^>]*>(.*?)</h3>"))
        strongs = [clean(s) for s in re.findall(r"<strong>(.*?)</strong>", a, re.S)]
        link = g(r'href="(https://bayofplentynz.com/event/[^"]+)"')
        if not title or not link or not strongs:
            continue
        out.append({
            "title": title,
            "when_raw": strongs[0],
            "venue_raw": strongs[1] if len(strongs) > 1 else "",
            "desc": clean(g(r'<div class="description">(.*?)</div>')),
            "image": g(r'<source media="\(min-width: 600px\)" srcset="([^"]+)"'),
            "link": link,
        })
    return out


def load_curation():
    p = ROOT / "curation.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def in_mount(venue):
    if re.search(r"papamoa|pāpāmoa", venue, re.I):
        return False
    return bool(re.search(r"maunganui|mt maunganui|totara st|truman l|kawaka|jean batten|marine parade|bay oval|blake park|domain road", venue, re.I))


def friendly_venue(venue_raw, cur):
    for key, name in cur.get("venue_names", {}).items():
        if key.lower() in venue_raw.lower():
            return name
    v = re.sub(r",?\s*(Mt|Mount) Maunganui.*$", "", venue_raw, flags=re.I)
    v = re.sub(r",?\s*(Tauranga|Bay of Plenty).*$", "", v, flags=re.I)
    v = re.sub(r"^\d+[a-z]?(/\d+)?\s+", "", v).strip(" ,")
    return f"{v}, Mount Maunganui" if v else "Mount Maunganui"


def main():
    cur = load_curation()
    today = today_nz()
    horizon = today + timedelta(days=LOOKAHEAD_DAYS)

    raw = []
    for n in range(1, MAX_PAGES + 1):
        items = parse_listing(get(LISTING.format(n)))
        if not items:
            break
        raw.extend(items)
    print(f"read {len(raw)} events from the site")
    if len(raw) < 20:
        sys.exit("too few events read from the site - keeping the previous list")

    excl = [s.lower() for s in cur.get("exclude_title_contains", [])]
    chosen, seen = [], set()
    for e in raw:
        up = e["title"].upper()
        if "CANCEL" in up or "POSTPONED" in up:
            continue
        if any(x in e["title"].lower() for x in excl):
            continue
        if not in_mount(e["venue_raw"] + " " + e["title"]):
            continue
        w = parse_when(e["when_raw"], today)
        if not w:
            continue
        iso, label = w
        d = date.fromisoformat(iso)
        if d < today or d > horizon:
            continue
        key = (e["title"].lower(), iso)
        if key in seen:
            continue
        seen.add(key)
        e.update(start=iso, when=label)
        chosen.append(e)

    chosen.sort(key=lambda e: e["start"])
    # one entry per recurring series (e.g. monthly markets): keep the soonest only
    first_of_series, unique = set(), []
    for e in chosen:
        series = re.sub(r"\W+", " ", e["title"].lower()).strip()
        if series in first_of_series:
            continue
        first_of_series.add(series)
        unique.append(e)
    chosen = unique[:MAX_EVENTS]

    pins = [p.lower() for p in cur.get("featured_title_contains", [])]
    events = []
    for e in chosen:
        try:
            page = get(e["link"], tries=2)
            m = re.search(r'property="og:description" content="([^"]*)"', page)
            desc = shorten(m.group(1)) if m else ""
        except Exception:  # noqa: BLE001
            desc = ""
        if len(desc) < 40:
            desc = shorten(e["desc"])
        title = by_substring(cur.get("titles", {}), e["title"]) or e["title"]
        title = clean(title)
        title = re.sub(r"\s*(?:[|•–-]|I)\s*(Mount|Mt\.?) Maunganui\s*$", "", title, flags=re.I)
        venue = by_substring(cur.get("venues", {}), e["title"]) or friendly_venue(e["venue_raw"], cur)
        desc = by_substring(cur.get("descriptions", {}), e["title"]) or desc
        if len(desc) < 40:
            desc = f"On at {venue.replace(', Mount Maunganui', '')} in Mount Maunganui."
        events.append({
            "title": title[:70],
            "start": e["start"],
            "when": e["when"],
            "venue": venue,
            "desc": desc,
            "image": e["image"],
            "featured": any(p in e["title"].lower() for p in pins),
        })

    if len(events) < MIN_EVENTS:
        sys.exit(f"only {len(events)} Mount Maunganui events found - keeping the previous list")
    if not any(e["featured"] for e in events):
        events[min(2, len(events) - 1)]["featured"] = True

    (ROOT / "events.json").write_text(
        json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source": BASE + "/events/", "events": events}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (ROOT / "last_checked.txt").write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n")
    print(f"wrote {len(events)} events")
    for e in events:
        print(" ", e["start"], e["title"], "|", e["venue"])


if __name__ == "__main__":
    main()
