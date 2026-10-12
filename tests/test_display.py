"""The screen-and-buttons program (display.py), without the HAT:  python3 tests/test_display.py

Uses a stand-in for the preset page and a pretend clock. Needs python3-pil,
which install.sh --display adds; without it these checks are skipped.
"""
import os, sys
try:
    import PIL  # noqa: F401
except ImportError:
    print("display checks skipped (python3-pil is not installed; only needed with --display)")
    sys.exit(0)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import display as D

PRESETS = [{"name": "Alpha FM", "url": "http://x/a"}, {"name": "Empty", "url": ""},
           {"name": "Bravo Radio", "url": "http://x/b"}, {"name": "Charlie 99.9", "url": "http://x/c"}]


class Clock:
    t = 1000.0
    def __call__(self): return self.t


class FakeApi:
    def __init__(self):
        self.st = {"presets": PRESETS, "playing": False, "current": None, "volume": 50, "title": "",
                   "recovering": False, "connecting": False, "notice": "", "error": "", "spotify": None}
        self.calls = []
        self.down = False

    def status(self):
        return None if self.down else dict(self.st)

    def play(self, i):
        self.calls.append(("play", i))
        self.st.update(playing=True, current=i, spotify=None, notice="")
        return dict(self.st)

    def stop(self):
        self.calls.append(("stop",))
        self.st.update(playing=False, current=None, spotify=None)
        return dict(self.st)

    def volume(self, v):
        self.calls.append(("volume", v))
        self.st["volume"] = v

    def favorite(self, title, station):
        self.calls.append(("favorite", title, station))
        self.st["liked"] = True


def make():
    clock, api = Clock(), FakeApi()
    r = D.Radio(api, clock=clock)
    r.refresh()
    return r, api, clock


# --- splitting titles
assert D.split_title("Pink Floyd - Wish You Were Here") == ("Pink Floyd", "Wish You Were Here")
assert D.split_title("Toto, Someone Else – Africa") == ("Toto, Someone Else", "Africa")
assert D.split_title("News at noon") == ("", "News at noon")
assert D.split_title(" - ") == ("", "-")
assert D.split_title(None) == ("", "")
assert D.playable(PRESETS) == [0, 2, 3]
print("title checks passed")

# --- next / previous
r, api, clock = make()
r.step(+1)                                   # stopped, nothing heard yet: first station
assert r.tuning == 0 and not api.calls
assert r.frame()[0]["message"] == "Tuning…" and r.frame()[0]["station"] == "Alpha FM"
r.step(+1); r.step(+1)                       # quick presses move along, skipping the empty preset
assert r.tuning == 3
clock.t += 0.5; assert r.tune_due() is False and not api.calls   # still choosing
clock.t += 1.0; assert r.tune_due() is True and api.calls == [("play", 3)]
assert r.last_preset == 3 and r.tuning is None
r.step(+1); assert r.tuning == 0              # wraps around
r.step(-1); r.step(-1); assert r.tuning == 2
clock.t += 2; r.tune_due(); assert api.calls[-1] == ("play", 2)
r.stop(); assert api.calls[-1] == ("stop",) and r.frame()[1] == "Stopped"
r.refresh(); r.step(-1)                      # from Stopped, either button brings back the last station
assert r.tuning == 2
clock.t += 2; r.tune_due(); r.refresh()
api.st["presets"] = [{"name": "Gone", "url": ""}]
r.refresh(); r.step(+1); assert r.tuning is None and r.frame()[1] == "No stations yet"
print("previous/next checks passed")

# --- volume
r, api, clock = make()
api.play(0); r.refresh()
r.change_volume(+5); r.change_volume(+5)     # quick presses add up
assert api.calls[-1] == ("volume", 60) and r.frame()[0]["volume"] == 60 and r.frame()[1] == "Volume 60"
for _ in range(20): r.change_volume(+5)
assert api.calls[-1] == ("volume", 100)
for _ in range(30): r.change_volume(-5)
assert api.calls[-1] == ("volume", 0)
api.stop(); api.st["spotify"] = {"state": "playing", "title": "A – B"}; r.refresh()
n = len(api.calls); r.change_volume(+5)
assert len(api.calls) == n and r.frame()[1] == "Set Spotify's volume in Spotify"
assert r.frame()[0]["volume"] == -1          # no radio volume shown while Spotify plays
print("volume checks passed")

# --- what the screen and light show
class UI: started = 0; tuning = None; last_preset = None
st = dict(FakeApi().st, playing=True, current=2, title="Artist - Song")
v = D.describe(st, UI)
assert (v["header"], v["station"], v["artist"], v["song"], v["led"]) == ("PRESET 3", "Bravo Radio", "Artist", "Song", "off")
v = D.describe(dict(st, recovering=True, connecting=True), UI)
assert v["message"] == "Trying to connect…" and v["led"] == "amber"
v = D.describe(dict(st, recovering=True, connecting=False), UI)
assert v["message"] == "Reconnecting…" and v["led"] == "amber"
v = D.describe(dict(st, playing=False, current=None, notice="Bravo Radio couldn't be played."), UI)
assert v["led"] == "red" and v["message_level"] == "bad" and not v["active"]
v = D.describe(dict(st, playing=False, current=None, error="problem"), UI)
assert v["led"] == "red" and v["message"] == "Player: problem"
v = D.describe(dict(st, playing=False, current=None, spotify={"state": "paused", "title": "X – Y"}), UI)
assert v["header"] == "SPOTIFY · PAUSED" and v["song"] == "Y" and v["led"] == "off"
v = D.describe(dict(st, playing=False, current=None), UI)
assert v["header"] == "STOPPED" and v["led"] == "off" and "starts the radio" in v["message"]
UI.started = D.time.time()
assert D.describe(None, UI)["message"] == "Starting…" and D.describe(None, UI)["led"] == "off"
UI.started = 0
v = D.describe(None, UI); assert v["led"] == "red" and "Can't reach" in v["message"]
print("screen and light checks passed")

# --- the screen goes dark when stopped, and a button wakes it
r, api, clock = make()
assert r.frame()[2] is True
clock.t += D.SCREEN_OFF_AFTER + 1
assert r.frame()[2] is False
r.change_volume(+5); assert r.frame()[2] is True     # a press lights it again
clock.t += D.SCREEN_OFF_AFTER + 5; assert r.frame()[2] is False
api.play(0); r.refresh(); clock.t += 3600; assert r.frame()[2] is True   # stays on while playing
api.down = True; r.refresh(); clock.t += 1
assert r.frame()[0]["led"] == "red"                  # long after start-up, an unreachable page is a problem
print("screen-off checks passed")

# --- saving the song to Favorites (hold previous)
r, api, clock = make()
r.save_favorite(); assert r.frame()[1] == "No song to save" and not api.calls      # stopped
api.play(2); api.st["title"] = "Artist - Song"; r.refresh()
assert r.frame()[0]["liked"] is False
r.save_favorite(); assert api.calls[-1] == ("favorite", "Artist - Song", "Bravo Radio")
assert r.frame()[1] == "Saved to Favorites" and r.frame()[0]["liked"] is True
n = len(api.calls); r.refresh(); r.save_favorite()
assert len(api.calls) == n and r.frame()[1] == "Already in Favorites"
api.st.update(title="", liked=False); r.refresh(); r.save_favorite(); assert r.frame()[1] == "No song to save"
api.st.update(title="Artist - Song", recovering=True); r.refresh(); r.save_favorite(); assert r.frame()[1] == "No song to save"
api.st.update(recovering=False); r.refresh(); r.step(+1); r.save_favorite(); assert r.frame()[1] == "No song to save"   # while tuning
r.tuning = None
api.stop(); api.st.update(spotify={"state": "playing", "title": "Toto \u2013 Africa"}, liked=False); r.refresh()
r.save_favorite(); assert api.calls[-1] == ("favorite", "Toto \u2013 Africa", "Spotify")
print("favorite checks passed")

# --- a button that does one thing on a tap and another on a hold
class FakeButton:
    hold_time = None; when_pressed = when_held = when_released = None
did = []
b = FakeButton()
D.hold_and_tap(b, tap=lambda: did.append("tap"), hold=lambda: did.append("hold"), kick=lambda: did.append("kick"))
assert b.hold_time == 1.5
b.when_pressed(); b.when_released()                      # quick press
b.when_pressed(); b.when_held(); b.when_released()       # held
assert did == ["tap", "kick", "hold", "kick"], did
print("tap and hold checks passed")

# --- drawing never fails, whatever the text
for view in (D.describe(st, UI), D.describe(None, UI),
             D.describe(dict(st, title="W" * 300), UI),
             D.describe(dict(st, title="Artist " * 40 + "- " + "Song " * 60), UI),
             D.describe(dict(st, playing=False, current=None), UI)):
    img = D.render(view, "Volume 55")
    assert img.size == (320, 240)
    D.render(view)
print("drawing checks passed")
