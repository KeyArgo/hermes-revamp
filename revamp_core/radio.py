"""Internet radio for Revamp: presets, Radio Browser search, favorites,
stream proxy (CORS + pls/m3u resolution) and ICY now-playing metadata.

All network calls are stdlib ``urllib`` with hard timeouts; failures map to
``RadioUnavailable``/``RadioError`` so the server can return 502 JSON.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import threading
import unicodedata
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .config import data_dir

UA = "Revamp/0.1"
RADIO_BROWSER_SEARCH = "https://all.api.radio-browser.info/json/stations/search"
# A few official mirrors: all.api is a DNS round-robin that regularly wedges on
# a dead node, so a dead node must not take the whole radio section with it.
RADIO_BROWSER_SERVERS = [
    "https://all.api.radio-browser.info",
    "https://de2.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
]
# Comma-separated override (tests/screenshots simulate an outage with a
# deliberately unroutable host, no monkeypatching needed).
_ovr = os.environ.get("REVAMP_RADIO_BROWSER_SERVERS", "").strip()
if _ovr:
    RADIO_BROWSER_SERVERS = [s.strip().rstrip("/") for s in _ovr.split(",") if s.strip()]
SEARCH_TIMEOUT = 8.0
# All mirrors together get this long, so a dead network still answers (with presets)
# inside the Hermes dashboard proxy's 10 s timeout; after a full failure, skip
# Radio Browser for a minute instead of waiting on every request.
RB_TOTAL_BUDGET = 6.0
RB_DOWN_COOLDOWN = 60.0
_rb_down_until = 0.0
NOWPLAYING_TIMEOUT = 6.0
SEARCH_CACHE_TTL = 3600.0
NP_CACHE_TTL = 15.0

BROWSE_PAGE = 50            # endless-scroll page size (~ what the UI asks for)

# Quality bar for the curated "Popular" list (raw clickcount alone is full of
# spam and vote-stuffed entries — see RADIO-codex.md for the live evidence).
POPULAR_LIMIT = 40
POPULAR_WINDOW = 150        # raw stations fetched per extension round
POPULAR_WINDOWS_MAX = 6     # …and at most this many rounds per cache fill
POPULAR_TTL = 900.0         # rebuild the ranked pool after 15 min
POPULAR_STALE_OK = 24 * 3600.0  # serve a stale pool instead of presets for a day
POPULAR_MIN_BITRATE = 64    # when the bitrate is known at all
POPULAR_VOTES_PER_CLICK = 30  # votes this far above clicks ⇒ vote-stuffed, drop
POPULAR_MAX_PER_COUNTRY = 5

CATEGORY_TTL = 6 * 3600.0
MIN_TAG_STATIONS = 10
MIN_COUNTRY_STATIONS = 5
MIN_LANGUAGE_STATIONS = 5
MAX_TAGS = 350
MAX_COUNTRIES = 250
MAX_LANGUAGES = 200
# "tags" that are not genres at all (junk words people paste into the tag box).
_TAG_JUNK = {
    "radio", "fm", "am", "dab", "online", "webradio", "web", "web radio",
    "online radio", "radio online", "radio station", "station", "estacion",
    "estacin", "radio internet", "internet radio", "best", "free", "live",
    "24", "247", "7", "724", "724hours", "www", "net", "com", "hd", "hq", "sd",
    "music", "musica", "msica", "entretenimiento", "mxico", "mexico",
    "norteamerica", "latinoamerica", "sudamerica", "centroamerica", "europa",
    "africa", "asia", "espanol", "espanola", "espana", "espa", "america",
    "portugues", "frances", "italiano", "tedesco", "cubana", "colombia",
    "argentina", "venezuela",
}
# Spelling variants of the same genre merged onto one canonical key (after
# lowercasing and stripping non-alphanumerics; e.g. "Hip Hop", "hip-hop",
# "hiphop" all collapse to "hiphop" without any help from this table).
_TAG_ALIASES = {
    "rb": "randb", "rnb": "randb",
    "rockroll": "rockandroll", "rocknroll": "rockandroll",
    "drumnbass": "drumandbass",
    "koreanpop": "kpop",
    "edm": "electronic", "electrodance": "electronic",
    "regeton": "reggaeton",
    "hiphopmusic": "hiphop", "rhymemusic": "hiphop",
    "countrymusic": "country", "jazzmusic": "jazz", "rockmusic": "rock",
    "popmusic": "pop", "classicalmusic": "classical", "metalmusic": "metal",
}

_LOCK = threading.RLock()
# popular() builds its pool over several network rounds: a lock of its own, so favorites
# and the other caches never wait on that.
_POPULAR_LOCK = threading.RLock()
MAX_CACHE_ENTRIES = 500
_SEARCH_CACHE: Dict[str, tuple[float, Any]] = {}
_NP_CACHE: Dict[str, tuple[float, Optional[str]]] = {}
_CAT_CACHE: Dict[str, tuple[float, List[Dict[str, Any]]]] = {}
_POPULAR: Optional[Dict[str, Any]] = None  # ranked pool, extended lazily as the UI scrolls
_POPULAR_STALE: Optional[Dict[str, Any]] = None  # last good pool, for outage fallback


class RadioError(Exception):
    pass


PRESETS: List[Dict[str, Any]] = [
    {"id": "somafm-groovesalad", "name": "SomaFM Groove Salad",
     "url": "https://ice1.somafm.com/groovesalad-128-mp3", "homepage": "https://somafm.com/groovesalad/",
     "tags": ["ambient", "chill", "downtempo"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/groovesalad400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-dronezone", "name": "SomaFM Drone Zone",
     "url": "https://ice1.somafm.com/dronezone-128-mp3", "homepage": "https://somafm.com/dronezone/",
     "tags": ["ambient", "space", "drone"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/dronezone400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-defcon", "name": "SomaFM DEF CON Radio",
     "url": "https://ice1.somafm.com/defcon-128-mp3", "homepage": "https://somafm.com/defcon/",
     "tags": ["electronic", "ambient"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/defcon400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-secretagent", "name": "SomaFM Secret Agent",
     "url": "https://ice1.somafm.com/secretagent-128-mp3", "homepage": "https://somafm.com/secretagent/",
     "tags": ["lounge", "spy", "easy"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/secretagent400.jpg", "country": "USA", "source": "preset"},
    {"id": "nightride", "name": "Nightride FM",
     "url": "https://stream.nightride.fm/nightride.mp3", "homepage": "https://nightride.fm",
     "tags": ["synthwave", "retrowave"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "chillsynth", "name": "Chillsynth FM",
     "url": "https://stream.nightride.fm/chillsynth.mp3", "homepage": "https://nightride.fm/chillsynth",
     "tags": ["chillsynth", "synthwave"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "radioparadise", "name": "Radio Paradise Main Mix",
     "url": "https://stream.radioparadise.com/mp3-192", "homepage": "https://radioparadise.com",
     "tags": ["eclectic", "rock"], "codec": "MP3", "bitrate": 192,
     "favicon": "", "country": "USA", "source": "preset"},
    # More presets: all HTTPS (the Hermes webview blocks http streams), checked live 2026-10-21.
    {"id": "somafm-lush", "name": "SomaFM Lush",
     "url": "https://ice1.somafm.com/lush-128-mp3", "homepage": "https://somafm.com/lush/",
     "tags": ["chillout", "vocal", "electronic"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/lush400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-indiepop", "name": "SomaFM Indie Pop Rocks!",
     "url": "https://ice1.somafm.com/indiepop-128-mp3", "homepage": "https://somafm.com/indiepop/",
     "tags": ["indie", "pop", "rock"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/indiepop400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-bootliquor", "name": "SomaFM Boot Liquor",
     "url": "https://ice1.somafm.com/bootliquor-128-mp3", "homepage": "https://somafm.com/bootliquor/",
     "tags": ["americana", "country"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/bootliquor400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-spacestation", "name": "SomaFM Space Station Soma",
     "url": "https://ice1.somafm.com/spacestation-128-mp3", "homepage": "https://somafm.com/spacestation/",
     "tags": ["ambient", "electronic"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/spacestation400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-deepspaceone", "name": "SomaFM Deep Space One",
     "url": "https://ice1.somafm.com/deepspaceone-128-mp3", "homepage": "https://somafm.com/deepspaceone/",
     "tags": ["ambient", "space"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/deepspaceone400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-fluid", "name": "SomaFM Fluid",
     "url": "https://ice1.somafm.com/fluid-128-mp3", "homepage": "https://somafm.com/fluid/",
     "tags": ["hip hop", "instrumental", "lofi"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/fluid400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-beatblender", "name": "SomaFM Beat Blender",
     "url": "https://ice1.somafm.com/beatblender-128-mp3", "homepage": "https://somafm.com/beatblender/",
     "tags": ["deep house", "downtempo"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/beatblender400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-u80s", "name": "SomaFM Underground 80s",
     "url": "https://ice1.somafm.com/u80s-128-mp3", "homepage": "https://somafm.com/u80s/",
     "tags": ["80s", "new wave"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/u80s400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-seventies", "name": "SomaFM Left Coast 70s",
     "url": "https://ice1.somafm.com/seventies-128-mp3", "homepage": "https://somafm.com/seventies/",
     "tags": ["70s", "rock"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/seventies400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-thetrip", "name": "SomaFM The Trip",
     "url": "https://ice1.somafm.com/thetrip-128-mp3", "homepage": "https://somafm.com/thetrip/",
     "tags": ["trance", "progressive"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/thetrip400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-suburbsofgoa", "name": "SomaFM Suburbs of Goa",
     "url": "https://ice1.somafm.com/suburbsofgoa-128-mp3", "homepage": "https://somafm.com/suburbsofgoa/",
     "tags": ["world", "india"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/suburbsofgoa400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-folkfwd", "name": "SomaFM Folk Forward",
     "url": "https://ice1.somafm.com/folkfwd-128-mp3", "homepage": "https://somafm.com/folkfwd/",
     "tags": ["folk", "indie"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/folkfwd400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-metal", "name": "SomaFM Metal Detector",
     "url": "https://ice1.somafm.com/metal-128-mp3", "homepage": "https://somafm.com/metal/",
     "tags": ["metal"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/metal400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-vaporwaves", "name": "SomaFM Vaporwaves",
     "url": "https://ice1.somafm.com/vaporwaves-128-mp3", "homepage": "https://somafm.com/vaporwaves/",
     "tags": ["vaporwave", "electronic"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/vaporwaves400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-sonicuniverse", "name": "SomaFM Sonic Universe",
     "url": "https://ice1.somafm.com/sonicuniverse-128-mp3", "homepage": "https://somafm.com/sonicuniverse/",
     "tags": ["jazz", "nu jazz"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/sonicuniverse400.jpg", "country": "USA", "source": "preset"},
    {"id": "somafm-cliqhop", "name": "SomaFM cliqhop idm",
     "url": "https://ice1.somafm.com/cliqhop-128-mp3", "homepage": "https://somafm.com/cliqhop/",
     "tags": ["idm", "electronic"], "codec": "MP3", "bitrate": 128,
     "favicon": "https://somafm.com/img3/cliqhop400.jpg", "country": "USA", "source": "preset"},
    {"id": "rp-mellow", "name": "Radio Paradise Mellow Mix",
     "url": "https://stream.radioparadise.com/mellow-192", "homepage": "https://radioparadise.com",
     "tags": ["eclectic", "mellow"], "codec": "MP3", "bitrate": 192,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "rp-rock", "name": "Radio Paradise Rock Mix",
     "url": "https://stream.radioparadise.com/rock-192", "homepage": "https://radioparadise.com",
     "tags": ["rock"], "codec": "MP3", "bitrate": 192,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "rp-global", "name": "Radio Paradise Global Mix",
     "url": "https://stream.radioparadise.com/global-192", "homepage": "https://radioparadise.com",
     "tags": ["world", "eclectic"], "codec": "MP3", "bitrate": 192,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "kexp", "name": "KEXP 90.3 Seattle",
     "url": "https://kexp-mp3-128.streamguys1.com/kexp128.mp3", "homepage": "https://www.kexp.org",
     "tags": ["indie", "eclectic"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "fip", "name": "FIP (Radio France)",
     "url": "https://icecast.radiofrance.fr/fip-midfi.mp3", "homepage": "https://www.radiofrance.fr/fip",
     "tags": ["eclectic", "jazz", "world"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "France", "source": "preset"},
    {"id": "nts1", "name": "NTS Radio 1",
     "url": "https://stream-relay-geo.ntslive.net/stream", "homepage": "https://www.nts.live",
     "tags": ["eclectic", "underground"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "UK", "source": "preset"},
    {"id": "nts2", "name": "NTS Radio 2",
     "url": "https://stream-relay-geo.ntslive.net/stream2", "homepage": "https://www.nts.live",
     "tags": ["eclectic", "underground"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "UK", "source": "preset"},
    {"id": "wfmu", "name": "WFMU Freeform",
     "url": "https://stream0.wfmu.org/freeform-128k", "homepage": "https://wfmu.org",
     "tags": ["freeform", "eclectic"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "tsfjazz", "name": "TSF Jazz",
     "url": "https://tsfjazz.ice.infomaniak.ch/tsfjazz-high.mp3", "homepage": "https://www.tsfjazz.com",
     "tags": ["jazz"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "France", "source": "preset"},
    {"id": "wqxr", "name": "WQXR New York",
     "url": "https://stream.wqxr.org/wqxr", "homepage": "https://www.wqxr.org",
     "tags": ["classical"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "wnyc", "name": "WNYC 93.9 FM",
     "url": "https://fm939.wnyc.org/wnycfm", "homepage": "https://www.wnyc.org",
     "tags": ["news", "talk"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "bbcws", "name": "BBC World Service",
     "url": "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service", "homepage": "https://www.bbc.co.uk/worldserviceradio",
     "tags": ["news", "talk"], "codec": "MP3", "bitrate": 56,
     "favicon": "", "country": "UK", "source": "preset"},
    {"id": "darksynth", "name": "Nightride Darksynth",
     "url": "https://stream.nightride.fm/darksynth.mp3", "homepage": "https://nightride.fm/darksynth",
     "tags": ["darksynth", "synthwave"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "USA", "source": "preset"},
    {"id": "ilovechill", "name": "I Love Chillhop",
     "url": "https://streams.ilovemusic.de/iloveradio17.mp3", "homepage": "https://www.ilovemusic.de",
     "tags": ["lofi", "chillhop"], "codec": "MP3", "bitrate": 192,
     "favicon": "", "country": "Germany", "source": "preset"},
]


# Hermes Desktop's bundled radio plugin is the lineup for the Hermes Radio hero
# (apps/desktop/src/plugins/radio/plugin.js PRESETS). Kept separate from PRESETS
# above: those are the Radio Browser fallback, and several URLs differ
# (Radio Paradise AAC/FLAC, Spacesynth, EVE Radio).
_NIGHTRIDE = "https://stream.nightride.fm/"
HERMES_LINEUP: List[Dict[str, Any]] = [
    {"id": "chillsynth", "name": "Chillsynth", "description": "Soft focus · warm synths",
     "provider": "Nightride FM", "url": f"{_NIGHTRIDE}chillsynth.mp3",
     "homepage": "https://nightride.fm/?station=chillsynth",
     "tags": ["chillsynth", "synthwave"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "", "source": "hermes"},
    {"id": "nightride", "name": "Nightride", "description": "Synthwave · after hours",
     "provider": "Nightride FM", "url": f"{_NIGHTRIDE}nightride.mp3",
     "homepage": "https://nightride.fm/",
     "tags": ["synthwave", "retrowave"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "", "source": "hermes"},
    {"id": "darksynth", "name": "Darksynth", "description": "Dark electronics · high energy",
     "provider": "Nightride FM", "url": f"{_NIGHTRIDE}darksynth.mp3",
     "homepage": "https://nightride.fm/?station=darksynth",
     "tags": ["darksynth", "synthwave"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "", "source": "hermes"},
    {"id": "spacesynth", "name": "Spacesynth", "description": "Cosmic synths · retro futures",
     "provider": "Nightride FM", "url": f"{_NIGHTRIDE}spacesynth.mp3",
     "homepage": "https://nightride.fm/?station=spacesynth",
     "tags": ["spacesynth", "synthwave"], "codec": "MP3", "bitrate": 128,
     "favicon": "", "country": "", "source": "hermes"},
    {"id": "paradise-main", "name": "Main Mix", "description": "Eclectic · human selected",
     "provider": "Radio Paradise", "url": "https://stream.radioparadise.com/aac-128",
     "homepage": "https://radioparadise.com/",
     "tags": ["eclectic"], "codec": "AAC", "bitrate": 128,
     "favicon": "", "country": "", "source": "hermes"},
    {"id": "paradise-mellow", "name": "Mellow Mix", "description": "A gentler pace",
     "provider": "Radio Paradise", "url": "https://stream.radioparadise.com/mellow-flac",
     "homepage": "https://radioparadise.com/",
     "tags": ["eclectic", "mellow"], "codec": "FLAC", "bitrate": None,
     "favicon": "", "country": "", "source": "hermes"},
    {"id": "eve-radio", "name": "EVE Radio", "description": "GamingNow · EVE community radio",
     "provider": "GamingNow", "url": "https://media01.gamingnow.net:8010/erweb.mp3",
     "homepage": "https://gamingnow.net/eve-radio/",
     "tags": ["gaming"], "codec": "MP3", "bitrate": None,
     "favicon": "", "country": "", "source": "hermes"},
]


def presets() -> List[Dict[str, Any]]:
    return [dict(s) for s in PRESETS]


def hermes_lineup() -> List[Dict[str, Any]]:
    """The Hermes Radio hero. No placeholder station: Hermes FM has no stream yet."""
    return [dict(s) for s in HERMES_LINEUP]


def _cache_put(cache: Dict[str, Any], key: str, value: tuple) -> None:
    """Insert, keeping at most MAX_CACHE_ENTRIES (oldest out). Expired entries were only
    skipped on read, so every distinct search used to stay for the life of the process."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > MAX_CACHE_ENTRIES:
        cache.pop(next(iter(cache)))


def _reset_caches() -> None:
    """Forget every cached response (tests; also handy after a network outage)."""
    global _POPULAR, _POPULAR_STALE
    with _POPULAR_LOCK, _LOCK:
        _SEARCH_CACHE.clear()
        _CAT_CACHE.clear()
        _NP_CACHE.clear()
        _POPULAR = None
        _POPULAR_STALE = None


# ---------------------------------------------------------------------------
# Radio Browser client
# ---------------------------------------------------------------------------

def _rb_json(path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Any, Optional[int]]:
    """GET a Radio Browser JSON endpoint, trying each mirror in turn.

    Returns (decoded body, total) where total is the X-RBtotal count when the
    server sends one. Raises RadioError when no mirror answers.
    """
    global _rb_down_until
    query = ("?" + urllib.parse.urlencode(params)) if params else ""
    now = time.monotonic()
    if now < _rb_down_until:
        # Every mirror failed moments ago: answer at once so callers fall back to
        # presets well inside the dashboard proxy's 10 s limit.
        raise RadioError("radio-browser unreachable (recently failed)")
    deadline = now + RB_TOTAL_BUDGET
    last_err = "no servers configured"
    for base in RADIO_BROWSER_SERVERS:
        left = deadline - time.monotonic()
        if left <= 0.5:
            last_err = "time budget used up"
            break
        req = urllib.request.Request(f"{base}/json/{path}{query}", headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=min(SEARCH_TIMEOUT, left)) as resp:
                body = resp.read()
                headers = getattr(resp, "headers", None) or {}
                total_h = headers.get("x-rbtotal") or headers.get("x-rb-total")
            data = json.loads(body.decode("utf-8", errors="replace"))
            try:
                total = int(total_h) if total_h is not None else None
            except ValueError:
                total = None
            return data, total
        except Exception as exc:  # mirror down/dead — try the next one
            last_err = str(exc)
    _rb_down_until = time.monotonic() + RB_DOWN_COOLDOWN
    raise RadioError(f"radio-browser unreachable: {last_err}")


def _int_field(raw: Dict[str, Any], key: str) -> int:
    try:
        return int(raw.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_station(raw: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    url = str(raw.get("url_resolved") or raw.get("url") or "").strip()
    if not url:
        return None
    if raw.get("hls") or url.lower().endswith(".m3u8") or ".m3u8?" in url.lower():
        return None
    tags_raw = raw.get("tags") or ""
    tags = [t.strip() for t in str(tags_raw).split(",") if t.strip()][:12]
    return {
        # No Radio Browser uuid: a stable id from the URL (never a bare "rb-" shared by every such station).
        "id": (f"rb-{str(raw.get('stationuuid')).strip()}" if str(raw.get("stationuuid") or "").strip()
               else "rb-url-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]),
        "name": str(raw.get("name") or "").strip() or "Unnamed",
        "url": url,
        "homepage": str(raw.get("homepage") or ""),
        "tags": tags,
        "codec": str(raw.get("codec") or ""),
        "bitrate": _int_field(raw, "bitrate"),
        "favicon": str(raw.get("favicon") or ""),
        "country": str(raw.get("country") or ""),
        "countrycode": str(raw.get("countrycode") or "").upper(),
        "votes": _int_field(raw, "votes"),
        "clickcount": _int_field(raw, "clickcount"),
        "source": source,
    }


# order names the UI may ask for → Radio Browser order fields
_SORT_ORDERS = {"top": "clickcount", "popular": "clickcount", "votes": "votes",
                "trending": "clicktrend", "bitrate": "bitrate"}


def browse(q: str = "", tag: str = "", country: str = "", language: str = "",
           sort: str = "top", limit: int = BROWSE_PAGE, offset: int = 0) -> Dict[str, Any]:
    """One endless-scroll page of stations, cached per query+page.

    Any mix of category filters (tag / country code / language) ANDed with an
    optional name search. Returns ``{"stations": [...], "total": int|None,
    "more": bool}``; ``more`` says the raw page was full, so scrolling further
    may still find stations. Raises RadioError when Radio Browser is unreachable.
    """
    limit = max(1, min(100, int(limit)))
    offset = max(0, int(offset))
    order = _SORT_ORDERS.get((sort or "top").lower(), "clickcount")
    key = json.dumps(["browse", q, tag, country, language, order, limit, offset])
    now = time.time()
    with _LOCK:
        hit = _SEARCH_CACHE.get(key)
        if hit and now - hit[0] < SEARCH_CACHE_TTL:
            page = hit[1]
            return {"stations": [dict(s) for s in page["stations"]],
                    "total": page["total"], "more": page["more"], "next_offset": page.get("next_offset")}
    # /stations/search is the only endpoint that ANDs the exact-match filters
    # with a name search (the bytagexact/… paths silently ignore `name`).
    # Over-fetch 2x: _normalize_station drops broken/spam rows, and a page
    # that comes back short would end the endless scroll before there really
    # are no more stations.
    win = max(limit, min(200, limit * 2))
    params: Dict[str, Any] = {"hidebroken": "true", "order": order, "reverse": "true",
                              "limit": win, "offset": offset}
    if tag:
        params["tag"] = tag.strip()
        params["tagexact"] = "true"
    if country:
        params["countrycode"] = country.strip().upper()
        params["countrycodeexact"] = "true"
    if language:
        params["language"] = language.strip()
        params["languageexact"] = "true"
    if q:
        params["name"] = q
    data, total = _rb_json("stations/search", params)
    raw_list = data if isinstance(data, list) else []
    out: List[Dict[str, Any]] = []
    used = len(raw_list)
    for i, raw in enumerate(raw_list):
        st = _normalize_station(raw, "radiobrowser")
        if st:
            out.append(st)
            if len(out) >= limit:
                used = i + 1
                break
    # Dropped rows mean more than `limit` raw rows were read: the next page starts after
    # the last one used, or it would repeat stations already shown.
    page = {"stations": out, "total": total, "more": len(raw_list) >= win or used < len(raw_list),
            "next_offset": offset + used}
    with _LOCK:
        _cache_put(_SEARCH_CACHE, key, (now, page))
    return {"stations": [dict(s) for s in out], "total": total, "more": page["more"],
            "next_offset": page["next_offset"]}


# ---------------------------------------------------------------------------
# Categories (genres / countries / languages), cached per kind
# ---------------------------------------------------------------------------

def _tag_key(name: str) -> str:
    folded = unicodedata.normalize("NFKD", str(name).lower()).encode("ascii", "ignore").decode()
    key = re.sub(r"[^a-z0-9]+", "", folded)
    return _TAG_ALIASES.get(key, key)


def categories(kind: str) -> List[Dict[str, Any]]:
    """Genres, countries or languages ordered by station count, junk merged/hidden.

    Each item is ``{"name": display, "value": filter value, "count": int}``;
    ``value`` feeds browse()'s tag/country/language filter.
    """
    kind = (kind or "tags").lower()
    if kind not in ("tags", "countries", "languages"):
        raise ValueError(f"unknown category kind: {kind}")
    now = time.time()
    with _LOCK:
        hit = _CAT_CACHE.get(kind)
        if hit and now - hit[0] < CATEGORY_TTL:
            return [dict(x) for x in hit[1]]
    data, _ = _rb_json(kind, {"order": "stationcount", "reverse": "true"})
    items: List[Dict[str, Any]] = []
    raw_list = data if isinstance(data, list) else []
    if kind == "tags":
        groups: Dict[str, Dict[str, Any]] = {}
        for raw in raw_list:
            name = str(raw.get("name") or "").strip()
            count = _int_field(raw, "stationcount")
            key = _tag_key(name)
            if not name or not key or key.isdigit() or key in _TAG_JUNK:
                continue
            if count < MIN_TAG_STATIONS:
                continue
            g = groups.get(key)
            if g is None:
                groups[key] = {"name": name, "value": name, "count": count}
            else:
                g["count"] += count
                if count > 0 and not g["name"]:
                    g["name"] = g["value"] = name
        items = sorted(groups.values(), key=lambda x: -x["count"])[:MAX_TAGS]
    elif kind == "countries":
        for raw in raw_list:
            name = re.sub(r"^The\s+", "", str(raw.get("name") or "").strip())
            code = str(raw.get("iso_3166_1") or raw.get("code") or "").strip().upper()
            count = _int_field(raw, "stationcount")
            if not name or not code or count < MIN_COUNTRY_STATIONS:
                continue
            items.append({"name": name, "value": code, "count": count})
        items.sort(key=lambda x: -x["count"])
        items = items[:MAX_COUNTRIES]
    else:
        for raw in raw_list:
            name = str(raw.get("name") or "").strip()
            count = _int_field(raw, "stationcount")
            if not name or name.lower() in ("unknown", "other") or count < MIN_LANGUAGE_STATIONS:
                continue
            items.append({"name": name, "value": name, "count": count})
        items.sort(key=lambda x: -x["count"])
        items = items[:MAX_LANGUAGES]
    with _LOCK:
        _CAT_CACHE[kind] = (now, [dict(x) for x in items])
    return [dict(x) for x in items]


def search_radiobrowser(q: str = "", tag: str = "", limit: int = 30,
                        offset: int = 0) -> List[Dict[str, Any]]:
    """Radio Browser search with a 1 h per-query cache. Raises RadioError."""
    offset = max(0, int(offset))
    key = json.dumps(["search", q, tag, limit, offset], sort_keys=True)
    now = time.time()
    with _LOCK:
        hit = _SEARCH_CACHE.get(key)
        if hit and now - hit[0] < SEARCH_CACHE_TTL:
            return [dict(s) for s in hit[1]]
    params: Dict[str, Any] = {"hidebroken": "true", "order": "clickcount",
                              "reverse": "true", "limit": int(limit),
                              "offset": offset}
    if q:
        params["name"] = q
    if tag:
        params["tag"] = tag
    data, _ = _rb_json("stations/search", params)
    out: List[Dict[str, Any]] = []
    for raw in data if isinstance(data, list) else []:
        st = _normalize_station(raw, "radiobrowser")
        if st:
            out.append(st)
    with _LOCK:
        _cache_put(_SEARCH_CACHE, key, (now, [dict(s) for s in out]))
    return out


# ---------------------------------------------------------------------------
# Popular: a quality-ranked pool, extended lazily while the UI scrolls
# ---------------------------------------------------------------------------

_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
_FLAG_RE = re.compile("[\U0001F1E6-\U0001F1FF]{1,2}")  # 🇩🇪 counts as one flag, not two emoji
_TEST_RE = re.compile(r"(?i)(^|[\s,;:!.|-])test([\s,;:!.|-]|$|[?!])")
_REPEAT_RE = re.compile(r"(?i)\b([a-z\u00c0-\u024f]{3,})\s+\1\b")


def _spammy_name(name: str) -> bool:
    """Reject station names that read like spam, not like a radio station."""
    n = (name or "").strip()
    if not n:
        return True
    if len(n) > 80:
        return True
    if _TEST_RE.search(n):
        return True
    if _REPEAT_RE.search(n):            # "Radio Radio", "FM FM", …
        return True
    if len(_EMOJI_RE.findall(_FLAG_RE.sub("", n))) >= 2:  # 🎵🎶🔥 best music ever 👑👑
        return True
    letters = [ch for ch in n if ch.isalpha()]
    if len(n) >= 40 and letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.85:
        return True                     # long ALL-CAPS KEYWORD LISTS
    if sum(n.count(ch) for ch in "|»•~★✦♦") >= 3:
        return True
    return False


_CODEC_NOISE_RE = re.compile(
    r"(?:\d{2,3}k|mp3|aacp?\d*|mpa\d|ogg|flac|wma|320|256|192|128|128k|96|hq|hd|sd|official|stream)+$")


def _norm_name(name: str) -> str:
    n = re.sub(r"[^a-z0-9]+", "", (name or "").casefold())
    # "SomaFM Groove Salad (128k MP3)" must dedupe against the preset of the same name:
    return _CODEC_NOISE_RE.sub("", n)


def _dedup_key(name: str, homepage: str) -> Tuple[str, str]:
    return (_norm_name(name), _home_host(homepage))


def _home_host(homepage: str) -> str:
    try:
        host = urllib.parse.urlparse(str(homepage or "")).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# The presets a trusting "Popular" list should open with (in order); their
# less-headline siblings line up after the Radio Browser stations.
FEATURED_PRESETS = ["somafm-groovesalad", "radioparadise", "kexp", "nts1", "wfmu",
                    "bbcws", "fip", "tsfjazz", "wqxr", "nightride"]
_PRESET_CC = {"usa": "US", "uk": "GB", "great britain": "GB", "france": "FR",
              "germany": "DE"}


def _preset_rows() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(featured presets first, then the rest), each with a usable countrycode."""
    top: List[Dict[str, Any]] = []
    rest: List[Dict[str, Any]] = []
    for sid in FEATURED_PRESETS:
        for p in PRESETS:
            if p["id"] == sid:
                row = dict(p)
                row.setdefault("countrycode", _PRESET_CC.get(str(p.get("country", "")).lower(), ""))
                top.append(row)
    top_ids = {r["id"] for r in top}
    for p in PRESETS:
        if p["id"] in top_ids:
            continue
        row = dict(p)
        row.setdefault("countrycode", _PRESET_CC.get(str(p.get("country", "")).lower(), ""))
        rest.append(row)
    return top, rest


def _fresh_popular_state() -> Dict[str, Any]:
    seen = set()
    for p in PRESETS:
        seen.add(_dedup_key(p["name"], p.get("homepage", "")))
        seen.add((_norm_name(p["name"]), ""))  # catch RB clones without the homepage
    top, rest = _preset_rows()
    return {"built_at": time.time(), "raw_offset": 0, "raw_done": False,
            "top": top, "ranked": [], "rest": rest, "seen": seen, "cc": {}}


def _popular_state() -> Dict[str, Any]:
    """The shared ranked pool: presets first, then quality-filtered Radio Browser.

    An expired pool is thrown away (the next build refetches), but kept in
    _POPULAR_STALE so a Radio Browser outage can be served stale instead of
    collapsing to presets for a whole day.
    """
    global _POPULAR, _POPULAR_STALE
    now = time.time()
    if _POPULAR is not None and (now - _POPULAR["built_at"] > POPULAR_STALE_OK
                                 or now - _POPULAR["built_at"] > POPULAR_TTL):
        if len(_POPULAR["ranked"]) > len(PRESETS):
            _POPULAR_STALE = _POPULAR
        _POPULAR = None
    if _POPULAR is None:
        _POPULAR = _fresh_popular_state()
    return _POPULAR


def _popular_seq(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The full popular list: featured presets at the top, ranked stations, then the other presets."""
    return state["top"] + state["ranked"] + state["rest"]


def _popular_stale_fallback(offset: int, limit: int) -> List[Dict[str, Any]]:
    """Last good pool (within POPULAR_STALE_OK), or the built-in presets."""
    now = time.time()
    for state in (_POPULAR, _POPULAR_STALE):
        if state and state["ranked"] and now - state["built_at"] <= POPULAR_STALE_OK:
            return [dict(s) for s in _popular_seq(state)[offset:offset + limit]]
    return presets() if offset == 0 else []


def _popular_accept(st: Dict[str, Any], raw: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Quality gate + dedupe + per-country cap; mutates state when it returns True."""
    url = st["url"].lower()
    https_ok = url.startswith("https://")
    if not https_ok and _int_field(raw, "lastcheckok") != 1:
        return False                    # plain-https *or* recently verified working
    if 0 < st["bitrate"] < POPULAR_MIN_BITRATE:
        return False
    if _spammy_name(st["name"]):
        return False
    if st["votes"] > POPULAR_VOTES_PER_CLICK * (st["clickcount"] + 1):
        return False                    # vote-stuffed listing
    key = _dedup_key(st["name"], st["homepage"])
    if key in state["seen"] or (key[0], "") in state["seen"]:
        return False                    # same normalized name+homepage, or preset name clone
    cc = st.get("countrycode") or "?"
    if state["cc"].get(cc, 0) >= POPULAR_MAX_PER_COUNTRY:
        return False
    state["seen"].add(key)
    state["cc"][cc] = state["cc"].get(cc, 0) + 1
    return True


def _popular_extend(state: Dict[str, Any], need: int) -> None:
    """Fetch raw clickcount-ordered windows until `need` ranked stations exist."""
    rounds = 0
    while len(state["ranked"]) < need and not state["raw_done"] and rounds < POPULAR_WINDOWS_MAX:
        data, _total = _rb_json("stations/search", {
            "hidebroken": "true", "lastcheckok": "1", "order": "clickcount",
            "reverse": "true", "limit": POPULAR_WINDOW, "offset": state["raw_offset"]})
        window = data if isinstance(data, list) else []
        if len(window) < POPULAR_WINDOW:
            state["raw_done"] = True
        state["raw_offset"] += len(window)
        rounds += 1
        if not window:
            break
        for raw in window:
            st = _normalize_station(raw, "radiobrowser")
            if st and _popular_accept(st, raw, state):
                state["ranked"].append(st)


def popular(limit: int = POPULAR_LIMIT, offset: int = 0) -> List[Dict[str, Any]]:
    """Curated-feeling popular list: presets first, then quality-ranked Radio Browser.

    Pages through a lazily-extended ranked pool (so endless scroll works and the
    whole build is cached); falls back to a stale pool, then to the built-in
    presets, so the section is never an empty error page.
    """
    limit = max(1, min(200, int(limit)))
    offset = max(0, int(offset))
    with _POPULAR_LOCK:
        state = _popular_state()
        try:
            if not state["raw_done"]:
                # Extend until the merged sequence (top + ranked + rest) covers the page.
                _popular_extend(state, offset + limit)
            out = _popular_seq(state)[offset:offset + limit]
        except RadioError:
            out = _popular_stale_fallback(offset, limit)
        if not out and offset == 0:
            out = presets()
        return [dict(s) for s in out]


# ---------------------------------------------------------------------------
# Favorites (persisted radio_favorites.json)
# ---------------------------------------------------------------------------

def _fav_path():
    return data_dir() / "radio_favorites.json"


def favorites() -> List[Dict[str, Any]]:
    try:
        data = json.loads(_fav_path().read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("favorites must be a list")
        return [f for f in data if isinstance(f, dict)]
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RadioError(f"could not read radio favorites: {exc}") from exc


def _save_favorites(items: List[Dict[str, Any]]) -> None:
    import json as _json
    import os as _os
    import tempfile

    p = _fav_path()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".fav-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            _json.dump(items, fh, ensure_ascii=False)
        _os.replace(tmp, p)
    except BaseException:
        try:
            _os.unlink(tmp)
        except OSError:
            pass
        raise


def add_favorite(station: Dict[str, Any]) -> Dict[str, Any]:
    st = dict(station or {})
    st["source"] = st.get("source") or "custom"
    if not st.get("url") or not isinstance(st["url"], str):
        raise RadioError("station needs a url")
    if st.get("id") not in (None, ""):
        st["id"] = str(st["id"])  # DELETE ?id= always compares strings
    if not st.get("id"):
        url_hash = hashlib.sha256(str(st["url"]).encode("utf-8")).hexdigest()
        st["id"] = "custom-" + url_hash
    def same_url(u: Any) -> str:
        return str(u or "").strip().rstrip("/")

    with _LOCK:
        favs = favorites()
        # One entry per station: the same id OR the same stream URL is replaced by the newest.
        favs = [f for f in favs if f.get("id") != st["id"] and same_url(f.get("url")) != same_url(st["url"])]
        favs.append(st)
        _save_favorites(favs)
    return st


def remove_favorite(sid: str) -> bool:
    with _LOCK:
        favs = favorites()
        keep = [f for f in favs if str(f.get("id")) != str(sid)]
        if len(keep) == len(favs):
            return False
        _save_favorites(keep)
        return True



# ---------------------------------------------------------------------------
# Stream URL resolution + proxy + ICY now playing
# ---------------------------------------------------------------------------

MAX_ICY_METAINT = 256 * 1024
# NAT64 prefixes count as global, but a DNS64 gateway turns them into the IPv4 address inside.
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def _validate_stream_url(url: str) -> str:
    """Only proxy public HTTP(S) streams.

    A playlist is upstream data, not a trusted redirect instruction. It must
    not turn a public ``.m3u`` request into a local-file or private-network
    read through REVamp's authenticated proxy.
    """
    try:
        parsed = urllib.parse.urlsplit(str(url or ""))
    except ValueError as exc:
        raise RadioError("invalid stream URL") from exc
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise RadioError("stream URL must be public http(s)")
    if parsed.username is not None or parsed.password is not None:
        raise RadioError("stream URL credentials are not allowed")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        answers = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise RadioError("stream host could not be resolved") from exc
    _public_answers(answers)
    return url


def _public_answers(answers: list) -> list:
    """Every getaddrinfo answer must be a public address; returns them unchanged."""
    if not answers:
        raise RadioError("stream host could not be resolved")
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0])
        except (IndexError, ValueError):
            raise RadioError("stream host returned an invalid address") from None
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        elif address.version == 6 and address in _NAT64[1]:
            # Local-use NAT64: the IPv4 inside is scattered, and nothing public uses it.
            raise RadioError("stream host is not a public address")
        elif address.version == 6 and address in _NAT64[0]:
            address = ipaddress.ip_address(int(address) & 0xFFFFFFFF)  # the IPv4 it reaches
        if not address.is_global:
            raise RadioError("stream host resolves to a non-public address")
    return answers


def _pinned_socket(host: str, port: int, timeout: Any, source_address: Any = None) -> socket.socket:
    """Resolve once, check, and connect to exactly the checked address.

    Validating a URL and then letting urllib resolve it again leaves a DNS
    rebinding gap: a zero-TTL record can answer public for the check and
    127.0.0.1 or a metadata address for the connect.
    """
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise RadioError("stream host could not be resolved") from exc
    err: Optional[OSError] = None
    for family, socktype, proto, _canon, addr in _public_answers(answers):
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # type: ignore[attr-defined]
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(addr)
            return sock
        except OSError as exc:
            sock.close()
            err = exc
    raise err or OSError("stream host could not be reached")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = _pinned_socket(self.host, self.port, self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        sock = _pinned_socket(self.host, self.port, self.timeout, self.source_address)
        # TLS still verifies the certificate against the hostname, not the IP.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(_PinnedHTTPSConnection, req, context=self._context)  # type: ignore[attr-defined]


def safe_stream_url(url: str) -> bool:
    """Whether *url* resolves only to public HTTP(S) stream endpoints."""
    try:
        _validate_stream_url(url)
        return True
    except RadioError:
        return False


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Validate every target before urllib follows an HTTP redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_stream_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, headers: Dict[str, str], timeout: float):
    _validate_stream_url(url)
    req = urllib.request.Request(url, headers=headers)
    # Do not honour HTTP(S)_PROXY: the player connects only to the public
    # stream target it just validated.
    # Every connection (including each redirect hop) goes through the pinned
    # handlers, so the address checked is the address connected to.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SafeRedirect(),
                                         _PinnedHTTPHandler(), _PinnedHTTPSHandler())
    return opener.open(req, timeout=timeout)


def resolve_playlist_url(url: str) -> str:
    """If *url* points at a .pls/.m3u playlist file, return its first stream URL."""
    lowered = url.lower()
    if not (lowered.split("?")[0].endswith(".pls") or lowered.split("?")[0].endswith(".m3u")):
        return url
    try:
        with _open(url, {"User-Agent": UA}, NOWPLAYING_TIMEOUT) as resp:
            # utf-8-sig: Windows tools start playlists with a BOM, which made "#EXTM3U" a URL.
            text = resp.read(64 * 1024).decode("utf-8-sig", errors="replace")
            # Relative entries belong to where the playlist actually came from, which
            # after a redirect is not the URL we asked for.
            geturl = getattr(resp, "geturl", None)
            base = (geturl() if callable(geturl) else None) or url
    except Exception:
        return url
    # pls: File1=... ; m3u: first non-comment line
    import re
    m = re.search(r"^File\d+\s*=\s*(\S+)", text, re.M | re.I)
    if m:
        candidate = urllib.parse.urljoin(base, m.group(1).strip())
        return candidate if safe_stream_url(candidate) else url
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            candidate = urllib.parse.urljoin(base, line)
            return candidate if safe_stream_url(candidate) else url
    return url


def open_stream(url: str, icy: bool = False):
    """Open *url* for streaming. Returns the response object (caller closes).

    Follows redirects (urllib does by default for http) and resolves pls/m3u.
    Request headers never include Icy-MetaData unless *icy* (nowplaying only).
    """
    target = resolve_playlist_url(url)
    if not safe_stream_url(target):
        raise RadioError("stream url must be public http(s)")
    headers = {"User-Agent": UA}
    if icy:
        headers["Icy-MetaData"] = "1"
    resp = _open(target, headers, max(SEARCH_TIMEOUT, 15.0))
    final = resp.geturl()
    if final and final != target and not target.lower().endswith((".pls", ".m3u")):
        pass
    return resp


def nowplaying(url: str) -> Dict[str, Any]:
    """ICY StreamTitle for *url*; cached 15 s. Returns {'title': str|None}."""
    now = time.time()
    key = url
    # Cache first: only checked URLs are cached, and a poll every 15 s should not cost
    # a DNS lookup (or turn a DNS hiccup into an error) when the answer is known.
    with _LOCK:
        hit = _NP_CACHE.get(key)
        if hit and now - hit[0] < NP_CACHE_TTL:
            return {"title": hit[1]}
    if not safe_stream_url(url):
        raise RadioError("stream url must be public http(s)")
    title: Optional[str] = None
    try:
        target = resolve_playlist_url(url)
        if not safe_stream_url(target):
            raise RadioError("stream url must be public http(s)")
        resp = _open(target, {"User-Agent": UA, "Icy-MetaData": "1"}, NOWPLAYING_TIMEOUT)
        with resp:
            metaint = resp.headers.get("icy-metaint")
            if metaint:
                interval = int(metaint)
                # The interval is the station's say-so: a huge one made us buffer that
                # much audio in memory. Real stations use 8-64 KiB; skip it in pieces.
                if not 0 < interval <= MAX_ICY_METAINT:
                    raise RadioError("implausible icy-metaint")
                got = 0
                while got < interval:
                    piece = resp.read(min(16 * 1024, interval - got))
                    if not piece:
                        break
                    got += len(piece)
                if got == interval:
                    blen = resp.read(1)[0] * 16
                    if blen:
                        meta = resp.read(blen).decode("utf-8", errors="replace")
                        import re
                        m = re.search(r"StreamTitle='([^']*)", meta)
                        if m:
                            title = m.group(1) or None
            else:
                # Header-only fallback: icy-name if present.
                name = resp.headers.get("icy-name")
                title = name or None
    except Exception:
        title = None
    with _LOCK:
        _cache_put(_NP_CACHE, key, (now, title))
    return {"title": title}
