"""Build a one-day podcast playlist from several shows and write it as feed.xml.

Rules (set in shows.json):
  mode "daily" -> newest unplayed episode goes in its slot; other unplayed
                  episodes from recent days are added to the END of the day
                  if they fit inside drive_minutes.
  mode "queue" -> oldest unplayed episode goes in its slot (one per day),
                  so a backlog spreads out over following days.
"Unplayed" means "not yet placed in a previous day's playlist" (state.json).
"""
import datetime as dt
import html
import json
import os
import sys
from zoneinfo import ZoneInfo

import feedparser

TZ = ZoneInfo("America/Toronto")
CONFIG_PATH = "shows.json"
STATE_PATH = "state.json"
FEED_PATH = "feed.xml"

cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
if os.path.exists(STATE_PATH):
    state = json.load(open(STATE_PATH, encoding="utf-8"))
else:
    state = {"used": {}, "items": []}

now = dt.datetime.now(TZ)
today = now.date()
today_s = today.isoformat()
DEFAULT_SECS = cfg.get("default_episode_minutes", 30) * 60


def parse_duration(entry):
    raw = str(entry.get("itunes_duration") or "").strip()
    if not raw:
        return DEFAULT_SECS
    try:
        parts = [int(float(p)) for p in raw.split(":")]
    except ValueError:
        return DEFAULT_SECS
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def fetch_episodes(show):
    """Return episodes newest-first, each as a plain dict."""
    parsed = feedparser.parse(show["feed"])
    eps = []
    for e in parsed.entries:
        if not e.get("enclosures"):
            continue
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        if not stamp:
            continue
        enc = e.enclosures[0]
        eps.append({
            "show": show["name"],
            "guid": e.get("id") or enc.get("href"),
            "title": e.get("title", "(untitled)"),
            "url": enc.get("href"),
            "length": enc.get("length") or "0",
            "type": enc.get("type") or "audio/mpeg",
            "published": dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc).isoformat(),
            "secs": parse_duration(e),
            "link": e.get("link", ""),
        })
    eps.sort(key=lambda x: x["published"], reverse=True)
    return eps


def build_today():
    picks, leftovers = [], []
    for show in cfg["shows"]:
        used = set(state["used"].setdefault(show["name"], []))
        cutoff = (now - dt.timedelta(days=show.get("lookback_days", 7))).isoformat()
        try:
            fresh = [ep for ep in fetch_episodes(show)
                     if ep["guid"] not in used and ep["published"] >= cutoff]
        except Exception as exc:  # one broken feed shouldn't kill the day
            print(f"WARNING {show['name']}: {exc}", file=sys.stderr)
            continue
        if not fresh:
            print(f"{show['name']}: nothing new")
            continue
        if show.get("mode") == "daily":
            picks.append(fresh[0])
            leftovers.extend(fresh[1:])          # newest missed day first
        else:
            picks.append(fresh[-1])              # oldest unplayed first

    budget = cfg.get("drive_minutes", 60) * 60
    total = sum(ep["secs"] for ep in picks)
    for ep in leftovers:
        if total + ep["secs"] <= budget:
            picks.append(ep)
            total += ep["secs"]

    base = dt.datetime.combine(today, dt.time(5, 0), tzinfo=TZ)
    for i, ep in enumerate(picks):
        ep["day"] = today_s
        ep["slot"] = i + 1
        ep["of"] = len(picks)
        ep["pubdate"] = (base - dt.timedelta(minutes=i)).isoformat()
        state["items"].append(ep)
        state["used"][ep["show"]].append(ep["guid"])
        print(f"{i + 1}. {ep['show']} - {ep['title']} ({ep['secs'] // 60} min)")
    print(f"Total {total // 60} min of {budget // 60}")

    keep_from = (today - dt.timedelta(days=cfg.get("keep_days", 7))).isoformat()
    state["items"] = [it for it in state["items"] if it["day"] >= keep_from]
    for name in state["used"]:
        state["used"][name] = state["used"][name][-300:]


def rfc822(iso):
    return dt.datetime.fromisoformat(iso).strftime("%a, %d %b %Y %H:%M:%S %z")


def write_feed():
    items = sorted(state["items"], key=lambda x: x["pubdate"], reverse=True)
    esc = html.escape
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">',
        "<channel>",
        f"<title>{esc(cfg['feed_title'])}</title>",
        f"<link>{esc(cfg['feed_url'])}</link>",
        "<language>en-ca</language>",
        "<description>Daily drive playlist, rebuilt every morning.</description>",
        "<itunes:author>Matt</itunes:author>",
        "<itunes:explicit>false</itunes:explicit>",
        f"<lastBuildDate>{rfc822(now.isoformat())}</lastBuildDate>",
    ]
    for it in items:
        day = dt.date.fromisoformat(it["day"]).strftime("%a %b %-d")
        title = f"{day} · {it['slot']}/{it['of']} · {it['show']}: {it['title']}"
        out += [
            "<item>",
            f"<title>{esc(title)}</title>",
            f"<guid isPermaLink=\"false\">drive-{it['day']}-{esc(it['guid'])}</guid>",
            f"<pubDate>{rfc822(it['pubdate'])}</pubDate>",
            f"<enclosure url=\"{esc(it['url'])}\" length=\"{esc(str(it['length']))}\" type=\"{esc(it['type'])}\"/>",
            f"<itunes:duration>{it['secs']}</itunes:duration>",
            f"<link>{esc(it['link'])}</link>",
            "</item>",
        ]
    out += ["</channel>", "</rss>"]
    open(FEED_PATH, "w", encoding="utf-8").write("\n".join(out))


if any(it["day"] == today_s for it in state["items"]):
    print(f"Playlist for {today_s} already built; rewriting feed only.")
else:
    build_today()

write_feed()
json.dump(state, open(STATE_PATH, "w", encoding="utf-8"), indent=1)
print(f"Wrote {FEED_PATH} with {len(state['items'])} items.")
