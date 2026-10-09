#!/bin/sh
# Run by Raspotify on every Spotify event.
# 1. Tell the preset page what Spotify is doing, so it can show it.
# 2. When Spotify starts playing, hand over: stop the radio so the two never
#    play over each other.
case "$PLAYER_EVENT" in
  playing|started|paused|stopped|track_changed|session_disconnected|unavailable)
    /usr/bin/curl -s -m 3 -X POST http://127.0.0.1/api/spotify \
      --data-urlencode "event=$PLAYER_EVENT" \
      --data-urlencode "name=${NAME:-}" \
      --data-urlencode "artists=${ARTISTS:-}" \
      --data-urlencode "show=${SHOW_NAME:-}" >/dev/null 2>&1 || true
    ;;
esac
case "$PLAYER_EVENT" in
  playing|started|play_request_id_changed)
    /usr/bin/curl -s -m 3 -X POST http://127.0.0.1/api/yield >/dev/null 2>&1 || true
    /usr/bin/mpc -q --host 127.0.0.1 stop >/dev/null 2>&1 || true
    ;;
esac
exit 0
