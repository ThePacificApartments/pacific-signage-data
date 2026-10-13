#!/usr/bin/env python3
"""Weekly refresh of the Mount Maunganui experiences for the Pacific Apartments signage.

You choose WHICH activities appear (the "experiences" list in curation.json). Each week this
script re-reads every one of those listings on bayofplentynz.com and updates its price, hours,
months of operation and photo. A listing is dropped automatically if it:
  - shows a "currently closed" style notice, or
  - has no photo.
It comes back by itself when the notice goes. If too few listings survive, experiences.json is
left untouched so the screens keep the last good list.
"""
import json
import re
import sys
import time
from datetime import datetime, timezone

from refresh_events import ROOT, clean, get, shorten

BASE = "https://bayofplentynz.com/explore/"
MIN_ITEMS = 6
CLOSED = re.compile(
    r"(currently|temporarily|permanently|now)\s+closed|closed\s+(due to|until|for the|while)|"
    r"is\s+closed\s+(due|until|while)|no longer (operating|trading)", re.I)
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def page_text(page_html):
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", page_html, flags=re.S)
    return clean(body)


def meta(page_html, prop):
    m = re.search(r'property="%s" content="([^"]*)"' % re.escape(prop), page_html)
    return clean(m.group(1)) if m else ""


def sized_image(url):
    if "images-bop.b-cdn.net" in url:
        return url.split("?")[0] + "?params=rs,s:900x700,q:80,g:auto,o:auto"
    return url


def parse_months(text):
    m = re.search(r"Months of Operation:\s*([^.]{0,120}?)\s*(?:Facilities|Proximity|Parking|Opening|$)", text, re.I)
    if not m:
        return None
    chunk = m.group(1).lower()
    if "all months" in chunk or "year" in chunk:
        return None
    found = [i + 1 for i, name in enumerate(MONTHS) if re.search(r"\b%s" % name, chunk)]
    return found or None


def parse_hours(text):
    m = re.search(r"Business Hours:\s*(.*?)\s*(?:Months of Operation|Facilities|Proximity|Parking|Follow Us|$)", text)
    if not m:
        return ""
    h = m.group(1).strip()
    h = re.sub(r",?\s*7 Days", "", h)
    h = re.sub(r"\s*-\s*", "–", h)
    h = re.sub(r"\s+", " ", h).strip(" ,")
    return ("Daily · " + h) if h and "mon" not in h.lower() and "sun" not in h.lower() else h


def main():
    cur = json.loads((ROOT / "curation.json").read_text(encoding="utf-8"))
    pinned = cur.get("experiences", [])
    if not pinned:
        sys.exit("no experiences listed in curation.json")

    out, skipped = [], []
    for item in pinned:
        slug = item["slug"]
        try:
            page = get(BASE + slug, tries=3)
        except Exception as e:  # noqa: BLE001
            skipped.append((slug, f"could not load ({e})"))
            continue
        text = page_text(page)
        og_desc = meta(page, "og:description")
        if CLOSED.search(og_desc) or CLOSED.search(text[:4000]):
            skipped.append((slug, "shows a closed notice"))
            continue
        image = meta(page, "og:image")
        if not image or "default" in image.lower() and "logo" in image.lower():
            skipped.append((slug, "no photo"))
            continue
        price = ""
        pm = re.search(r"Prices?(?:\s+from)?:?\s*(.{0,70})", text)
        figures = []
        if pm:
            seg = pm.group(1)
            adult = re.search(r"Adults?\s*\$\s*([\d,]+(?:\.\d+)?)", seg)
            if adult:
                figures = [float(adult.group(1).replace(",", ""))]
            else:
                figures = [float(x.replace(",", "")) for x in re.findall(r"\$\s*([\d,]+(?:\.\d+)?)", seg)][:1]
        if figures:
            low = figures[0]
            price = "From $" + (str(int(low)) if low == int(low) else f"{low:.2f}")
        title = item.get("title") or re.sub(r"\s*\|.*$", "", meta(page, "og:title")) or slug
        desc = item.get("desc") or shorten(og_desc, 190)
        if len(desc) < 30:
            skipped.append((slug, "no usable description"))
            continue
        months = item.get("months") or parse_months(text)
        entry = {
            "tag": item.get("tag", ""),
            "title": title[:60],
            "desc": desc,
            "price": item.get("price") or price,
            "note": item.get("note") or parse_hours(text),
            "image": sized_image(image),
        }
        if months:
            entry["months"] = months
        out.append(entry)
        time.sleep(0.5)

    for slug, why in skipped:
        print(f"  left out: {slug} - {why}")
    if len(out) < MIN_ITEMS:
        sys.exit(f"only {len(out)} experiences usable - keeping the previous list")

    (ROOT / "experiences.json").write_text(
        json.dumps({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source": "https://bayofplentynz.com/experiences/", "experiences": out},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(out)} experiences")
    for e in out:
        print(" ", e["title"], "|", e["price"], "|", e["note"], "|", e.get("months", "all year"))


if __name__ == "__main__":
    main()
