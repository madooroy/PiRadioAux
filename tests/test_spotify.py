"""Showing Spotify Connect in the Now-playing box.

Runs the main checks first (it reuses their stand-in player):  python3 tests/test_spotify.py
"""
import os, runpy, sys
g = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_app.py"))
A, c, Fake = g["A"], g["c"], g["Fake"]
A.threading.Thread = g["_RealThread"]; A.mpd = g["fake_mpd"]
pass
c.put("/api/presets", json=[{"name": "A", "url": "http://x/a"}])
Fake.state = "stop"; A.want(None)
L = {"REMOTE_ADDR": "127.0.0.1"}
post = lambda **d: c.post("/api/spotify", data=d, environ_base=L)
assert c.get("/api/status").get_json()["spotify"] is None
# another machine may not call it
assert c.post("/api/spotify", data={"event": "playing"}, environ_base={"REMOTE_ADDR": "192.168.1.9"}).status_code == 403
assert c.get("/api/status").get_json()["spotify"] is None
# radio playing, then Spotify starts: radio stops, page shows Spotify with the song
c.post("/api/play/0"); assert Fake.state == "play" and A._wanted
post(event="track_changed", name="Africa", artists="Toto\nSomeone Else")
assert c.get("/api/status").get_json()["spotify"] is None          # title alone doesn't mean playing
r = post(event="playing"); assert r.get_json()["state"] == "playing"
st = c.get("/api/status").get_json()
assert Fake.state == "stop" and A._wanted is None and st["playing"] is False
assert st["spotify"] == {"state": "playing", "title": "Toto, Someone Else – Africa"}, st
# pause, then a podcast episode
post(event="paused"); assert c.get("/api/status").get_json()["spotify"]["state"] == "paused"
post(event="track_changed", name="Episode 5", artists="", show="Some Show"); post(event="playing")
assert c.get("/api/status").get_json()["spotify"]["title"] == "Some Show – Episode 5"
# a long pause stops being shown
post(event="paused"); A._spotify["at"] -= A.SPOTIFY_PAUSE_KEEP + 5
assert c.get("/api/status").get_json()["spotify"] is None
# tapping a preset ends Spotify and clears it; so does Stop Playing; so does a disconnect
post(event="playing"); st = c.post("/api/play/0").get_json(); assert st["spotify"] is None and st["playing"] and A._spotify["state"] == ""
c.post("/api/stop"); post(event="playing"); assert c.post("/api/stop").get_json()["spotify"] is None
post(event="playing"); post(event="session_disconnected"); assert c.get("/api/status").get_json()["spotify"] is None
post(event="playing"); post(event="volume_changed"); assert c.get("/api/status").get_json()["spotify"]["state"] == "playing"
print("spotify display checks passed")
