"""Picking the station up again after an unexpected restart.

Runs the main checks first (it reuses their stand-in player):  python3 tests/test_resume.py
"""
import json, os, runpy
g = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_app.py"))
A, c, Fake = g["A"], g["c"], g["Fake"]
A.threading.Thread = g["_RealThread"]; A.mpd = g["fake_mpd"]
A._host_resolves = lambda url: True
c.put("/api/presets", json=[{"name": "A", "url": "http://x/a"}, {"name": "B", "url": "http://x/b"}])
saved = lambda: json.loads(A.RESUME_FILE.read_text()) if A.RESUME_FILE.exists() else None

def restart():
    """What a restart leaves behind: nothing in memory, the player silent."""
    A._wanted = None; A._last_preset = None; Fake.state = "stop"; Fake.queue = []

# playing -> saved; the Pi stops -> it plays again
c.post("/api/play/1"); assert saved() == {"index": 1, "url": "http://x/b", "name": "B"}
restart()
assert A.resume_after_restart(sleep=lambda s: None) is True
st = c.get("/api/status").get_json()
assert st["playing"] and st["current"] == 1 and A._wanted["url"] == "http://x/b"
print("resume after a restart passed")

# Stop, Power off, Spotify taking over: stays quiet after a restart
for leave in (lambda: c.post("/api/stop"),
              lambda: c.post("/api/poweroff"),
              lambda: c.post("/api/spotify", data={"event": "playing"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})):
    c.post("/api/play/0"); assert saved()
    leave(); assert saved() is None
    restart(); assert A.resume_after_restart(sleep=lambda s: None) is False and Fake.state == "stop"
A.spotify_event("stopped")
print("no resume after Stop, Power off or Spotify passed")

# the preset was changed or removed meanwhile: forget it
c.post("/api/play/1"); A.PRESETS_FILE.write_text(json.dumps([{"name": "A", "url": "http://x/a"}]))
restart(); assert A.resume_after_restart(sleep=lambda s: None) is False and saved() is None
c.put("/api/presets", json=[{"name": "A", "url": "http://x/a"}, {"name": "B", "url": "http://x/b"}])
# a damaged file is ignored
A.RESUME_FILE.write_text("{nope"); restart(); assert A.resume_after_restart(sleep=lambda s: None) is False
print("changed presets and damaged file passed")

# waits for the network, then plays
c.post("/api/play/0"); restart()
answers = iter([False, False, True]); naps = []
A._host_resolves = lambda url: next(answers)
assert A.resume_after_restart(sleep=naps.append) is True and len(naps) == 2 and Fake.state == "play"
# gives up waiting after the limit, but still starts (the watchdog takes it from there)
restart(); A._host_resolves = lambda url: False; naps = []
assert A.resume_after_restart(wait=10, step=2, sleep=naps.append) is True and len(naps) == 5
A._host_resolves = lambda url: True
# someone picked something else while it waited: leave that alone
restart(); A._wanted = {"index": 0, "url": "http://x/a", "name": "A"}
assert A.resume_after_restart(sleep=lambda s: None) is False
print("waiting for the network passed")

# a station the watchdog gave up on is not tried again after a restart
real = A.start_stream; A.start_stream = lambda cl, u: setattr(Fake, "state", "stop")
c.post("/api/play/0"); Fake.state = "stop"
out = [A.watchdog_tick(now=t) for t in range(10, 200, 10)]
assert "gave up" in out and saved() is None, out
A.start_stream = real
print("no resume after giving up passed")
