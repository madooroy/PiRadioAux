#!/usr/bin/env python3
"""PiRadioAux - a tiny preset page for an internet radio built on MPD.

Runs on the Raspberry Pi. Open http://piradio.local on your phone.
"""
import http.client
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request
from mpd import MPDClient

BASE = Path(__file__).resolve().parent
PRESETS_FILE = Path(os.environ.get("PIRADIO_PRESETS", BASE / "presets.json"))
# Songs saved with the heart on the page.
FAVORITES_FILE = Path(os.environ.get("PIRADIO_FAVORITES", PRESETS_FILE.with_name("favorites.json")))
MAX_FAVORITES = 500
MPD_HOST = os.environ.get("MPD_HOST", "localhost")
MPD_PORT = int(os.environ.get("MPD_PORT", "6600"))
# Command that ends any Spotify session so the radio can take over.
SPOTIFY_STOP_CMD = os.environ.get(
    "PIRADIO_SPOTIFY_STOP", "sudo -n systemctl restart raspotify"
)
# Command that shuts the Pi down safely (the Power off link on the page).
POWEROFF_CMD = os.environ.get("PIRADIO_POWEROFF", "sudo -n systemctl poweroff")
# The name shown on the page and on the phone's home screen (set by install.sh).
APP_NAME = os.environ.get("PIRADIO_NAME", "").strip() or "PiRadio"
MAX_PRESETS = 20
PLAYLIST_SUFFIXES = (".m3u", ".pls")

app = Flask(__name__)
_lock = threading.Lock()
_last_preset = None  # index of the preset most recently started from this page

# The station the listener asked for. While this is set, the watchdog below
# restarts the stream whenever it drops or freezes. Cleared by Stop, by
# Power off, and when Spotify takes over.
_wanted = None         # {"index": preset number, "url": stream address}
_watch = {"elapsed": None, "stalled": 0, "restarts": 0, "next_try": 0.0, "recovering": False, "played": False}
_notice = ""           # why the last station was given up on; shown on the page
GIVE_UP_AFTER = 3      # restarts allowed for a station that has not played at all
_play_lock = threading.RLock()
WATCH_INTERVAL = 10    # seconds between checks
STALL_CHECKS = 3       # this many checks with no progress counts as frozen
MAX_BACKOFF = 60       # longest wait between restart attempts


class BadRequest(Exception):
    pass


@contextmanager
def mpd():
    client = MPDClient()
    # Generous: stopping a chunked (HLS) stream can take MPD several seconds.
    client.timeout = 20
    client.connect(MPD_HOST, MPD_PORT)
    try:
        yield client
    finally:
        try:
            client.close()
            client.disconnect()
        except Exception:
            pass


def load_presets():
    try:
        return json.loads(PRESETS_FILE.read_text())
    except (OSError, ValueError):
        return []


def save_presets(presets):
    tmp = PRESETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(presets, indent=2) + "\n")
    tmp.replace(PRESETS_FILE)


def load_favorites():
    try:
        data = json.loads(FAVORITES_FILE.read_text())
        return [f for f in data if isinstance(f, dict) and f.get("title")]
    except (OSError, ValueError):
        return []


def save_favorites(favorites):
    tmp = FAVORITES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(favorites, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(FAVORITES_FILE)


def _fav_key(title):
    return " ".join(str(title).lower().split())


def is_favorite(title):
    key = _fav_key(title)
    return bool(key) and any(_fav_key(f["title"]) == key for f in load_favorites())


def clean_presets(data):
    if not isinstance(data, list):
        raise BadRequest("Presets must be a list.")
    if len(data) > MAX_PRESETS:
        raise BadRequest(f"At most {MAX_PRESETS} presets.")
    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            raise BadRequest("Each preset needs a name and a stream address.")
        name = str(item.get("name", "")).strip()[:40]
        url = str(item.get("url", "")).strip()
        if not name:
            raise BadRequest("Every preset needs a name.")
        if url and urlparse(url).scheme not in ("http", "https"):
            raise BadRequest(f"'{name}': the stream address must start with http:// or https://")
        feed = str(item.get("nowplaying", "")).strip()
        if feed and urlparse(feed).scheme not in ("http", "https"):
            raise BadRequest(f"'{name}': the now-playing address must start with http:// or https://")
        preset = {"name": name, "url": url}
        if feed:
            preset["nowplaying"] = feed
        cleaned.append(preset)
    return cleaned


def is_playlist(url):
    return urlparse(url).path.lower().endswith(PLAYLIST_SUFFIXES)


FEED_TTL = 15          # seconds between checks of a now-playing feed
FEED_RETRY = 60        # wait this long after a feed fails
FEED_MAX_AGE = 12 * 60  # ignore a song that started longer ago than this
_feeds = {}            # url -> {"until", "title", "error", "busy"}
_feeds_lock = threading.Lock()
BROWSER_UA = ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        return resp.read(200_000).decode("utf-8", "replace")


def _refresh_feed(url):
    """Fetch one now-playing feed and remember the result.

    Understands the AmperWave format: {"performances": [{"time", "artist",
    "title"}, ...]}, newest first.
    """
    title, error, ttl = "", "", FEED_RETRY
    try:
        latest = json.loads(_http_get(url))["performances"][0]
        started = datetime.strptime(latest["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        ttl = FEED_TTL
        age = time.time() - started.timestamp()
        if -120 <= age <= FEED_MAX_AGE:
            artist = str(latest.get("artist", "")).strip()
            song = str(latest.get("title", "")).strip()
            title = " \u2013 ".join(x for x in (artist, song) if x)[:120]
        else:
            error = f"latest song is {int(age // 60)} min old"
    except Exception as exc:  # never let a feed problem affect the radio
        error = f"{type(exc).__name__}: {exc}"[:200]
    with _feeds_lock:
        _feeds[url] = {"until": time.time() + ttl, "title": title, "error": error, "busy": False}


def feed_title(url):
    """Latest known artist and song for a feed. Never waits on the network:
    an out-of-date entry is refreshed in the background for the next request."""
    start = False
    with _feeds_lock:
        entry = _feeds.setdefault(url, {"until": 0, "title": "", "error": "not checked yet", "busy": False})
        if time.time() >= entry["until"] and not entry["busy"]:
            entry["busy"] = start = True
    if start:
        threading.Thread(target=_refresh_feed, args=(url,), daemon=True).start()
    with _feeds_lock:
        return _feeds[url]["title"]


def start_stream(client, url):
    client.clear()
    if is_playlist(url):
        client.load(url)
    else:
        client.add(url)
    client.play(0)


def want(preset):
    """Remember (or forget, with None) the station the watchdog should keep alive."""
    global _wanted, _notice
    with _play_lock:
        _wanted = preset
        _notice = ""
        _watch.update(elapsed=None, stalled=0, restarts=0, next_try=0.0, recovering=False, played=False)


def watchdog_tick(now=None):
    """One health check. Returns what it found, mainly for the tests."""
    global _wanted, _notice
    now = time.time() if now is None else now
    with _play_lock:
        wanted = _wanted
        if not wanted:
            return "idle"
        with mpd() as client:
            status = client.status()
            if status.get("state") == "play":
                try:
                    elapsed = float(status.get("elapsed", 0))
                except ValueError:
                    elapsed = 0.0
                if _watch["elapsed"] is None or elapsed > _watch["elapsed"] + 0.5:
                    _watch.update(elapsed=elapsed, stalled=0)
                    if elapsed >= 2:   # sound has really come out, not just "connecting"
                        _watch.update(restarts=0, next_try=0.0, recovering=False, played=True)
                    return "ok"
                _watch["stalled"] += 1
                if _watch["stalled"] < STALL_CHECKS:
                    return "waiting"
            # Stopped, paused, or frozen while a station is wanted: bring it back.
            _watch["recovering"] = True
            if now < _watch["next_try"]:
                return "backoff"
            if not _watch["played"] and _watch["restarts"] >= GIVE_UP_AFTER:
                # It has never produced sound since it was tapped: stop trying.
                app.logger.warning("giving up on %s after %d attempts", wanted["url"], _watch["restarts"] + 1)
                _wanted = None
                _watch.update(recovering=False)
                _notice = f"{wanted.get('name') or 'That station'} couldn't be played."
                try:
                    client.clear()
                except Exception:
                    pass
                threading.Thread(target=_explain_failure, args=(wanted,), daemon=True).start()
                return "gave up"
            _watch["restarts"] += 1
            _watch["next_try"] = now + min(MAX_BACKOFF, 5 * 2 ** (_watch["restarts"] - 1))
            _watch.update(elapsed=None, stalled=0)
            app.logger.warning("stream stopped; restarting %s (attempt %d)", wanted["url"], _watch["restarts"])
            start_stream(client, wanted["url"])
            return "restarted"


def _explain_failure(wanted):
    """Add the likely reason to the 'couldn't be played' note, if one can be found."""
    global _notice
    try:
        found = check_address(wanted["url"])
    except Exception:
        return
    with _play_lock:
        if _wanted is None and _notice.endswith("couldn't be played.") and found.get("level") != "ok":
            _notice += " " + found["message"]


def watchdog_loop():
    while True:
        time.sleep(WATCH_INTERVAL)
        try:
            watchdog_tick()
        except Exception as exc:  # MPD briefly away (an update, say): try again next time
            app.logger.warning("watchdog: %s", exc)


# ---- Checking an address, and finding stations ----------------------------

APP_UA = "PiRadioAux/1.0"
RADIO_BROWSER_HOSTS = ["de1", "de2", "fi1", "nl1", "at1"]  # tried in order
_rb_host = None
PLAYLIST_TYPES = ("audio/x-mpegurl", "audio/mpegurl", "audio/x-scpls", "application/pls+xml")
HLS_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl")
CODEC_NAMES = {"audio/mpeg": "MP3", "audio/aac": "AAC", "audio/aacp": "AAC", "audio/ogg": "Ogg",
               "application/ogg": "Ogg", "audio/flac": "FLAC"}


# Stations that limit listening by country redirect elsewhere to a "blocked" clip.
GEOBLOCK = re.compile(r"geo[-_ ]?(block|restrict|fenc)|not[-_ ]?available[-_ ]?in", re.I)


def _probe(url):
    """Open an address briefly. Returns (status, content type, first bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return resp.status, ctype, resp.read(2048), resp.geturl()
    except http.client.BadStatusLine as exc:
        if "ICY" in str(exc):          # old-style radio servers answer "ICY 200 OK"
            return 200, "audio/mpeg", b"", url
        raise


def hls_alternative(url):
    """For AmperWave's chunked addresses, the matching continuous one."""
    m = re.match(r"^(https?://live\.amperwave\.net)/manifest/(.+)-hlsc(\d*)\.m3u8$", url.split("?")[0])
    return f"{m.group(1)}/direct/{m.group(2)}-ibc{m.group(3)}" if m else ""


def check_address(url):
    """Judge whether an address will work as a preset.

    level is "ok", "warn" (will probably misbehave) or "bad" (will not play).
    """
    if urlparse(url).scheme not in ("http", "https"):
        return {"level": "bad", "message": "The address must start with http:// or https://"}
    path = urlparse(url).path.lower()
    try:
        status, ctype, head, *rest = _probe(url)
    except urllib.error.HTTPError as exc:
        return {"level": "bad", "message": f"The server answered with error {exc.code}. The address is probably wrong or out of date."}
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        return {"level": "bad", "message": f"Couldn't connect: {reason}"}
    final = (rest[0] if rest else url) or url
    if GEOBLOCK.search(final) and not GEOBLOCK.search(url):
        return {"level": "bad", "message": "This station blocks listeners outside its own country, so it will not play here."}
    if ctype in HLS_TYPES or path.endswith(".m3u8") or head.lstrip().startswith(b"#EXTM3U") and b"#EXT-X-" in head:
        result = {"level": "warn", "message": "This is a chunked (HLS) address. It plays, but tends to stop and reconnect at commercial breaks."}
        alt = hls_alternative(url)
        if alt:
            try:
                if _probe(alt)[1].startswith("audio/"):
                    result["suggestion"] = alt
                    result["message"] += " A continuous address for the same station is available."
            except Exception:
                pass
        return result
    if ctype in PLAYLIST_TYPES or path.endswith(PLAYLIST_SUFFIXES):
        if path.endswith(PLAYLIST_SUFFIXES):
            return {"level": "ok", "message": "Playlist file that points at the stream. This should play."}
        return {"level": "warn", "message": "This is a playlist file. It only plays if the address ends in .m3u or .pls."}
    if ctype.startswith("audio/") or ctype == "application/ogg":
        return {"level": "ok", "message": f"Live audio ({CODEC_NAMES.get(ctype, ctype.split('/')[-1].upper())})."}
    if ctype.startswith("text/html"):
        return {"level": "bad", "message": "This is a web page, not a stream. Use the station's direct stream address."}
    return {"level": "warn", "message": f"Unrecognised content ({ctype or 'no type given'}). It may not play."}


def simpler_queries(query):
    """Looser versions of a search to fall back on: "CKOI-FM" -> "CKOI",
    "rythme 102.9" -> "rythme", and after that the first word alone."""
    q = re.sub(r"\b\d{2,4}(?:[.,]\d{1,2})?\b", " ", query)          # the frequency
    q = re.sub(r"(?i)\b(?:fm|am|hd\d?)\b", " ", q)                    # the band
    q = " ".join(re.sub(r"[-_/.,()]+", " ", q).split())
    out = []
    first = q.split(" ")[0] if " " in q else ""
    if first.lower() in ("radio", "the", "la", "le", "el", "station"):   # too vague to search alone
        first = ""
    for cand in (q, first):
        if len(cand) >= 2 and cand.lower() != query.lower() and cand not in out:
            out.append(cand)
    return out


def search_with_fallback(query):
    """Search; when nothing matches, retry with looser versions of the name.
    Returns (results, the search that produced them)."""
    results = search_stations(query)
    if results:
        return results, query
    for cand in simpler_queries(query):
        results = search_stations(cand)
        if results:
            return results, cand
    return [], query


def search_stations(query):
    """Look stations up in the community Radio Browser directory."""
    global _rb_host
    params = urllib.parse.urlencode({"name": query, "limit": 40, "hidebroken": "true",
                                     "order": "clickcount", "reverse": "true"})
    hosts = ([_rb_host] if _rb_host else []) + [h for h in RADIO_BROWSER_HOSTS if h != _rb_host]
    last = None
    for host in hosts:
        try:
            req = urllib.request.Request(f"https://{host}.api.radio-browser.info/json/stations/search?{params}",
                                         headers={"User-Agent": APP_UA})
            with urllib.request.urlopen(req, timeout=6) as resp:
                found = json.loads(resp.read(2_000_000).decode("utf-8", "replace"))
            _rb_host = host
            break
        except Exception as exc:
            last = exc
    else:
        raise RuntimeError(f"Station search isn't reachable right now ({last}).")
    results, seen = [], set()
    for s in found:
        url = (s.get("url_resolved") or s.get("url") or "").strip()
        if urlparse(url).scheme not in ("http", "https") or url in seen:
            continue
        seen.add(url)
        hls = bool(s.get("hls")) or urlparse(url).path.lower().endswith(".m3u8")
        where = ", ".join(x for x in (str(s.get("state") or "").strip(), str(s.get("countrycode") or "").strip()) if x)
        codec = str(s.get("codec") or "").strip()
        detail = " ".join(x for x in ("" if codec.upper() == "UNKNOWN" else codec,
                                      f"{s['bitrate']}k" if s.get("bitrate") else "") if x)
        results.append({"name": str(s.get("name") or "").strip()[:60], "url": url, "where": where,
                        "detail": detail, "hls": hls})
    results.sort(key=lambda r: r["hls"])   # continuous streams first; otherwise most listened first
    return results[:15]


# What Spotify Connect on this Pi is doing, as reported by its event hook.
_spotify = {"state": "", "title": "", "at": 0.0}   # state: "playing", "paused" or ""
SPOTIFY_PAUSE_KEEP = 30 * 60   # stop showing a paused session after this long


def spotify_event(event, name="", artists="", show=""):
    """Record one event from Spotify Connect. Returns the resulting state."""
    now = time.time()
    if event == "track_changed":
        who = ", ".join(a.strip() for a in artists.splitlines() if a.strip()) or show.strip()
        _spotify["title"] = " \u2013 ".join(x for x in (who, name.strip()) if x)[:200]
    elif event in ("playing", "started"):
        _spotify.update(state="playing", at=now)
    elif event == "paused":
        _spotify.update(state="paused", at=now)
    elif event in ("stopped", "session_disconnected", "unavailable"):
        _spotify.update(state="", title="", at=now)
    return _spotify["state"]


def spotify_status():
    """What to show for Spotify, or None when it isn't in use."""
    state = _spotify["state"]
    if state == "paused" and time.time() - _spotify["at"] > SPOTIFY_PAUSE_KEEP:
        state = ""
    return {"state": state, "title": _spotify["title"]} if state else None


def stop_spotify():
    """End any Spotify session without holding up the page."""
    _spotify.update(state="", title="", at=time.time())
    if not SPOTIFY_STOP_CMD:
        return
    try:
        subprocess.Popen(
            shlex.split(SPOTIFY_STOP_CMD),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def current_status():
    presets = load_presets()
    with mpd() as client:
        status = client.status()
        song = client.currentsong()
    playing = status.get("state") == "play"
    current = None
    if playing:
        url = song.get("file", "")
        current = next((i for i, p in enumerate(presets) if p["url"] and p["url"] == url), None)
        if current is None and _last_preset is not None and _last_preset < len(presets):
            if is_playlist(presets[_last_preset]["url"]):
                current = _last_preset
    recovering = False
    if not playing and _wanted and _wanted["index"] < len(presets):
        # The stream dropped and the watchdog is bringing it back.
        playing, current, recovering = True, _wanted["index"], True
        song = {}
    title = song.get("title", "") if playing else ""
    feed_info = None
    if playing and current is not None:
        feed = presets[current].get("nowplaying")
        if feed:
            feed_text = feed_title(feed)
            title = title or feed_text
            feed_info = {"url": feed, "title": feed_text, "error": _feeds.get(feed, {}).get("error", "")}
    try:
        volume = int(status.get("volume", -1))
    except ValueError:
        volume = -1
    spotify = None if playing else spotify_status()
    return {
        "playing": playing,
        "current": current,
        "title": title,
        "recovering": recovering,
        "connecting": recovering and not _watch["played"],   # has not produced sound yet
        "stream_title": song.get("title", "") if playing else "",
        "feed": feed_info,
        "volume": volume,
        "error": status.get("error", ""),
        "notice": _notice,
        "spotify": spotify,
        "liked": is_favorite(title or (spotify or {}).get("title", "")),   # is the song showing a saved favorite?
        "presets": presets,
    }


@app.errorhandler(BadRequest)
def bad_request(err):
    return jsonify(error=str(err)), 400


@app.errorhandler(Exception)
def player_unreachable(err):
    if hasattr(err, "code") and isinstance(getattr(err, "code"), int):
        return jsonify(error=str(err)), err.code
    app.logger.exception("request failed")
    return jsonify(error=f"Can't reach the player (MPD): {err}"), 503


@app.get("/")
def index():
    return render_template("index.html", max_presets=MAX_PRESETS, name=APP_NAME)


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
@app.get("/favicon.ico")
def icon():
    return app.send_static_file("apple-touch-icon.png")


@app.get("/manifest.webmanifest")
def manifest():
    data = {
        "name": APP_NAME, "short_name": APP_NAME, "start_url": "/", "display": "standalone",
        "background_color": "#1c1814", "theme_color": "#1c1814",
        "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }
    return app.response_class(json.dumps(data), mimetype="application/manifest+json")


@app.get("/api/status")
def api_status():
    return jsonify(current_status())


@app.post("/api/play/<int:index>")
def api_play(index):
    global _last_preset
    presets = load_presets()
    if index >= len(presets):
        raise BadRequest("No such preset.")
    url = presets[index]["url"]
    if not url:
        raise BadRequest("This preset has no stream address yet. Tap Edit presets to add one.")
    with _play_lock:
        with mpd() as client:
            was_playing = client.status().get("state") == "play"
            start_stream(client, url)
        _last_preset = index
        want({"index": index, "url": url, "name": presets[index].get("name", "")})
    if not was_playing:
        # The radio was silent, so Spotify may be what's playing: end it.
        stop_spotify()
    return jsonify(current_status())


@app.post("/api/stop")
def api_stop():
    want(None)
    with mpd() as client:
        client.stop()
    stop_spotify()
    return jsonify(current_status())


@app.post("/api/yield")
def api_yield():
    """Spotify has started playing: stop the radio and stop keeping it alive."""
    want(None)
    with mpd() as client:
        client.stop()
    return jsonify(ok=True)


@app.post("/api/spotify")
def api_spotify():
    """Called by the Spotify Connect event hook on this Pi; not for other machines."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="Local use only."), 403
    form = request.form
    event = form.get("event", "")[:40]
    state = spotify_event(event, form.get("name", "")[:200], form.get("artists", "")[:400], form.get("show", "")[:200])
    if event in ("playing", "started"):
        # Spotify has the speaker now: stop the radio and stop keeping it alive.
        want(None)
        try:
            with mpd() as client:
                client.stop()
        except Exception:
            pass
    return jsonify(ok=True, state=state)


@app.post("/api/volume")
def api_volume():
    try:
        volume = int((request.get_json(silent=True) or {}).get("volume"))
    except (TypeError, ValueError):
        raise BadRequest("Volume must be a number from 0 to 100.")
    volume = max(0, min(100, volume))
    with mpd() as client:
        client.setvol(volume)
    return jsonify(volume=volume)


def _poweroff():
    try:
        subprocess.Popen(
            shlex.split(POWEROFF_CMD),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        app.logger.exception("power off failed")


@app.post("/api/poweroff")
def api_poweroff():
    want(None)
    try:
        with mpd() as client:
            client.stop()
    except Exception:
        pass  # shut down even if the player is not answering
    # Short delay so this reply reaches the phone before the Pi goes down.
    threading.Timer(1.5, _poweroff).start()
    return jsonify(ok=True)


@app.get("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        raise BadRequest("Type at least two letters of the station's name.")
    try:
        results, used = search_with_fallback(query[:60])
        return jsonify(results=results, used=used if used != query[:60] else "")
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 502


@app.post("/api/check")
def api_check():
    url = str((request.get_json(silent=True) or {}).get("url", "")).strip()
    if not url:
        raise BadRequest("No address to check.")
    return jsonify(check_address(url))


@app.get("/api/favorites")
def api_favorites():
    return jsonify(favorites=load_favorites())


@app.post("/api/favorites")
def api_favorites_add():
    """Save the song showing on the page. Saving the same song again keeps one copy."""
    data = request.get_json(silent=True) or {}
    title = " ".join(str(data.get("title", "")).split())[:200]
    station = " ".join(str(data.get("station", "")).split())[:60]
    if not title or title == "---":
        raise BadRequest("There is no song title to save right now.")
    with _lock:
        favorites = load_favorites()
        if not any(_fav_key(f["title"]) == _fav_key(title) for f in favorites):   # already saved: leave it be
            favorites.insert(0, {"title": title, "station": station,
                                 "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            save_favorites(favorites[:MAX_FAVORITES])
    return jsonify(favorites=load_favorites())


@app.delete("/api/favorites")
def api_favorites_remove():
    title = str((request.get_json(silent=True) or {}).get("title", ""))
    with _lock:
        save_favorites([f for f in load_favorites() if _fav_key(f["title"]) != _fav_key(title)])
    return jsonify(favorites=load_favorites())


@app.get("/api/presets/export")
def api_presets_export():
    """The saved presets as a file to keep, or to load into another PiRadio."""
    body = json.dumps(load_presets(), indent=2, ensure_ascii=False) + "\n"
    resp = app.response_class(body, mimetype="application/json")
    resp.headers["Content-Disposition"] = 'attachment; filename="piradio-presets.json"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.put("/api/presets")
def api_presets():
    presets = clean_presets(request.get_json(silent=True))
    with _lock:
        save_presets(presets)
    w = _wanted
    if w and not any(p["url"] == w["url"] for p in presets):
        want(None)  # the station being kept alive was edited away
    elif w:
        w["index"] = next(i for i, p in enumerate(presets) if p["url"] == w["url"])
    return jsonify(presets=presets)


if __name__ == "__main__":
    port = int(os.environ.get("PIRADIO_PORT", "80"))
    threading.Thread(target=watchdog_loop, daemon=True).start()
    try:
        from waitress import serve

        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        app.run(host="0.0.0.0", port=port)
