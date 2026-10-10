"""Region-blocked stations, and giving up on a station that never starts.

Runs the main checks first (it reuses their stand-in player):  python3 tests/test_giveup.py
"""
import os, runpy, sys
g = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_app.py"))
A, c, Fake = g["A"], g["c"], g["Fake"]
A.threading.Thread = g["_RealThread"]; A.mpd = g["fake_mpd"]
# region block
A._probe = lambda u: (200, "application/x-mpegurl", b"#EXTM3U\n#EXT-X-VERSION:3", "http://cdn.cogecolive.com/geoblock/stream_geoblocked_fr.m3u8")
r = A.check_address("https://p/api/livestream-redirect/CKOIFMAAC_SC.aac"); assert r["level"] == "bad" and "country" in r["message"], r
A._probe = lambda u: (200, "audio/mpeg", b"", u)
assert A.check_address("https://p/a.mp3")["level"] == "ok"
# give up on a station that never starts
A._probe = lambda u: (200, "application/x-mpegurl", b"#EXTM3U\n#EXT-X-VERSION:3", "http://x/geoblock/a.m3u8")
c.put("/api/presets", json=[{"name": "A", "url": "http://x/a"}, {"name": "B", "url": "http://x/b"}])
def start_fails(client, url): Fake.state = "stop"
real_start = A.start_stream; A.start_stream = start_fails
Fake.state = "stop"; c.post("/api/play/0"); Fake.state = "stop"
out = [A.watchdog_tick(now=t) for t in (10, 20, 30, 40, 50, 60, 70, 80)]
assert out[:5] == ["restarted", "restarted", "restarted", "backoff", "gave up"], out
assert "gave up" in out and out[-1] == "idle", out
import time; time.sleep(0.3)
st = c.get("/api/status").get_json()
assert st["playing"] is False and st["recovering"] is False and "A couldn't be played." in st["notice"] and "country" in st["notice"], st
# a brief "play" with no sound must not reset the count
A.start_stream = lambda cl, u: setattr(Fake, "state", "play")
Fake.elapsed = 0.0; Fake.state = "stop"; c.post("/api/play/0")
assert c.get("/api/status").get_json()["notice"] == ""
seq = []
for t in range(10, 200, 10):
    seq.append(A.watchdog_tick(now=t))
assert "gave up" in seq, seq
# a station that has played is never given up on
A.start_stream = start_fails
Fake.state = "play"; Fake.elapsed = 0.0; c.post("/api/play/1"); Fake.state = "play"; Fake.elapsed = 30
assert A.watchdog_tick(now=10) == "ok"
Fake.state = "stop"
seq = [A.watchdog_tick(now=t) for t in range(20, 900, 10)]
assert "gave up" not in seq and seq.count("restarted") > 5, seq
print("give-up and region-block checks passed")
