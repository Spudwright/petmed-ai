"""crittr.ai — YouTube channel integration.

Pulls latest videos from a public channel via RSS feed (no API key needed).
Caches the result in-process for CACHE_TTL_SECONDS.

Config (env vars):
    YOUTUBE_CHANNEL       Channel URL or handle. Accepts any of:
                          - https://www.youtube.com/@handle
                          - https://www.youtube.com/channel/UCxxx
                          - @handle
                          - UCxxx
    YOUTUBE_MAX_VIDEOS    Optional. Max videos to return (default 9, max 15).

Usage:
    from flask import Flask
    from youtube import youtube_bp
    app = Flask(__name__)
    app.register_blueprint(youtube_bp)
    # Now GET /api/youtube/videos returns JSON

If you're not on Flask, you can call `get_videos()` directly and wire it
into your framework's routing.
"""
import os
import re
import time
import logging
import threading
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 600  # 10 minutes

# in-process cache
_cache = {"ts": 0.0, "videos": [], "channel_id": None}
_lock = threading.Lock()


def _resolve_channel_id(value: str) -> str | None:
    """Resolve a handle / URL / raw ID into a canonical UCxxx channel id.

    Returns None if it can't be resolved.
    """
    if not value:
        return None
    v = value.strip()

    # Already a raw channel id
    if v.startswith("UC") and len(v) >= 20 and re.match(r"^UC[A-Za-z0-9_-]{20,}$", v):
        return v

    # /channel/UC... URL
    m = re.search(r"/channel/(UC[A-Za-z0-9_-]{20,})", v)
    if m:
        return m.group(1)

    # Handle form: @foo or https://youtube.com/@foo — need to scrape page for channelId
    handle = None
    m = re.search(r"/@([A-Za-z0-9._-]+)", v)
    if m:
        handle = m.group(1)
    elif v.startswith("@"):
        handle = v[1:]

    if handle:
        url = f"https://www.youtube.com/@{handle}"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (crittr.ai bot)"},
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                body = r.read().decode("utf-8", errors="ignore")
            # Look for "channelId":"UC..." or <link rel="canonical" href=".../channel/UC...">
            m = re.search(r'"channelId":"(UC[A-Za-z0-9_-]{20,})"', body)
            if m:
                return m.group(1)
            m = re.search(r'/channel/(UC[A-Za-z0-9_-]{20,})', body)
            if m:
                return m.group(1)
        except Exception as e:
            log.warning(f"[youtube] failed to resolve handle {handle}: {e}")

    return None


def _fetch_rss(channel_id: str, max_videos: int) -> list[dict]:
    """Fetch the channel's public RSS feed and return a list of video dicts."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (crittr.ai bot)"},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        xml_bytes = r.read()

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    root = ET.fromstring(xml_bytes)
    videos = []
    for entry in root.findall("atom:entry", ns):
        vid_el = entry.find("yt:videoId", ns)
        if vid_el is None or not vid_el.text:
            continue
        video_id = vid_el.text

        title_el = entry.find("atom:title", ns)
        title = title_el.text if title_el is not None else "Untitled"

        published_el = entry.find("atom:published", ns)
        published = published_el.text if published_el is not None else ""

        # Description lives under media:group/media:description
        desc = ""
        mg = entry.find("media:group", ns)
        if mg is not None:
            d = mg.find("media:description", ns)
            if d is not None and d.text:
                desc = d.text.strip()

        videos.append({
            "id": video_id,
            "title": title,
            "published": published,
            "description": desc[:280],  # keep payload small
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "embed_url": f"https://www.youtube.com/embed/{video_id}",
        })
        if len(videos) >= max_videos:
            break
    return videos


def get_videos(force_refresh: bool = False) -> list[dict]:
    """Return the latest videos for the configured channel (cached)."""
    channel_env = os.environ.get("YOUTUBE_CHANNEL", "").strip()
    try:
        max_videos = int(os.environ.get("YOUTUBE_MAX_VIDEOS", "9"))
    except ValueError:
        max_videos = 9
    max_videos = max(1, min(15, max_videos))

    if not channel_env:
        log.info("[youtube] YOUTUBE_CHANNEL not set — returning empty list")
        return []

    now = time.time()
    with _lock:
        fresh = (now - _cache["ts"]) < CACHE_TTL_SECONDS
        if fresh and not force_refresh and _cache["videos"]:
            return _cache["videos"]

        # Resolve channel id if we don't have one yet (or env changed)
        channel_id = _cache.get("channel_id")
        if not channel_id or force_refresh:
            channel_id = _resolve_channel_id(channel_env)
            if not channel_id:
                log.warning(f"[youtube] could not resolve channel: {channel_env!r}")
                return _cache.get("videos", [])
            _cache["channel_id"] = channel_id

        try:
            videos = _fetch_rss(channel_id, max_videos)
            _cache["videos"] = videos
            _cache["ts"] = now
            return videos
        except Exception as e:
            log.error(f"[youtube] fetch failed: {e}")
            # serve stale cache if we have it, else empty list
            return _cache.get("videos", [])


# -------- Flask blueprint (optional) --------

try:
    from flask import Blueprint, jsonify

    youtube_bp = Blueprint("youtube", __name__)

    @youtube_bp.route("/api/youtube/videos")
    def api_videos():
        return jsonify({"videos": get_videos()})

    @youtube_bp.route("/api/youtube/refresh", methods=["POST"])
    def api_refresh():
        return jsonify({"videos": get_videos(force_refresh=True)})

except ImportError:
    youtube_bp = None  # Flask not installed; use get_videos() directly.
