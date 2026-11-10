"""Who may use the page, and what it will fetch.

Runs the main checks first (it reuses their stand-in player):  python3 tests/test_security.py
"""
import os, runpy, socket
g = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_app.py"))
A, c = g["A"], g["c"]
A.threading.Thread = g["_RealThread"]; A.mpd = g["fake_mpd"]
me = socket.gethostname().lower()
PHONE = {"REMOTE_ADDR": "192.168.1.20"}          # a phone on the home network
OK = {"X-PiRadio": "1"}

# --- the address used must be this Pi's own
for host in ("192.168.1.50", "192.168.1.50:80", "[fe80::1]", "[::1]:80", "localhost",
             me, f"{me}.local", f"{me}.lan", f"{me}.home.arpa", f"{me.upper()}.LOCAL"):
    assert c.get("/api/status", headers={"Host": host}, environ_base=PHONE).status_code == 200, host
for host in ("evil.example.com", f"{me}.evil.example.com", "attacker.local", f"x{me}.local"):
    r = c.get("/api/status", headers={"Host": host}, environ_base=PHONE)
    assert r.status_code == 403, host                       # a DNS-rebinding page is turned away
print("host checks passed")

# --- changes need the X-PiRadio header (the page sends it; other sites can't)
assert c.post("/api/stop", environ_base=PHONE).status_code == 403
assert c.post("/api/poweroff", environ_base=PHONE).status_code == 403
assert c.put("/api/presets", json=[], environ_base=PHONE).status_code == 403
assert c.post("/api/stop", headers=OK, environ_base=PHONE).status_code == 200
assert c.get("/api/status", environ_base=PHONE).status_code == 200           # reading needs nothing
assert c.post("/api/stop").status_code == 200                                # this Pi itself (display, Spotify hook)
print("header checks passed")

# --- the Spotify hand-over is for this Pi only
assert c.post("/api/yield", headers=OK, environ_base=PHONE).status_code == 403
assert c.post("/api/spotify", headers=OK, environ_base=PHONE, data={"event": "playing"}).status_code == 403
assert c.post("/api/yield").status_code == 200
print("local-only checks passed")

# --- the page won't fetch this Pi's own services or link-local addresses for anyone
A._probe = g["_real_probe"]                      # the main checks swap in a stand-in
for url in ("http://127.0.0.1:6600/", "http://localhost/api/status", "http://[::1]/", "http://169.254.169.254/latest"):
    r = c.post("/api/check", json={"url": url}, headers=OK, environ_base=PHONE).get_json()
    assert r["level"] == "bad" and "this Pi itself" in r["message"], (url, r)
try:
    A._open(A.urllib.request.Request("http://127.0.0.1/feed.json"), timeout=2); raise AssertionError("fetched a loopback feed")
except ValueError:
    pass
class Redir(A._CheckedRedirects): pass
try:
    Redir().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/secret"); raise AssertionError("followed")
except ValueError:
    pass
print("fetch checks passed")

# --- size limits
long = "http://x/" + "a" * 2100
r = c.put("/api/presets", json=[{"name": "L", "url": long}], headers=OK, environ_base=PHONE)
assert r.status_code == 400 and "too long" in r.get_json()["error"]
assert c.post("/api/check", json={"url": long}, headers=OK, environ_base=PHONE).status_code == 400
big = '{"title": "' + "x" * 300_000 + '"}'
r = c.post("/api/favorites", data=big, headers={**OK, "Content-Type": "application/json"}, environ_base=PHONE)
assert r.status_code == 413, r.status_code
print("size checks passed")
