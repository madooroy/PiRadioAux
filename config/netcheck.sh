#!/bin/bash
# PiRadio network check. piradio-netcheck.timer runs it every minute.
#
# The Pi 4's Wi-Fi can hang while the rest of the Pi keeps running: the page
# can't be reached and the radio goes quiet, but nothing has crashed, so the
# hardware watchdog never steps in. This checks that the router answers, and
# when it doesn't, for so many minutes in a row:
#    3  restarts the network
#    5  reloads the Wi-Fi driver
#    8  restarts the Pi (at most once every 6 hours, so a router that is
#       simply switched off doesn't cause a restart every few minutes)
#   30, 60, ...  restarts the network again
# Each step is written to /var/lib/piradio-netcheck/actions.log, which the
# health check reads.
set -u

STATE_DIR=${PIRADIO_NETCHECK_STATE:-/run/piradio-netcheck}
LOG=${PIRADIO_NETCHECK_LOG:-/var/lib/piradio-netcheck/actions.log}
LAST_REBOOT=${PIRADIO_NETCHECK_LAST_REBOOT:-/var/lib/piradio-netcheck/last-restart}
NOW=${PIRADIO_NOW:-$(date +%s)}
UPTIME_S=${PIRADIO_UPTIME:-$(cut -d. -f1 /proc/uptime)}
RUN=${PIRADIO_NETCHECK_RUN:-}     # "echo" for a dry run: print the actions instead
PAUSE=${PIRADIO_NETCHECK_PAUSE:-3}
REBOOT_GAP=$((6 * 3600))

mkdir -p "$STATE_DIR" "$(dirname "$LOG")" "$(dirname "$LAST_REBOOT")"
# Give the network time to come up after a start.
[ "$UPTIME_S" -ge 180 ] || exit 0

reachable() {
  if [ -n "${PIRADIO_NETCHECK_PING:-}" ]; then   # for tests: "ok" or "fail"
    [ "$PIRADIO_NETCHECK_PING" = ok ]
    return
  fi
  local gw
  gw=$(ip -4 route show default 2>/dev/null | awk '/^default/ {print $3; exit}')
  [ -n "$gw" ] || return 1
  ping -c 2 -W 2 -q "$gw" >/dev/null 2>&1
}

note() {
  echo "$(date -d "@$NOW" '+%F %T') $*" >> "$LOG"
  logger -t piradio-netcheck "$*" 2>/dev/null || true
}

restart_network() {
  $RUN nmcli networking off
  sleep "$PAUSE"
  $RUN nmcli networking on
}

COUNT=$STATE_DIR/failed-minutes
if reachable; then
  if [ -s "$COUNT" ] && [ "$(cat "$COUNT")" -ge 3 ]; then
    note "connection back after $(cat "$COUNT") minute(s)"
  fi
  rm -f "$COUNT"
  exit 0
fi

n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$COUNT"

case $n in
  3)
    note "router unreachable for 3 minutes: restarting the network"
    restart_network
    ;;
  5)
    note "still unreachable: reloading the Wi-Fi driver"
    $RUN modprobe -r brcmfmac_wcc 2>/dev/null
    $RUN modprobe -r brcmfmac
    sleep "$PAUSE"
    $RUN modprobe brcmfmac
    ;;
  8)
    last=$(cat "$LAST_REBOOT" 2>/dev/null || echo 0)
    if [ $((NOW - last)) -lt $REBOOT_GAP ]; then
      note "still unreachable: not restarting the Pi again so soon (the last restart was less than 6 hours ago)"
    else
      echo "$NOW" > "$LAST_REBOOT"
      note "still unreachable after 8 minutes: restarting the Pi"
      $RUN systemctl reboot
    fi
    ;;
  *)
    if [ $((n % 30)) -eq 0 ]; then
      note "still unreachable after $n minutes: restarting the network"
      restart_network
    fi
    ;;
esac
exit 0
