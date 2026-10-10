"""Saving, listing and removing favorite songs:  python3 tests/test_favorites.py"""
import json, os, sys, tempfile

t = tempfile.mkdtemp()
os.environ["PIRADIO_PRESETS"] = os.path.join(t, "presets.json")
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A

c = A.app.test_client()
assert c.post("/api/favorites", json={"title": "---"}).status_code == 400 and c.post("/api/favorites", json={}).status_code == 400
c.post("/api/favorites", json={"title": "Boston \u2013 More Than A Feeling", "station": "WXYZ 92.5"})
# the same song again, typed differently, is not saved twice and keeps the first entry
r = c.post("/api/favorites", json={"title": "boston  \u2013  more than a feeling", "station": "x"}).get_json()["favorites"]
assert len(r) == 1 and r[0]["station"] == "WXYZ 92.5", r
assert A.is_favorite("BOSTON \u2013 More than a feeling") and not A.is_favorite("") and not A.is_favorite("Other")
assert c.get("/api/favorites").get_json()["favorites"][0]["at"].endswith("Z")
assert c.delete("/api/favorites", json={"title": "Boston \u2013 More Than A Feeling"}).get_json()["favorites"] == []
# a damaged file is treated as empty, not as an error
open(os.path.join(t, "favorites.json"), "w").write("{broken")
assert A.load_favorites() == [] and c.get("/api/favorites").status_code == 200
# export of presets is a downloadable file
json.dump([{"name": "A", "url": "http://x/a"}], open(os.path.join(t, "presets.json"), "w"))
r = c.get("/api/presets/export")
assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and json.loads(r.data) == [{"name": "A", "url": "http://x/a"}]
print("favorites and export checks passed")
