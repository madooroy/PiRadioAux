#!/usr/bin/env python3
"""Screen and buttons for PiRadio, on a Pimoroni Display HAT Mini.

Shows the station, artist and song (or what Spotify is playing) on the 2-inch
screen, and turns the HAT's four buttons into radio controls:

    top left      previous preset          (hold to save the song to Favorites)
    bottom left   next preset          (either one restarts the last station when stopped)
    top right     volume up            (hold to keep going up)
    bottom right  volume down          (hold to stop playing)

The light on the HAT stays off while all is well. It shows amber while a
station is connecting or reconnecting, and red when a station was given up on
or the player reports an error.

It runs as its own service (piradio-display) beside the preset page and only
talks to the page's own web address, exactly as the phone does. If this
program stops, the radio carries on.

    python3 display.py              run on the HAT (what the service does)
    python3 display.py --preview D  no hardware: write each new screen to D/screen.png
"""
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

BASE_URL = os.environ.get("PIRADIO_URL", "http://127.0.0.1").rstrip("/")
NAME = os.environ.get("PIRADIO_NAME", "PiRadio")
FLIP = os.environ.get("PIRADIO_DISPLAY_FLIP", "0") == "1"   # turn the picture (and buttons) around

WIDTH, HEIGHT = 320, 240
VOLUME_STEP = 5
TUNE_DELAY = 1.0        # seconds after the last previous/next press before the station starts
SCREEN_OFF_AFTER = 60   # seconds of being stopped before the screen goes dark
TOAST_TIME = 2.5        # seconds a short message stays on screen
STARTUP_GRACE = 90      # seconds after start before "can't reach the page" counts as a problem

# Colours from the preset page's dark theme.
BG = (28, 24, 20)
INK = (241, 232, 218)
MUTED = (163, 149, 132)
ACCENT = (224, 138, 79)
WARN = (240, 162, 115)
BAD = (255, 107, 107)
LINE = (59, 50, 41)

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            _fonts[key] = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
        except OSError:
            _fonts[key] = ImageFont.load_default(size)
    return _fonts[key]


# ---- What to show -------------------------------------------------------------

def split_title(title):
    """'Artist - Song' (radio) or 'Artist – Song' (Spotify) -> (artist, song)."""
    title = (title or "").strip()
    for sep in (" – ", " - ", " — "):
        if sep in title:
            artist, song = title.split(sep, 1)
            if artist.strip() and song.strip():
                return artist.strip(), song.strip()
    return "", title


def playable(presets):
    """Indexes of presets that have a stream address."""
    return [i for i, p in enumerate(presets or []) if (p.get("url") or "").strip()]


def describe(status, ui):
    """Turn the page's status (or None if unreachable) into what the screen and light show.

    Returns a dict: header, station, artist, song, message, message_level,
    volume, led ("off", "amber", "red"), active (something is playing or wrong).
    """
    view = {"header": "", "station": "", "artist": "", "song": "", "message": "",
            "message_level": "", "volume": -1, "led": "off", "active": False, "liked": False}
    if status is None:
        starting = time.time() - ui.started < STARTUP_GRACE
        view.update(header="", station=NAME,
                    message="Starting…" if starting else f"Can't reach {NAME}. It may be restarting.",
                    message_level="" if starting else "bad",
                    led="off" if starting else "red", active=starting)
        return view
    presets = status.get("presets") or []
    view["volume"] = status.get("volume", -1)
    current = status.get("current")
    if ui.tuning is not None:
        p = presets[ui.tuning] if ui.tuning < len(presets) else {}
        view.update(header=f"PRESET {ui.tuning + 1}", station=p.get("name") or f"Preset {ui.tuning + 1}",
                    message="Tuning…", active=True)
        return view
    if status.get("playing"):
        p = presets[current] if current is not None and current < len(presets) else {}
        view["header"] = f"PRESET {current + 1}" if current is not None else "RADIO"
        view["station"] = p.get("name") or "Radio"
        view["active"] = True
        if status.get("recovering"):
            view["message"] = "Trying to connect…" if status.get("connecting") else "Reconnecting…"
            view["message_level"] = "warn"
            view["led"] = "amber"
        else:
            view["artist"], view["song"] = split_title(status.get("title"))
            view["liked"] = bool(status.get("liked") and view["song"])
        return view
    spotify = status.get("spotify")
    if spotify:
        view["header"] = "SPOTIFY" if spotify.get("state") == "playing" else "SPOTIFY · PAUSED"
        view["station"] = "Spotify"
        view["artist"], view["song"] = split_title(spotify.get("title"))
        view["volume"] = -1   # Spotify's volume is its own, set in the Spotify app
        view["liked"] = bool(status.get("liked") and view["song"])
        view["active"] = spotify.get("state") == "playing"
        return view
    # Stopped.
    view["header"] = "STOPPED"
    view["station"] = NAME
    # A problem keeps the light red; the screen still goes dark after a while.
    if status.get("notice"):
        view.update(message=status["notice"], message_level="bad", led="red")
    elif status.get("error"):
        view.update(message="Player: " + status["error"], message_level="bad", led="red")
    else:
        last = ui.last_preset
        if last is not None and last < len(presets) and (presets[last].get("url") or "").strip():
            view["message"] = f"\u25b2 or \u25bc plays {presets[last].get('name') or 'the last station'}."
        elif playable(presets):
            view["message"] = "\u25b2 or \u25bc starts the radio."
        else:
            view["message"] = "Add stations from the preset page on your phone."
    return view


# ---- Drawing --------------------------------------------------------------------

def _wrap(draw, text, fnt, width, max_lines):
    """Break text into at most max_lines lines that fit width; '…' if cut short."""
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = word
        while draw.textlength(line, font=fnt) > width:   # a single word wider than the screen
            cut = len(line)
            while cut > 1 and draw.textlength(line[:cut], font=fnt) > width:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=fnt) > width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


def _fit(draw, text, sizes, bold, width, max_lines):
    """Largest font size from sizes at which text fits in max_lines; smallest one cut short otherwise."""
    for size in sizes:
        fnt = font(size, bold)
        lines = _wrap(draw, text, fnt, width, max_lines * 10)
        if len(lines) <= max_lines:
            return fnt, lines
    fnt = font(sizes[-1], bold)
    return fnt, _wrap(draw, text, fnt, width, max_lines)


RAIL = 30          # width of the strip at each side, beside the buttons, for the button marks
DIM = (112, 100, 88)


def _heart(d, cx, cy, size, fill):
    r = size / 4
    d.ellipse((cx - 2 * r, cy - r * 1.6, cx, cy + r * 0.4), fill=fill)
    d.ellipse((cx, cy - r * 1.6, cx + 2 * r, cy + r * 0.4), fill=fill)
    d.polygon([(cx - 2 * r + 0.3, cy - r * 0.4), (cx + 2 * r - 0.3, cy - r * 0.4), (cx, cy + r * 2.1)], fill=fill)


def _button_marks(d):
    """What each button does, drawn in the strips beside them.

    Left: previous (up the preset list) with hold-for-favorite, next (down).
    Right: volume up, volume down with hold-to-stop.
    """
    lx, rx = RAIL // 2, WIDTH - RAIL // 2
    top, bottom = 62, 166   # lined up with the buttons on the real HAT (photo, Oct 22)
    # previous preset, and below it the hold mark: heart
    d.polygon([(lx - 8, top + 5), (lx + 8, top + 5), (lx, top - 6)], fill=MUTED)
    _heart(d, lx, top + 24, 12, DIM)
    # next preset
    d.polygon([(lx - 8, bottom - 5), (lx + 8, bottom - 5), (lx, bottom + 6)], fill=MUTED)
    # volume up
    d.rectangle((rx - 8, top - 1, rx + 8, top + 1), fill=MUTED)
    d.rectangle((rx - 1, top - 8, rx + 1, top + 8), fill=MUTED)
    # volume down, and above it the hold mark: stop
    d.rectangle((rx - 8, bottom - 1, rx + 8, bottom + 1), fill=MUTED)
    d.rectangle((rx - 5, bottom - 29, rx + 5, bottom - 19), fill=DIM)
    for x in (RAIL, WIDTH - RAIL):
        d.line((x, 8, x, HEIGHT - 30), fill=LINE, width=1)


def render(view, toast=""):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    left, right = RAIL + 8, WIDTH - RAIL - 8
    inner = right - left
    _button_marks(d)
    # Header: where the sound comes from, a heart for a saved song, and the volume.
    small = font(15, True)
    d.text((left, 10), view["header"], font=small, fill=MUTED)
    end = right
    if view["volume"] >= 0:
        vol = f"VOL {view['volume']}"
        end = right - d.textlength(vol, font=small)
        d.text((end, 10), vol, font=small, fill=MUTED)
        end -= 10
    if view.get("liked"):
        _heart(d, end - 7, 19, 14, ACCENT)
    # Station.
    fnt, lines = _fit(d, view["station"], (28, 24, 21, 18), True, inner, 1)
    y = 34
    d.text((left, y), lines[0], font=fnt, fill=ACCENT)
    y += fnt.size + 14
    d.line((left, y - 6, right, y - 6), fill=LINE, width=1)
    # Artist and song, or a message.
    bottom = HEIGHT - 22
    if view["message"]:
        colour = {"bad": BAD, "warn": WARN}.get(view["message_level"], INK)
        fnt, lines = _fit(d, view["message"], (21, 18, 16), view["message_level"] == "bad", inner, 5)
        for line in lines:
            d.text((left, y), line, font=fnt, fill=colour)
            y += fnt.size + 6
    else:
        if view["artist"]:
            fnt, lines = _fit(d, view["artist"], (21, 18, 16), False, inner, 2)
            for line in lines:
                d.text((left, y), line, font=fnt, fill=MUTED)
                y += fnt.size + 5
            y += 4
        if view["song"]:
            room = max(1, min(3, (bottom - y) // 28))
            fnt, lines = _fit(d, view["song"], (25, 21, 18), True, inner, room)
            for line in lines:
                if y + fnt.size > bottom:
                    break
                d.text((left, y), line, font=fnt, fill=INK)
                y += fnt.size + 6
    # Volume bar along the bottom, or a short message in its place.
    if toast:
        fnt = font(15, True)
        d.rectangle((0, HEIGHT - 26, WIDTH, HEIGHT), fill=LINE)
        d.text(((WIDTH - d.textlength(toast, font=fnt)) / 2, HEIGHT - 22), toast, font=fnt, fill=INK)
    elif view["volume"] >= 0:
        d.rectangle((left, HEIGHT - 10, right, HEIGHT - 6), fill=LINE)
        d.rectangle((left, HEIGHT - 10, left + int(inner * min(100, view["volume"]) / 100), HEIGHT - 6), fill=ACCENT)
    return img


# ---- Talking to the preset page --------------------------------------------------

class Api:
    def __init__(self, base=BASE_URL):
        self.base = base

    def _call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read() or b"null")

    def status(self):
        try:
            return self._call("GET", "/api/status")
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def play(self, index):
        return self._call("POST", f"/api/play/{index}")

    def stop(self):
        return self._call("POST", "/api/stop")

    def volume(self, value):
        return self._call("POST", "/api/volume", {"volume": value})

    def favorite(self, title, station):
        return self._call("POST", "/api/favorites", {"title": title, "station": station})


# ---- What the buttons do ---------------------------------------------------------

class Radio:
    """Button actions and screen state. Hardware-free, so it can be tested."""

    def __init__(self, api, clock=time.time):
        self.api = api
        self.clock = clock
        self.lock = threading.RLock()
        self.status = None
        self.started = clock()
        self.tuning = None          # preset index chosen with previous/next, not started yet
        self.tune_at = 0.0
        self.last_preset = None     # last station heard, for restarting from Stopped
        self.toast, self.toast_until = "", 0.0
        self.stopped_since = None
        self.woken_at = clock()
        self.volume = None          # what we last asked for, so quick presses add up

    # -- state from the page
    def refresh(self):
        status = self.api.status()
        with self.lock:
            self.status = status
            if status:
                if status.get("playing") and status.get("current") is not None:
                    self.last_preset = status["current"]
                if self.volume is None or self.clock() - self._volume_at > 2:
                    self.volume = status.get("volume")
        return status

    _volume_at = 0.0

    def say(self, text):
        with self.lock:
            self.toast, self.toast_until = text, self.clock() + TOAST_TIME

    def wake(self):
        self.woken_at = self.clock()

    # -- buttons
    def step(self, direction):
        """Previous (-1) or next (+1). From Stopped, either one restarts the last station."""
        self.wake()
        with self.lock:
            presets = (self.status or {}).get("presets") or []
            choices = playable(presets)
            if not choices:
                self.say("No stations yet")
                return
            playing = (self.status or {}).get("playing")
            here = self.tuning if self.tuning is not None else (self.status or {}).get("current")
            if self.tuning is None and not playing:
                last = self.last_preset
                target = last if last in choices else choices[0]
            elif here in choices:
                target = choices[(choices.index(here) + direction) % len(choices)]
            else:
                target = choices[0] if direction > 0 else choices[-1]
            self.tuning, self.tune_at = target, self.clock() + TUNE_DELAY

    def tune_due(self):
        """Start the chosen station once the presses have stopped."""
        with self.lock:
            if self.tuning is None or self.clock() < self.tune_at:
                return False
            target, self.tuning = self.tuning, None
        try:
            self.status = self.api.play(target)
            self.last_preset = target
        except Exception as err:
            self.say(_short_error(err))
        return True

    def change_volume(self, delta):
        self.wake()
        with self.lock:
            st = self.status or {}
            if not st.get("playing") and (st.get("spotify") or {}).get("state") == "playing":
                self.say("Set Spotify's volume in Spotify")
                return
            base = self.volume if self.volume is not None else st.get("volume", 50)
            if base is None or base < 0:
                base = 50
            new = max(0, min(100, base + delta))
            self.volume, self._volume_at = new, self.clock()
        try:
            self.api.volume(new)
            if self.status is not None:
                self.status["volume"] = new
            self.say(f"Volume {new}")
        except Exception as err:
            self.say(_short_error(err))

    def song_showing(self):
        """(title, station) of the song on screen, or None: what the phone page's heart would save."""
        st = self.status or {}
        if self.tuning is not None:
            return None
        if st.get("playing"):
            if st.get("recovering") or not split_title(st.get("title"))[1]:
                return None
            presets, cur = st.get("presets") or [], st.get("current")
            station = presets[cur].get("name", "") if cur is not None and cur < len(presets) else ""
            return st["title"], station
        spotify = st.get("spotify") or {}
        if spotify.get("title"):
            return spotify["title"], "Spotify"
        return None

    def save_favorite(self):
        self.wake()
        with self.lock:
            song = self.song_showing()
            liked = (self.status or {}).get("liked")
        if not song:
            self.say("No song to save")
            return
        if liked:
            self.say("Already in Favorites")
            return
        try:
            self.api.favorite(*song)
            with self.lock:
                if self.status is not None:
                    self.status["liked"] = True
            self.say("Saved to Favorites")
        except Exception as err:
            self.say(_short_error(err))

    def stop(self):
        self.wake()
        with self.lock:
            self.tuning = None
        try:
            self.status = self.api.stop()
            self.say("Stopped")
        except Exception as err:
            self.say(_short_error(err))

    # -- what to show
    def frame(self):
        """(view, toast, screen_on) for right now."""
        with self.lock:
            now = self.clock()
            view = describe(self.status, self)
            if self.volume is not None and view["volume"] >= 0:
                view["volume"] = self.volume
            toast = self.toast if now < self.toast_until else ""
            if view["active"]:
                self.stopped_since = None
            elif self.stopped_since is None:
                self.stopped_since = now
            quiet_since = max(self.stopped_since or now, self.woken_at)
            screen_on = view["active"] or bool(toast) or now - quiet_since < SCREEN_OFF_AFTER
            return view, toast, screen_on


def _short_error(err):
    if isinstance(err, urllib.error.HTTPError):
        try:
            return (json.loads(err.read()).get("error") or "Didn't work")[:40]
        except Exception:
            return "Didn't work"
    return f"Can't reach {NAME}"[:40]


# ---- The HAT itself ----------------------------------------------------------------

# Pins on the Display HAT Mini (BCM numbers), from Pimoroni's own library.
PIN_A, PIN_B, PIN_X, PIN_Y = 5, 6, 16, 24
PIN_LED_R, PIN_LED_G, PIN_LED_B = 17, 27, 22
PIN_DC, PIN_BACKLIGHT = 9, 13
SPI_PORT, SPI_CS = 0, 1


class GpiodOutputs:
    """Output pins claimed straight through the kernel (libgpiod), as Pimoroni's own
    st7789 library does. Needed for the screen's data/command pin, GPIO 9: it is
    also the SPI connection's MISO pin, and gpiozero refuses to claim it while SPI
    is on ("Invalid argument"). The kernel interface lets it be driven anyway."""

    def __init__(self, names):
        import glob
        import gpiod
        from gpiod.line import Direction, Value
        self._value = Value
        for path in sorted(glob.glob("/dev/gpiochip*")):
            try:
                with gpiod.Chip(path) as chip:
                    offsets = {n: chip.line_offset_from_id(f"GPIO{n}") for n in names}
            except (OSError, ValueError):
                continue
            break
        else:
            raise RuntimeError(f"no GPIO chip has pins {names}")
        self._offsets = offsets
        settings = gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.INACTIVE)
        self._req = gpiod.request_lines(path, consumer="piradio-display",
                                        config={tuple(offsets.values()): settings})
        self._state = {n: False for n in names}

    def set(self, name, on):
        self._req.set_value(self._offsets[name], self._value.ACTIVE if on else self._value.INACTIVE)
        self._state[name] = bool(on)

    def get(self, name):
        return self._state[name]

    def release(self):
        try:
            self._req.release()
        except Exception:
            pass


class HatMini:
    """The 320x240 ST7789 screen, four buttons and the RGB light."""

    def __init__(self, flip=False):
        import numpy
        import spidev
        from gpiozero import Button, RGBLED
        self.numpy = numpy
        self.flip = flip
        self.spi = spidev.SpiDev(SPI_PORT, SPI_CS)
        self.spi.mode = 0
        self.spi.max_speed_hz = 60_000_000
        self.pins = GpiodOutputs((PIN_DC, PIN_BACKLIGHT))
        self.led = RGBLED(PIN_LED_R, PIN_LED_G, PIN_LED_B, active_high=False)
        # Buttons by where they are when the picture is the right way up.
        a, b, x, y = (Button(p, pull_up=True, bounce_time=0.03) for p in (PIN_A, PIN_B, PIN_X, PIN_Y))
        if flip:
            a, b, x, y = y, x, b, a
        self.top_left, self.bottom_left, self.top_right, self.bottom_right = a, b, x, y
        self._init_panel()
        self.pins.set(PIN_BACKLIGHT, True)

    def _cmd(self, c, *data):
        self.pins.set(PIN_DC, False)
        self.spi.writebytes([c])
        if data:
            self.pins.set(PIN_DC, True)
            self.spi.writebytes(list(data))

    def _init_panel(self):
        # The same start-up sequence Pimoroni's st7789 library sends.
        self._cmd(0x01); time.sleep(0.15)                  # software reset
        self._cmd(0x36, 0x70)                               # memory access order
        self._cmd(0xB2, 0x0C, 0x0C, 0x00, 0x33, 0x33)      # porch
        self._cmd(0x3A, 0x05)                               # 16-bit colour
        self._cmd(0xB7, 0x14)
        self._cmd(0xBB, 0x37)
        self._cmd(0xC0, 0x2C)
        self._cmd(0xC2, 0x01)
        self._cmd(0xC3, 0x12)
        self._cmd(0xC4, 0x20)
        self._cmd(0xD0, 0xA4, 0xA1)
        self._cmd(0xC6, 0x0F)
        self._cmd(0xE0, 0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B, 0x3F, 0x54, 0x4C, 0x18, 0x0D, 0x0B, 0x1F, 0x23)
        self._cmd(0xE1, 0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C, 0x3F, 0x44, 0x51, 0x2F, 0x1F, 0x1F, 0x20, 0x23)
        self._cmd(0x21)                                     # colour inversion (this panel needs it)
        self._cmd(0x11); time.sleep(0.12)                  # wake
        self._cmd(0x29); time.sleep(0.1)                   # display on

    def show(self, img):
        np = self.numpy
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint16)
        if not self.flip:
            pixels = np.rot90(pixels, 2)   # the HAT's natural way up, as Pimoroni's library draws it
        rgb565 = ((pixels[..., 0] & 0xF8) << 8) | ((pixels[..., 1] & 0xFC) << 3) | (pixels[..., 2] >> 3)
        data = rgb565.astype(">u2").tobytes()
        self._cmd(0x2A, 0, 0, (WIDTH - 1) >> 8, (WIDTH - 1) & 0xFF)
        self._cmd(0x2B, 0, 0, (HEIGHT - 1) >> 8, (HEIGHT - 1) & 0xFF)
        self._cmd(0x2C)
        self.pins.set(PIN_DC, True)
        self.spi.writebytes2(data)

    def screen(self, on):
        if on != self.pins.get(PIN_BACKLIGHT):
            self.pins.set(PIN_BACKLIGHT, on)

    def light(self, colour):
        self.led.color = {"off": (0, 0, 0), "amber": (1, 0.35, 0), "red": (1, 0, 0)}[colour]

    def close(self):
        try:
            self.light("off")
            self.pins.set(PIN_BACKLIGHT, False)
            self._cmd(0x28)     # display off
            self._cmd(0x10)     # sleep
        except Exception:
            pass
        self.pins.release()


class Preview:
    """No hardware: each new screen is written to a PNG, for trying layouts on a computer."""

    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self.lit, self.colour = True, "off"

    def show(self, img):
        img.save(os.path.join(self.folder, "screen.png"))

    def screen(self, on):
        self.lit = on

    def light(self, colour):
        self.colour = colour

    def close(self):
        pass


def wire_buttons(hat, radio, kick):
    """Connect the four buttons to the radio. kick() asks the main loop to redraw now."""
    def act(fn):
        def run(*_):
            fn()
            kick()
        return run

    # Previous on release; held for 1.5 seconds it saves the song instead.
    hold_and_tap(hat.top_left, tap=lambda: radio.step(-1), hold=radio.save_favorite, kick=kick)
    hat.bottom_left.when_pressed = act(lambda: radio.step(+1))
    # Volume up: once per press, and again every half second while held.
    hat.top_right.hold_time, hat.top_right.hold_repeat = 0.5, True
    hat.top_right.when_pressed = act(lambda: radio.change_volume(+VOLUME_STEP))
    hat.top_right.when_held = act(lambda: radio.change_volume(+VOLUME_STEP))
    # Volume down on release; held for 1.5 seconds it stops instead.
    hold_and_tap(hat.bottom_right, tap=lambda: radio.change_volume(-VOLUME_STEP), hold=radio.stop, kick=kick)


def hold_and_tap(button, tap, hold, kick, hold_time=1.5):
    """A quick press does tap() when let go; holding does hold() once, and no tap."""
    held = {"yes": False}
    button.hold_time = hold_time

    def pressed(*_):
        held["yes"] = False

    def on_hold(*_):
        held["yes"] = True
        hold()
        kick()

    def released(*_):
        if not held["yes"]:
            tap()
            kick()

    button.when_pressed = pressed
    button.when_held = on_hold
    button.when_released = released


def main(argv):
    if len(argv) > 2 and argv[1] == "--preview":
        hat = Preview(argv[2])
    else:
        hat = HatMini(flip=FLIP)
    radio = Radio(Api())
    wake = threading.Event()
    stopping = threading.Event()

    def finish(*_):
        stopping.set()
        wake.set()

    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    if not isinstance(hat, Preview):
        wire_buttons(hat, radio, wake.set)

    shown, lit, colour, next_poll = None, None, None, 0.0
    try:
        while not stopping.is_set():
            now = time.time()
            if radio.tune_due():
                next_poll = 0.0
            if now >= next_poll:
                radio.refresh()
                next_poll = now + 1.0
            view, toast, screen_on = radio.frame()
            key = json.dumps([view, toast], sort_keys=True)
            if screen_on and key != shown:
                hat.show(render(view, toast))
                shown = key
            if screen_on != lit:
                hat.screen(screen_on)
                lit = screen_on
            if view["led"] != colour:
                hat.light(view["led"])
                colour = view["led"]
            wake.wait(0.2)
            wake.clear()
    finally:
        hat.close()


if __name__ == "__main__":
    main(sys.argv)
