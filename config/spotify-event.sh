#!/bin/sh
# Run by Raspotify on every Spotify event. When Spotify starts playing,
# hand over: tell the preset page (so it stops keeping the radio alive)
# and stop the radio, so the two never play over each other.
case "$PLAYER_EVENT" in
  playing|started|play_request_id_changed)
    /usr/bin/curl -s -m 3 -X POST http://127.0.0.1/api/yield >/dev/null 2>&1 || true
    /usr/bin/mpc -q --host 127.0.0.1 stop >/dev/null 2>&1 || true
    ;;
esac
exit 0
