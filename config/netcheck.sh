#!/bin/bash
# PiRadio network check. piradio-netcheck.timer runs it every minute.
#
# The Pi 4's Wi-Fi can hang while the rest of the Pi keeps running: the page
# can't be reached and the radio goes quiet, but nothing has crashed, so the
# hardware watchdog never steps in. This checks that the router answers, and
# when it doesn't, for so many minutes in a row:
#    1  if the saved Wi-Fi settings have gone missing, puts back the copy
#       kept below
#    3  restarts the network
#    5  reloads the Wi-Fi driver
#    8  restarts the Pi (at most once every 6 hours, so a router that is
#       simply switched off doesn't cause a restart every few minutes)
#   30, 60, ...  restarts the network again
# While the router answers, it keeps a copy of the Wi-Fi settings (only
# rewritten when they change). The settings once came back empty after a
# shutdown, leaving the Pi with no Wi-Fi to join.
# Each step is written to /var/lib/piradio-netcheck/actions.log, which the
# health check reads.
set -u

STATE_DIR=${PIRADIO_NETCHECK_STATE:-/run/piradio-netcheck}
LIB=${PIRADIO_NETCHECK_LIB:-/var/lib/piradio-netcheck}
LOG=${PIRADIO_NETCHECK_LOG:-$LIB/actions.log}
LAST_REBOOT=${PIRADIO_NETCHECK_LAST_REBOOT:-$LIB/last-restart}
BACKUP=$LIB/wifi-backup
ROOT=${PIRADIO_NETCHECK_ROOT:-}   # for tests: a folder standing in for /
SYS=${PIRADIO_NETCHECK_SYS:-/sys}
SETTINGS_DIRS="etc/NetworkManager/system-connections etc/netplan"
NOW=${PIRADIO_NOW:-$(date +%s)}
UPTIME_S=${PIRADIO_UPTIME:-$(cut -d. -f1 /proc/uptime)}
RUN=${PIRADIO_NETCHECK_RUN:-}     # "echo" for a dry run: print the actions instead
PAUSE=${PIRADIO_NETCHECK_PAUSE:-3}
REBOOT_GAP=$((6 * 3600))

umask 077
mkdir -p "$STATE_DIR" "$LIB" "$(dirname "$LOG")" "$(dirname "$LAST_REBOOT")"
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

has_wifi_settings() {
  if [ -n "${PIRADIO_NETCHECK_HAS_WIFI:-}" ]; then   # for tests: "yes" or "no"
    [ "$PIRADIO_NETCHECK_HAS_WIFI" = yes ]
    return
  fi
  nmcli -t -f TYPE connection show 2>/dev/null | grep -q '802-11-wireless'
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

reload_wifi_driver() {
  # Newer kernels split the driver: brcmfmac plus a chip-family module
  # (brcmfmac_cyw on the Pi 4) that holds it. Those have to go first.
  local holder
  for holder in "$SYS"/module/brcmfmac/holders/*; do
    [ -e "$holder" ] && $RUN modprobe -r "$(basename "$holder")"
  done
  $RUN modprobe -r brcmfmac
  sleep "$PAUSE"
  $RUN modprobe brcmfmac   # loads the chip-family module again by itself
}

# Copy the Wi-Fi settings files that are there and not empty, when they differ
# from the copy already kept.
back_up_settings() {
  local dir f
  for dir in $SETTINGS_DIRS; do
    for f in "$ROOT/$dir"/*; do
      [ -f "$f" ] && [ -s "$f" ] || continue
      if ! cmp -s "$f" "$BACKUP/$dir/$(basename "$f")"; then
        mkdir -p "$BACKUP/$dir"
        cp -p "$f" "$BACKUP/$dir/"
        chmod 600 "$BACKUP/$dir/$(basename "$f")"   # holds the Wi-Fi password
      fi
    done
  done
}

# Put back each kept file that is now missing or empty. Returns 1 if there was
# nothing to put back.
restore_settings() {
  local dir f target restored=0
  for dir in $SETTINGS_DIRS; do
    for f in "$BACKUP/$dir"/*; do
      [ -s "$f" ] || continue
      target="$ROOT/$dir/$(basename "$f")"
      if [ ! -s "$target" ]; then
        mkdir -p "$ROOT/$dir"
        cp -p "$f" "$target"
        restored=1
      fi
    done
  done
  [ $restored = 1 ] || return 1
  sync
  # Settings kept in netplan become NetworkManager connections through netplan.
  if [ -n "$(ls -A "$ROOT/etc/netplan" 2>/dev/null)" ] && command -v netplan >/dev/null; then
    $RUN netplan generate
  fi
  $RUN nmcli connection reload
  $RUN nmcli device connect wlan0
}

COUNT=$STATE_DIR/failed-minutes
if reachable; then
  if [ -s "$COUNT" ] && [ "$(cat "$COUNT")" -ge 3 ]; then
    note "connection back after $(cat "$COUNT") minute(s)"
  fi
  rm -f "$COUNT" "$STATE_DIR/restore-tried"
  if [ -d /sys/class/net/wlan0 ] || [ -n "$ROOT" ]; then
    has_wifi_settings && back_up_settings
  fi
  exit 0
fi

n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$COUNT"

# Wi-Fi settings gone: nothing below can help until they are back.
if [ ! -e "$STATE_DIR/restore-tried" ] && ! has_wifi_settings; then
  touch "$STATE_DIR/restore-tried"
  if restore_settings; then
    note "the Wi-Fi settings were missing: put back the saved copy"
  elif [ -d "$BACKUP" ]; then
    note "the Wi-Fi settings are missing, and the saved copy has nothing to put back"
  else
    note "the Wi-Fi settings are missing, and no copy has been saved yet"
  fi
fi

case $n in
  3)
    note "router unreachable for 3 minutes: restarting the network"
    restart_network
    ;;
  5)
    note "still unreachable: reloading the Wi-Fi driver"
    reload_wifi_driver
    ;;
  8)
    last=$(cat "$LAST_REBOOT" 2>/dev/null || echo 0)
    if [ $((NOW - last)) -lt $REBOOT_GAP ]; then
      note "still unreachable: not restarting the Pi again so soon (the last restart was less than 6 hours ago)"
    else
      echo "$NOW" > "$LAST_REBOOT"
      note "still unreachable after 8 minutes: restarting the Pi"
      sync
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
