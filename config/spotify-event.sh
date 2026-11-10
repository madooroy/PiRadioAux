#!/bin/sh
# Run by Raspotify on every Spotify event.
# 1. Tell the preset page what Spotify is doing, so it can show it.
# 2. When Spotify starts playing, hand over: stop the radio so the two never
#    play over each other.
# The page's port comes from /etc/default/piradio (PIRADIO_PORT, 80 if unset).
PORT=$(sed -n 's/^PIRADIO_PORT="\{0,1\}\([0-9]*\)"\{0,1\}$/\1/p' /etc/default/piradio 2>/dev/null | tail -n 1)
PAGE="http://127.0.0.1:${PORT:-80}"
# Up to 10 seconds: the page can be busy for a few seconds while it restarts a
# station, and a missed hand-over would let the radio start again over Spotify.
case "$PLAYER_EVENT" in
  playing|started|paused|stopped|track_changed|session_disconnected|unavailable)
    /usr/bin/curl -s -m 10 -X POST "$PAGE/api/spotify" \
      --data-urlencode "event=$PLAYER_EVENT" \
      --data-urlencode "name=${NAME:-}" \
      --data-urlencode "artists=${ARTISTS:-}" \
      --data-urlencode "show=${SHOW_NAME:-}" >/dev/null 2>&1 || true
    ;;
esac
case "$PLAYER_EVENT" in
  playing|started|play_request_id_changed)
    /usr/bin/curl -s -m 10 --retry 2 --retry-all-errors -X POST "$PAGE/api/yield" >/dev/null 2>&1 || true
    /usr/bin/mpc -q --host 127.0.0.1 stop >/dev/null 2>&1 || true
    ;;
esac
exit 0
