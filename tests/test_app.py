"""Checks for the preset page (app.py), run against a stand-in for the radio player.

No Pi, MPD or network is needed:  python3 tests/test_app.py
"""
import json, os, sys, tempfile, shutil
from contextlib import contextmanager

# The project folder: the one above tests/, or a path given on the command line.
proj = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
json.dump([{"name":"a","url":"http://a/1"},{"name":"b","url":"http://b/2"},{"name":"c","url":"http://c/3"},{"name":"d","url":""},{"name":"e","url":""},{"name":"f","url":""}], open(os.path.join(tmp,"presets.json"),"w"))
os.environ["PIRADIO_PRESETS"] = os.path.join(tmp, "presets.json")
os.environ["PIRADIO_SPOTIFY_STOP"] = "true"
os.environ["PIRADIO_POWEROFF"] = "touch %s/poweroff-ran" % tmp
sys.path.insert(0, proj)
import app as A


class Fake:
    state, queue, vol, calls = "stop", [], 40, []
    def status(self): return {"state": Fake.state, "volume": str(Fake.vol)}
    def currentsong(self): return {"file": Fake.queue[0], "title": "Song"} if Fake.queue else {}
    def clear(self): Fake.queue = []
    def add(self, u): Fake.queue.append(u); Fake.calls.append(("add", u))
    def load(self, u): Fake.queue.append("resolved"); Fake.calls.append(("load", u))
    def play(self, i): Fake.state = "play"
    def stop(self): Fake.state = "stop"
    def setvol(self, v): Fake.vol = v


@contextmanager
def fake_mpd():
    yield Fake()

A.mpd = fake_mpd
spot = []
A.SPOTIFY_STOP_CMD = "true"; _real_ss = A.stop_spotify; A.stop_spotify = lambda: (spot.append(1), _real_ss())[1]   # count calls, run the real one
c = A.app.test_client()

r = c.get("/"); assert r.status_code == 200 and b"PiRadio" in r.data
s = c.get("/api/status").get_json()
assert len(s["presets"]) == 6 and s["playing"] is False and s["volume"] == 40

s = c.post("/api/play/0").get_json()
assert s["playing"] and s["current"] == 0 and s["title"] == "Song", s
assert len(spot) == 1                       # radio was silent -> Spotify ended
s = c.post("/api/play/2").get_json()
assert s["current"] == 2 and len(spot) == 1  # station to station -> Spotify untouched

r = c.post("/api/play/3"); assert r.status_code == 400 and "no stream address" in r.get_json()["error"]
assert c.post("/api/play/99").status_code == 400

assert c.post("/api/volume", json={"volume": 150}).get_json()["volume"] == 100 and Fake.vol == 100
assert c.post("/api/volume", json={"volume": "x"}).status_code == 400

s = c.post("/api/stop").get_json(); assert not s["playing"] and s["current"] is None and len(spot) == 2

new = [{"name": "A", "url": "http://x/a.m3u"}, {"name": "B", "url": ""}]
assert c.put("/api/presets", json=new).status_code == 200
assert json.load(open(os.environ["PIRADIO_PRESETS"])) == new
s = c.post("/api/play/0").get_json()
assert Fake.calls[-1] == ("load", "http://x/a.m3u") and s["current"] == 0  # playlist address
assert c.put("/api/presets", json=[{"name": "A", "url": "file:///etc/passwd"}]).status_code == 400
assert c.put("/api/presets", json=[{"name": "", "url": "http://x"}]).status_code == 400
assert c.put("/api/presets", json=[{"name": "n", "url": ""}] * 21).status_code == 400


@contextmanager
def dead():
    raise ConnectionRefusedError("refused")
    yield

A.mpd = dead
r = c.get("/api/status"); assert r.status_code == 503 and "Can't reach" in r.get_json()["error"]
assert c.get("/nope").status_code == 404
print("all checks passed")
for u in ("/static/apple-touch-icon.png","/apple-touch-icon.png","/favicon.ico","/manifest.webmanifest"):
    assert c.get(u).status_code == 200, u
assert b"apple-touch-icon" in c.get("/").data
print("icon checks passed")

import time
assert c.get("/api/poweroff").status_code == 405           # a plain link can't trigger it
r = c.post("/api/poweroff"); assert r.status_code == 200    # works even with the player down
assert not os.path.exists(tmp + "/poweroff-ran")            # reply goes out first
time.sleep(2.5); assert os.path.exists(tmp + "/poweroff-ran")
assert b"Power off PiRadio" in c.get("/").data and b"<h1>PiRadio</h1>" in c.get("/").data
print("power off checks passed")

# ---- now-playing feed ----
from datetime import datetime, timezone, timedelta
def feed(age_s, artist="Inxs", title="Need You Tonight"):
    t = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"performances": [{"time": t, "artist": artist, "title": title}, {"time": "2020-01-01T00:00:00Z", "artist": "Old", "title": "Old"}]})
calls = []
def fake_get(url):
    calls.append(url)
    if "bad" in url: raise OSError("down")
    if "stale" in url: return feed(3 * 3600)
    return feed(60)
A._http_get = fake_get
import threading as _t
class Now:  # run "background" refreshes immediately so the checks are deterministic
    def __init__(self, target, args, daemon): self.f, self.a = target, args
    def start(self): self.f(*self.a)
_RealThread = A.threading.Thread
A.threading.Thread = Now
assert A.feed_title("http://f/fresh") == "Inxs \u2013 Need You Tonight"
assert A.feed_title("http://f/fresh") and len(calls) == 1          # cached
assert A.feed_title("http://f/stale") == "" and "min old" in A._feeds["http://f/stale"]["error"]
assert A.feed_title("http://f/bad") == "" and A.feed_title("http://f/bad") == "" and len(calls) == 3
assert "OSError" in A._feeds["http://f/bad"]["error"]
# through the page: a preset with a feed and no title from the stream
Fake.state, Fake.queue = "stop", []
Fake.currentsong = lambda self: {"file": Fake.queue[0]} if Fake.queue else {}
A.mpd = fake_mpd
new = [{"name": "A", "url": "http://x/a", "nowplaying": "http://f/fresh"}, {"name": "B", "url": "http://x/b", "nowplaying": "ftp://no"}]
assert c.put("/api/presets", json=new).status_code == 400
new[1]["nowplaying"] = ""
r = c.put("/api/presets", json=new); assert r.status_code == 200 and "nowplaying" not in r.get_json()["presets"][1]
st = c.post("/api/play/0").get_json()
assert st["title"] == "Inxs \u2013 Need You Tonight" and st["feed"]["error"] == "" and st["stream_title"] == ""
assert c.post("/api/play/1").get_json()["title"] == ""
assert b'class = "np"' in c.get("/").data or b'className = "np"' in c.get("/").data
print("now-playing checks passed")

# ---- watchdog ----
A.threading.Thread = _RealThread
A.mpd = fake_mpd
Fake.elapsed = 0.0
Fake.status = lambda self: {"state": Fake.state, "volume": str(Fake.vol), "elapsed": str(Fake.elapsed)}
Fake.calls.clear()
c.put("/api/presets", json=[{"name": "A", "url": "http://x/a"}, {"name": "B", "url": "http://x/b"}])
A.want(None); assert A.watchdog_tick() == "idle"
c.post("/api/play/0"); assert A._wanted == {"index": 0, "url": "http://x/a", "name": "A"}
Fake.elapsed = 5;  assert A.watchdog_tick(now=100) == "ok"
Fake.elapsed = 15; assert A.watchdog_tick(now=110) == "ok"
# the stream ends: MPD goes to stop
Fake.state = "stop"; n = len(Fake.calls)
st = c.get("/api/status").get_json(); assert st["playing"] and st["recovering"] and st["current"] == 0   # shown as reconnecting at once
assert A.watchdog_tick(now=120) == "restarted" and Fake.state == "play" and Fake.calls[-1] == ("add", "http://x/a")
# it fails again straight away: wait before retrying, then retry with a longer gap each time
Fake.state = "stop"
assert A.watchdog_tick(now=122) == "backoff"
st = c.get("/api/status").get_json(); assert st["playing"] and st["current"] == 0 and st["recovering"]
assert A.watchdog_tick(now=126) == "restarted"; Fake.state = "stop"
assert A.watchdog_tick(now=130) == "backoff" and A.watchdog_tick(now=137) == "restarted"
# it comes back
Fake.elapsed = 3; assert A.watchdog_tick(now=150) == "ok" and A._watch["restarts"] == 0
assert c.get("/api/status").get_json()["recovering"] is False
# frozen: "playing" but making no progress
assert A.watchdog_tick(now=160) == "waiting" and A.watchdog_tick(now=170) == "waiting"
assert A.watchdog_tick(now=180) == "restarted"
# Stop, Spotify and Power off all end the watch
Fake.elapsed = 9; A.watchdog_tick(now=190)
c.post("/api/stop"); assert A._wanted is None and A.watchdog_tick(now=200) == "idle" and Fake.state == "stop"
c.post("/api/play/1"); n_spot = len(spot)
r = c.post("/api/yield"); assert r.status_code == 200 and A._wanted is None and Fake.state == "stop" and len(spot) == n_spot
assert A.watchdog_tick(now=300) == "idle" and Fake.state == "stop"      # radio stays off under Spotify
c.post("/api/play/1"); c.post("/api/poweroff"); assert A._wanted is None
# editing presets: follows the station if it moves, lets go if it is removed
c.post("/api/play/1")
c.put("/api/presets", json=[{"name": "B", "url": "http://x/b"}, {"name": "A", "url": "http://x/a"}]); assert A._wanted["index"] == 0
c.put("/api/presets", json=[{"name": "A", "url": "http://x/a"}]); assert A._wanted is None
print("watchdog checks passed")

# ---- address check ----
import urllib.error, http.client
table = {
    "https://s/live.aac": (200, "audio/aacp", b""),
    "https://s/page": (200, "text/html", b"<html>"),
    "https://s/list.pls": (200, "audio/x-scpls", b"[playlist]"),
    "https://s/list": (200, "audio/x-mpegurl", b"#EXTM3U\nhttp://x"),
    "https://s/x.m3u8": (200, "application/vnd.apple.mpegurl", b"#EXTM3U\n#EXT-X-VERSION:3"),
    "https://live.amperwave.net/manifest/acme-demofmaac-hlsc2.m3u8": (200, "application/vnd.apple.mpegurl", b"#EXTM3U"),
    "https://live.amperwave.net/direct/acme-demofmaac-ibc2": (200, "audio/aacp", b""),
    "https://live.amperwave.net/manifest/other-testfmaac-hlsc.m3u8": (200, "application/vnd.apple.mpegurl", b"#EXTM3U"),
    "https://s/odd": (200, "application/octet-stream", b""),
}
table["https://live.amperwave.net/manifest/acme-demofmaac-hlsc2.m3u8?user-id=abc"] = table["https://live.amperwave.net/manifest/acme-demofmaac-hlsc2.m3u8"]
def fake_probe(url):
    if url == "https://s/gone": raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    if url == "https://s/dead": raise urllib.error.URLError("timed out")
    if url not in table: raise urllib.error.URLError("no such host")
    return table[url]
_real_probe = A._probe
A._probe = fake_probe
lv = lambda u: A.check_address(u)["level"]
assert lv("https://s/live.aac") == "ok" and "AAC" in A.check_address("https://s/live.aac")["message"]
assert lv("https://s/page") == "bad" and lv("https://s/gone") == "bad" and lv("https://s/dead") == "bad" and lv("ftp://s/x") == "bad"
assert lv("https://s/list.pls") == "ok" and lv("https://s/list") == "warn" and lv("https://s/odd") == "warn"
r = A.check_address("https://s/x.m3u8"); assert r["level"] == "warn" and "suggestion" not in r
r = A.check_address("https://live.amperwave.net/manifest/acme-demofmaac-hlsc2.m3u8?user-id=abc")
assert r["level"] == "warn" and r["suggestion"] == "https://live.amperwave.net/direct/acme-demofmaac-ibc2", r
assert A.hls_alternative("https://live.amperwave.net/manifest/other-testfmaac-hlsc.m3u8") == "https://live.amperwave.net/direct/other-testfmaac-ibc"
r = A.check_address("https://live.amperwave.net/manifest/other-testfmaac-hlsc.m3u8"); assert "suggestion" not in r   # alternative not reachable -> not offered
assert c.post("/api/check", json={"url": "https://s/live.aac"}).get_json()["level"] == "ok"
assert c.post("/api/check", json={}).status_code == 400
real_open = A.urllib.request.urlopen
# the real opener copes with old "ICY 200 OK" servers
def icy(*a, **k): raise http.client.BadStatusLine("ICY 200 OK")
A.urllib.request.urlopen = icy
assert _real_probe("http://old/stream")[:2] == (200, "audio/mpeg")
A.urllib.request.urlopen = real_open
print("address checks passed")

# ---- station search ----
class Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, n=-1): return self.body
opened = []
def fake_open(req, timeout=0):
    opened.append(req.full_url)
    if "//de1." in req.full_url: raise OSError("server down")
    assert req.get_header("User-agent") == "PiRadioAux/1.0"
    return Resp(json.dumps([
        {"name": "Chunky FM", "url": "https://c/x.m3u8", "url_resolved": "https://c/x.m3u8", "hls": 1, "codec": "AAC", "bitrate": 64, "state": "NH", "countrycode": "US"},
        {"name": "KEXP", "url": "http://k/pls", "url_resolved": "https://k/kexp128.mp3", "hls": 0, "codec": "MP3", "bitrate": 128, "state": "Washington", "countrycode": "US"},
        {"name": "KEXP dup", "url": "https://k/kexp128.mp3", "url_resolved": "https://k/kexp128.mp3", "hls": 0},
        {"name": "Bad", "url": "rtmp://nope", "url_resolved": "", "hls": 0},
    ]).encode())
A.urllib.request.urlopen = fake_open
res = A.search_stations("kexp")
assert [r["name"] for r in res] == ["KEXP", "Chunky FM"], res            # continuous first, duplicates and non-http dropped
assert res[0] == {"name": "KEXP", "url": "https://k/kexp128.mp3", "where": "Washington, US", "detail": "MP3 128k", "hls": False}
assert "//de1." in opened[0] and "//de2." in opened[1] and "name=kexp" in opened[1] and "hidebroken=true" in opened[1]
opened.clear(); A.search_stations("x"); assert "//de2." in opened[0]         # remembers the server that worked
assert c.get("/api/search?q=k").status_code == 400
assert len(c.get("/api/search?q=kexp").get_json()["results"]) == 2
def all_down(req, timeout=0): raise OSError("offline")
A.urllib.request.urlopen = all_down
r = c.get("/api/search?q=kexp"); assert r.status_code == 502 and "isn't reachable" in r.get_json()["error"]
A.urllib.request.urlopen = real_open
print("search checks passed")
