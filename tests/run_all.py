#!/usr/bin/env python3
"""Run every check:  python3 tests/run_all.py

Needs python3-flask and python3-mpd (both are on the Pi after install.sh).
Nothing here touches MPD, Spotify, the network, or the real presets.
"""
import os, subprocess, sys

here = os.path.dirname(os.path.abspath(__file__))
failed = []
for name in ("test_app.py", "test_giveup.py", "test_spotify.py", "test_favorites.py", "test_display.py", "test_resume.py"):
    run = subprocess.run([sys.executable, os.path.join(here, name)], capture_output=True, text=True)
    passed = [line for line in run.stdout.splitlines() if line.endswith("passed")]
    if run.returncode == 0:
        print(f"OK    {name}: {len(passed)} group(s) of checks passed")
    else:
        failed.append(name)
        print(f"FAIL  {name}")
        print("      " + "\n      ".join((run.stderr or run.stdout).strip().splitlines()[-6:]))
print()
print("All checks passed." if not failed else f"{len(failed)} file(s) failed: {', '.join(failed)}")
sys.exit(1 if failed else 0)
