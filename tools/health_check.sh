#!/usr/bin/env bash
# PiRadio health check - run on the Pi:
#   sudo bash ~/PiRadioAux/tools/health_check.sh
# Read-only: it looks at the services, the installed files, the audio output,
# the page, updates, the network and the SD card, and changes nothing.
set -u

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0" >&2
    exit 1
fi

APP_DIR=/opt/piradio
SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PORT=80
[[ -f /etc/default/piradio ]] && PORT=$(sed -n 's/^PIRADIO_PORT="\{0,1\}\([0-9]*\)"\{0,1\}$/\1/p' /etc/default/piradio | tail -n 1)
PORT=${PORT:-80}

PASS=0; WARN=0; FAIL=0
ok()      { echo "  OK    $*"; PASS=$((PASS + 1)); }
warn()    { echo "  WARN  $*"; WARN=$((WARN + 1)); }
bad()     { echo "  FAIL  $*"; FAIL=$((FAIL + 1)); }
info()    { echo "        $*"; }
section() { echo; echo "== $* =="; }

section "Services"
for svc in mpd raspotify piradio; do
    case $svc in
        mpd) what="radio player" ;;
        raspotify) what="Spotify Connect" ;;
        *) what="preset page" ;;
    esac
    if [[ $(systemctl is-active "$svc" 2>/dev/null) == active ]]; then
        ok "$svc ($what) is running"
    else
        bad "$svc ($what) is not running - see: journalctl -u $svc -b --no-pager | tail -n 30"
    fi
    [[ $(systemctl is-enabled "$svc" 2>/dev/null) == enabled ]] || bad "$svc does not start on boot - run install.sh again"
done
restarts=$(systemctl show -p NRestarts --value piradio 2>/dev/null)
if [[ ${restarts:-0} -eq 0 ]]; then
    ok "the preset page has not crashed since it was started"
else
    warn "the preset page restarted itself $restarts time(s) - see: journalctl -u piradio -b --no-pager | tail -n 40"
fi

section "Installed files ($APP_DIR)"
if [[ -f $APP_DIR/app.py && -f $APP_DIR/templates/index.html ]]; then
    ok "app files present"
    if [[ -f $SRC/app.py ]]; then
        if cmp -s "$SRC/app.py" "$APP_DIR/app.py" && cmp -s "$SRC/display.py" "$APP_DIR/display.py" && cmp -s "$SRC/templates/index.html" "$APP_DIR/templates/index.html"; then
            ok "installed version matches $SRC"
        else
            warn "installed version differs from $SRC (copied but not installed?) - run: sudo bash $SRC/install.sh"
        fi
    fi
else
    bad "app files missing - run install.sh"
fi
if python3 -c "import flask, mpd" 2>/dev/null; then
    ok "Python libraries present (flask, mpd)"
else
    bad "Python libraries missing - run install.sh"
fi
if [[ -f $APP_DIR/presets.json ]]; then
    count=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(len(d) if isinstance(d, list) else -1)" "$APP_DIR/presets.json" 2>/dev/null)
    if [[ ${count:--1} -ge 0 ]]; then
        ok "presets file readable: $count preset(s)"
    else
        bad "presets file is damaged ($APP_DIR/presets.json) - use Import on the Edit presets page, or delete it and run install.sh"
    fi
    owner=$(stat -c %U "$APP_DIR/presets.json")
    [[ $owner == piradio ]] || warn "presets file belongs to '$owner', not 'piradio': saving presets may fail - run install.sh"
else
    bad "no presets file - run install.sh"
fi
if [[ -f $APP_DIR/favorites.json ]]; then
    info "favorites: $(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$APP_DIR/favorites.json" 2>/dev/null || echo "unreadable") saved song(s)"
fi

section "Preset page"
name=$(sed -n 's/^PIRADIO_NAME="\(.*\)"$/\1/p' /etc/default/piradio 2>/dev/null)
info "name: ${name:-PiRadio}   address: http://$(hostname).local   or   http://$(hostname -I 2>/dev/null | awk '{print $1}')"
status=$(curl -s -m 5 -o /tmp/piradio-health.$$ -w '%{http_code}' "http://127.0.0.1:$PORT/api/status" 2>/dev/null)
if [[ $status == 200 ]]; then
    ok "page answers on port $PORT"
    python3 - /tmp/piradio-health.$$ <<'PY' 2>/dev/null | sed 's/^/        /'
import json, sys
s = json.load(open(sys.argv[1]))
if s.get("playing"):
    cur = s.get("current")
    station = s["presets"][cur]["name"] if cur is not None and cur < len(s["presets"]) else "radio"
    print("now: %s%s%s" % (station, " - " + s["title"] if s.get("title") else "", "  (reconnecting)" if s.get("recovering") else ""))
elif s.get("spotify"):
    print("now: Spotify (%s) %s" % (s["spotify"]["state"], s["spotify"].get("title", "")))
else:
    print("now: nothing playing")
if s.get("notice"):
    print("notice on the page: " + s["notice"])
if s.get("error"):
    print("player message: " + s["error"])
PY
elif [[ $status == 503 ]]; then
    bad "page answers, but it cannot reach the radio player (MPD)"
else
    bad "page does not answer on port $PORT (HTTP ${status:-none}) - see: journalctl -u piradio -b --no-pager | tail -n 30"
fi
rm -f /tmp/piradio-health.$$
give_ups=$(journalctl -u piradio -b --no-pager 2>/dev/null | grep -c "giving up on")
restarts_stream=$(journalctl -u piradio -b --no-pager 2>/dev/null | grep -c "stream stopped; restarting")
info "since the Pi started: $restarts_stream stream reconnection(s), $give_ups station(s) given up on"

section "Radio player (MPD)"
if mpc -q --host 127.0.0.1 status >/dev/null 2>&1; then
    ok "answers on this Pi"
else
    bad "does not answer - see: journalctl -u mpd -b --no-pager | tail -n 30"
fi
listen=$(ss -ltn 2>/dev/null | awk '$4 ~ /:6600$/ {print $4}' | tr '\n' ' ')
if [[ -z $listen ]]; then
    warn "nothing is listening on port 6600"
elif grep -qE '(^| )(0\.0\.0\.0|\*|\[::\]):6600' <<<"$listen"; then
    bad "the player accepts commands from the whole network ($listen) - run install.sh again, then restart the Pi"
else
    ok "only reachable from this Pi ($listen)"
fi
if [[ $(systemctl is-enabled mpd.socket 2>/dev/null) == enabled ]]; then
    warn "mpd.socket is enabled: the player may open to the network after a restart - run install.sh again"
fi

section "Audio output"
if [[ -f /etc/asound.conf ]]; then
    if grep -q "PiRadio (shared output)" /etc/asound.conf; then mode="shared (software mixer)"
    elif grep -q "PiRadio (direct output)" /etc/asound.conf; then mode="direct"
    else mode="not set up by PiRadio"; fi
    card=$(sed -n 's/.*hw:CARD=\([A-Za-z0-9_]*\).*/\1/p' /etc/asound.conf | head -n 1)
    info "mode: $mode   device: ${card:-unknown}"
    if [[ $mode == "not set up by PiRadio" ]]; then
        warn "/etc/asound.conf was not written by PiRadio - run install.sh again"
    elif [[ -n $card ]] && aplay -l 2>/dev/null | grep -q "^card [0-9]*: $card "; then
        ok "the configured device ($card) is present"
    else
        bad "the configured device (${card:-unknown}) is not present - is the adapter unplugged? Plug it in, or run install.sh again"
    fi
    usb=$(aplay -l 2>/dev/null | awk -F'[ :]+' '/^card/ && /USB/ {print $3; exit}')
    if [[ -n $usb && $usb != "$card" ]]; then
        warn "a USB audio adapter ($usb) is plugged in but not in use - run install.sh again to switch to it"
    fi
else
    bad "/etc/asound.conf is missing - run install.sh"
fi
if speaker=$(amixer -c "${card:-0}" 2>/dev/null | grep -oE '\[[0-9]+%\]' | head -n 1); then
    [[ -n $speaker ]] && info "hardware level: $speaker (the page's slider and the Spotify app set the listening volume)"
fi

section "Spotify Connect"
if [[ -f /etc/raspotify/conf ]] && grep -q '^LIBRESPOT_NAME=' /etc/raspotify/conf; then
    ok "speaker name: $(sed -n 's/^LIBRESPOT_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' /etc/raspotify/conf | tail -n 1)"
else
    bad "not configured by PiRadio - run install.sh"
fi
if [[ -x /usr/local/bin/piradio-spotify-event ]]; then
    ok "hand-over script installed"
    if [[ -f $SRC/config/spotify-event.sh ]] && ! cmp -s "$SRC/config/spotify-event.sh" /usr/local/bin/piradio-spotify-event; then
        warn "hand-over script differs from $SRC - run install.sh again"
    fi
else
    bad "hand-over script missing: radio and Spotify would play over each other - run install.sh"
fi
if [[ -f /etc/sudoers.d/piradio ]] && visudo -cqf /etc/sudoers.d/piradio 2>/dev/null; then
    ok "the page may restart Spotify Connect and power off the Pi"
else
    bad "permission file /etc/sudoers.d/piradio missing or invalid: presets cannot end a Spotify session, and Power off will not work - run install.sh"
fi

section "Automatic updates"
if [[ $(systemctl is-enabled apt-daily-upgrade.timer 2>/dev/null) == enabled ]]; then
    next=$(systemctl show -p NextElapseUSecRealtime --value apt-daily-upgrade.timer 2>/dev/null)
    ok "on; next run: ${next:-unknown}"
else
    warn "off (sudo systemctl enable --now apt-daily-upgrade.timer turns them on)"
fi
log=/var/log/unattended-upgrades/unattended-upgrades.log
if [[ -f $log ]]; then
    last=$(grep -E 'Packages that will be upgraded|No packages found that can be upgraded|All upgrades installed' "$log" | tail -n 1)
    [[ -n $last ]] && info "last run: ${last:0:150}"
    # Only errors since the last run that finished count: an old one (often "Lock could
    # not be acquired", from an update starting while something else was installing)
    # is history once a later run has succeeded.
    recent=$(awk '/Packages that will be upgraded|No packages found that can be upgraded|All upgrades installed/ {buf=""; next} {buf = buf $0 "\n"} END {printf "%s", buf}' "$log" | grep ERROR | tail -n 2)
    if [[ -n $recent ]]; then
        warn "the last update run reported a problem:"; info "${recent:0:300}"
    fi
else
    info "no update has run yet"
fi
[[ -f /var/run/reboot-required ]] && info "a restart is pending (it happens by itself at 3 AM)"
unfinished=$(dpkg --audit 2>/dev/null | awk '/^ [a-z0-9]/ {print $1}' | tr '\n' ' ')
if [[ -n $unfinished ]]; then
    bad "an install was left unfinished: ${unfinished}- see: sudo dpkg --configure -a"
else
    ok "no unfinished installs"
fi
if grep -q '^auto_initramfs=0' /boot/firmware/config.txt 2>/dev/null && [[ ! -f /etc/initramfs-tools/conf.d/piradio-fast-boot ]]; then
    bad "--fast-boot is on without its initramfs setting: kernel updates will fail - run: sudo bash $SRC/install.sh"
fi

section "Network"
if command -v iw >/dev/null && iw dev wlan0 link 2>/dev/null | grep -q '^Connected'; then
    ssid=$(iw dev wlan0 link | sed -n 's/^\s*SSID: //p')
    signal=$(iw dev wlan0 link | sed -n 's/^\s*signal: \(-[0-9]*\).*/\1/p')
    if [[ -n $signal && $signal -lt -75 ]]; then
        warn "Wi-Fi signal is weak ($signal dBm on $ssid): streams may drop - move the Pi or the router"
    else
        ok "Wi-Fi connected to $ssid (${signal:-?} dBm)"
    fi
elif ip -4 addr show eth0 2>/dev/null | grep -q 'inet '; then
    ok "connected by network cable"
else
    warn "no Wi-Fi or cable connection found"
fi
if [[ $(systemctl is-active avahi-daemon 2>/dev/null) == active ]]; then
    ok "the name $(hostname).local is being announced"
else
    warn "avahi-daemon is not running: $(hostname).local will not work, use the address above"
fi
if curl -s -m 6 -o /dev/null -A PiRadioAux/1.0 "https://de1.api.radio-browser.info/json/stats" 2>/dev/null; then
    ok "internet reachable (station directory answers)"
else
    warn "the station directory did not answer: Find a station may not work right now"
fi

section "Staying on"
if [[ -d /sys/class/net/wlan0 ]]; then
    ps=$(iw dev wlan0 get power_save 2>/dev/null | sed -n 's/.*: //p')
    if [[ $ps == off ]]; then
        ok "Wi-Fi power saving is off"
    else
        warn "Wi-Fi power saving is ${ps:-unknown}: it can make the Wi-Fi hang - run: sudo bash $SRC/install.sh"
    fi
fi
if [[ $(systemctl is-active piradio-netcheck.timer 2>/dev/null) == active ]]; then
    ok "network check runs every minute (restarts a hung Wi-Fi)"
else
    bad "network check is not running - run: sudo bash $SRC/install.sh"
fi
wd=$(systemctl show -p RuntimeWatchdogUSec --value 2>/dev/null)
if [[ -n $wd && $wd != 0 && $wd != infinity ]]; then
    ok "hardware watchdog on ($wd): a frozen Pi restarts by itself"
else
    warn "hardware watchdog is off - run: sudo bash $SRC/install.sh"
fi
nclog=/var/lib/piradio-netcheck/actions.log
if [[ -s $nclog ]]; then
    since=$(date -d '7 days ago' '+%F %T')
    steps=$(awk -v s="$since" '($1" "$2) >= s && !/connection back/' "$nclog" | wc -l)
    if [[ $steps -gt 0 ]]; then
        warn "the network check stepped in $steps time(s) in the last 7 days:"
        awk -v s="$since" '($1" "$2) >= s' "$nclog" | tail -n 6 | while read -r line; do info "$line"; done
    else
        ok "the network check has not needed to step in for 7 days"
    fi
else
    ok "the network check has never needed to step in"
fi
boots=$(journalctl --list-boots --no-pager 2>/dev/null | tail -n +2 | wc -l)
if [[ -d /var/log/journal ]]; then
    info "saved log is on: $boots start(s) recorded; after an unexpected stop, see: journalctl -b -1 -n 40 --no-pager"
fi

if grep -q '^PIRADIO_DISPLAY="1"' /etc/default/piradio 2>/dev/null; then
    section "Screen and buttons (Display HAT Mini)"
    if [[ -e /dev/spidev0.1 ]]; then
        ok "SPI is on (the screen's connection)"
    else
        bad "SPI is off - restart the Pi once after install.sh --display (sudo reboot)"
    fi
    if [[ $(systemctl is-active piradio-display 2>/dev/null) == active ]]; then
        ok "piradio-display (screen and buttons) is running"
    else
        bad "piradio-display is not running - see: journalctl -u piradio-display -b --no-pager | tail -n 30"
    fi
    if python3 -c "import PIL, numpy, spidev, gpiozero" 2>/dev/null; then
        ok "Python libraries present (pil, numpy, spidev, gpiozero)"
    else
        bad "Python libraries for the screen missing - run: sudo bash $SRC/install.sh --display"
    fi
    restarts=$(systemctl show -p NRestarts --value piradio-display 2>/dev/null)
    [[ ${restarts:-0} -eq 0 ]] || warn "the screen program restarted itself $restarts time(s) - see: journalctl -u piradio-display -b --no-pager | tail -n 40"
    temp=$(vcgencmd measure_temp 2>/dev/null | sed -n "s/temp=\([0-9]*\).*/\1/p")
    if [[ -n $temp ]]; then
        if [[ $temp -lt 70 ]]; then ok "chip temperature ${temp} C under the screen"; else warn "chip temperature ${temp} C - the screen may be trapping heat"; fi
    fi
fi

section "Start-up"
boot=/boot/firmware
if grep -qw 'cloud-init=disabled' "$boot/cmdline.txt" 2>/dev/null; then
    info "--fast-boot settings are applied (undo: sudo bash $SRC/install.sh --undo-fast-boot)"
else
    info "--fast-boot is not applied (optional, see the README)"
fi
ready=$(journalctl -u piradio -b -o short-monotonic --no-pager 2>/dev/null | sed -n 's/^\[ *\([0-9.]*\)\].*Started piradio.*/\1/p' | head -n 1)
# Only meaningful when the page came up as part of start-up. After an install or an
# update in the same session, the service's first start is minutes in and says nothing.
if [[ -n $ready && ${ready%.*} -lt 120 ]]; then
    info "the preset page started ${ready%.*} s after Linux did (this boot)"
fi

section "SD card and system"
root_dev=$(findmnt -no SOURCE /)
if findmnt -no OPTIONS / | grep -qE '(^|,)rw(,|$)'; then
    ok "root filesystem mounted read-write ($root_dev)"
else
    bad "root filesystem is read-only: presets and favorites cannot be saved"
fi
state=$(tune2fs -l "$root_dev" 2>/dev/null | sed -n 's/^Filesystem state: *//p')
if [[ $state == clean ]]; then
    ok "ext4 state: clean"
else
    bad "ext4 state: ${state:-unknown} - the card may need checking or replacing"
fi
errors=$(dmesg 2>/dev/null | grep -iE 'EXT4-fs (error|warning)|mmc[0-9].*(error|timeout)|I/O error' | tail -n 3)
if [[ -z $errors ]]; then
    ok "no SD card or filesystem errors in the kernel log"
else
    bad "the kernel reports storage errors:"; info "$errors"
fi
used=$(df --output=pcent / | tail -n 1 | tr -dc '0-9')
if [[ ${used:-0} -lt 85 ]]; then ok "disk ${used}% used"; else warn "disk ${used}% used"; fi
if command -v vcgencmd >/dev/null; then
    throttled=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
    if [[ $throttled == 0x0 ]]; then
        ok "power supply: no under-voltage or throttling since boot"
    elif [[ -n $throttled ]]; then
        t=$((throttled))
        (( t & 0x1 ))     && bad "UNDER-VOLTAGE NOW - use the official power supply for this Pi"
        (( t & 0x10000 )) && warn "under-voltage occurred since boot (weak supply or cable)"
        (( t & 0x40000 )) && warn "the processor was throttled since boot"
        (( t & 0x80000 )) && warn "the soft temperature limit was reached since boot"
    fi
    info "$(vcgencmd measure_temp 2>/dev/null)"
fi
if [[ $(timedatectl show -p NTPSynchronized --value 2>/dev/null) == yes ]]; then
    ok "clock synchronised"
else
    warn "clock not synchronised: the 11 PM update and favorite dates may be off"
fi
info "up $(uptime -p 2>/dev/null | sed 's/^up //')"

echo
echo "Result: $PASS OK, $WARN warning(s), $FAIL failure(s)"
[[ $FAIL -eq 0 ]]
