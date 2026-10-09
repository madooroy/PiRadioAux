#!/bin/bash
# PiRadioAux installer. Run on the Pi from inside the project folder:
#     sudo bash install.sh
# Options:
#     --name "Kitchen Radio"   the name shown on the page and in Spotify (default: PiRadio)
#     --hostname kitchenradio  also rename the Pi on the network (page becomes http://kitchenradio.local)
#     --fast-boot              shorten start-up for a Pi with no screen (see the README)
# Safe to run again after changing files or swapping the audio adapter.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash install.sh" >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR=/opt/piradio
NAME=""
NEW_HOST=""
FAST_BOOT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name)     NAME="${2:-}"; shift 2 ;;
    --hostname) NEW_HOST="${2:-}"; shift 2 ;;
    --fast-boot) FAST_BOOT=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
# Keep the name chosen on an earlier run unless a new one is given.
if [ -z "$NAME" ] && [ -f /etc/default/piradio ]; then
  NAME="$(sed -n 's/^PIRADIO_NAME="\(.*\)"$/\1/p' /etc/default/piradio)"
fi
NAME="${NAME:-PiRadio}"
if ! printf '%s' "$NAME" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$'; then
  echo "The name can use letters, numbers, spaces, dots, dashes and underscores (up to 32)." >&2
  exit 1
fi
if [ -n "$NEW_HOST" ] && ! printf '%s' "$NEW_HOST" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'; then
  echo "The hostname can use lowercase letters, numbers and dashes." >&2
  exit 1
fi

# An earlier version of this project was called PiTivoli. Carry such an
# install over: keep its presets, remove its old pieces.
if [ -d /opt/pitivoli ] || [ -f /etc/systemd/system/pitivoli.service ]; then
  echo "== Moving an earlier PiTivoli install over to PiRadio"
  systemctl disable --now pitivoli 2>/dev/null || true
  mkdir -p "$APP_DIR"
  if [ -f /opt/pitivoli/presets.json ] && [ ! -f "$APP_DIR/presets.json" ]; then
    cp /opt/pitivoli/presets.json "$APP_DIR/presets.json"
    echo "   Presets carried over."
  fi
  rm -f /etc/systemd/system/pitivoli.service /etc/sudoers.d/pitivoli \
        /usr/local/bin/pitivoli-spotify-event /etc/apt/apt.conf.d/52pitivoli-upgrades \
        /etc/systemd/system/apt-daily.timer.d/pitivoli.conf \
        /etc/systemd/system/apt-daily-upgrade.timer.d/pitivoli.conf
  if [ -f /etc/raspotify/conf ]; then
    sed -i '/# --- PiTivoli start/,/# --- PiTivoli end/d' /etc/raspotify/conf
  fi
  rm -rf /opt/pitivoli
  userdel pitivoli 2>/dev/null || true
  systemctl daemon-reload
fi

echo "== 1/6  Installing packages"
apt-get update
apt-get install -y mpd mpc alsa-utils curl python3-flask python3-mpd python3-waitress

echo "   Turning on automatic updates (nightly, around 11 PM)"
apt-get install -y unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF2'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF2
cat > /etc/apt/apt.conf.d/52piradio-upgrades <<'EOF2'
// PiRadio: update everything (system, radio player, Spotify Connect),
// not only security fixes, and restart overnight when an update needs it.
Unattended-Upgrade::Origins-Pattern {
    "origin=*";
};
Unattended-Upgrade::Remove-Unused-Dependencies "true";
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
EOF2
for t in apt-daily apt-daily-upgrade; do mkdir -p /etc/systemd/system/$t.timer.d; done
printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 22:30\nRandomizedDelaySec=10m\n' > /etc/systemd/system/apt-daily.timer.d/piradio.conf
printf '[Timer]\nOnCalendar=\nOnCalendar=*-*-* 23:00\nRandomizedDelaySec=10m\n' > /etc/systemd/system/apt-daily-upgrade.timer.d/piradio.conf
systemctl daemon-reload
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer

echo "== 2/6  Finding the audio output"
# Prefer a USB audio adapter; fall back to the Pi's own headphone jack.
CARD="$(aplay -l | awk -F'[ :]+' '/^card/ && /USB/ {print $3; exit}')"
if [ -z "$CARD" ]; then
  CARD="$(aplay -l | awk -F'[ :]+' '/^card/ && /Headphones/ {print $3; exit}')"
  echo "   No USB audio adapter found - using the built-in headphone jack ($CARD)."
  echo "   Plug the adapter in and run this script again to switch to it."
else
  echo "   Using USB audio adapter: $CARD"
fi
if [ -z "$CARD" ]; then
  echo "No audio output found. Check 'aplay -l'." >&2
  exit 1
fi
# Free the device while the output is set up and tested.
systemctl stop mpd raspotify 2>/dev/null || true

two_at_once() {  # can two programs play through the default output together?
  aplay -q -D default -t raw -f cd -d 2 /dev/zero >/dev/null 2>&1 &
  local first=$! result=0
  sleep 0.5
  aplay -q -D default -t raw -f cd -d 1 /dev/zero >/dev/null 2>&1 || result=1
  wait "$first" || result=1
  return $result
}

if [ "$CARD" = "Headphones" ]; then
  # The built-in jack mixes several programs by itself; a software mixer breaks it.
  sed "s/@CARD@/$CARD/g" "$HERE/config/asound-direct.conf" > /etc/asound.conf
  echo "   Output mode: direct"
else
  sed "s/@CARD@/$CARD/g" "$HERE/config/asound-shared.conf" > /etc/asound.conf
  if two_at_once; then
    echo "   Output mode: shared (radio and Spotify can hand over smoothly)"
  else
    sed "s/@CARD@/$CARD/g" "$HERE/config/asound-direct.conf" > /etc/asound.conf
    echo "   Output mode: direct (the shared mode did not work with this device)"
  fi
fi
if two_at_once; then
  echo "   Sound test: passed"
else
  echo "   Sound test: only one program can play at a time on this device."
  echo "   Radio and Spotify will still work; stop one before starting the other."
fi
# Hardware level to full; day-to-day volume is set from the web page / Spotify.
for ctl in PCM Speaker Headphone Master; do
  amixer -q -c "$CARD" sset "$ctl" 100% unmute 2>/dev/null || true
done
alsactl store 2>/dev/null || true

echo "== 3/6  Setting up the radio player (MPD)"
[ -f /etc/mpd.conf.orig ] || cp /etc/mpd.conf /etc/mpd.conf.orig
cp "$HERE/config/mpd.conf" /etc/mpd.conf
systemctl enable mpd
# Enabling mpd also enables its network listener; turn that off again so the
# player only answers on this Pi (bind_to_address in mpd.conf).
systemctl disable --now mpd.socket 2>/dev/null || true
systemctl restart mpd

echo "== 4/6  Setting up Spotify Connect (Raspotify)"
if ! dpkg -s raspotify >/dev/null 2>&1; then
  curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
fi
install -m 755 "$HERE/config/spotify-event.sh" /usr/local/bin/piradio-spotify-event
CONF=/etc/raspotify/conf
[ -f "$CONF.orig" ] || cp "$CONF" "$CONF.orig"
sed -i '/# --- PiRadio start/,/# --- PiRadio end/d' "$CONF"
cat >> "$CONF" <<EOF
# --- PiRadio start
LIBRESPOT_NAME="$NAME"
LIBRESPOT_BITRATE="320"
LIBRESPOT_DEVICE="default"
LIBRESPOT_ONEVENT="/usr/local/bin/piradio-spotify-event"
# --- PiRadio end
EOF
systemctl enable raspotify
systemctl restart raspotify

echo "== 5/6  Installing the preset page"
id piradio >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin piradio
mkdir -p "$APP_DIR/templates" "$APP_DIR/static"
cp "$HERE/app.py" "$APP_DIR/"
cp "$HERE/templates/index.html" "$APP_DIR/templates/"
cp "$HERE"/static/* "$APP_DIR/static/"
# Keep presets you have already edited from the phone.
if [ ! -f "$APP_DIR/presets.json" ]; then
  if [ -f "$HERE/presets.local.json" ]; then
    cp "$HERE/presets.local.json" "$APP_DIR/presets.json"   # your own stations
  else
    cp "$HERE/presets.json" "$APP_DIR/presets.json"         # the examples
  fi
fi
chown -R piradio:piradio "$APP_DIR"
printf 'PIRADIO_NAME="%s"\n' "$NAME" > /etc/default/piradio
echo 'piradio ALL=(root) NOPASSWD: /usr/bin/systemctl restart raspotify, /usr/bin/systemctl poweroff' > /etc/sudoers.d/piradio
chmod 440 /etc/sudoers.d/piradio
visudo -cf /etc/sudoers.d/piradio >/dev/null
cp "$HERE/config/piradio.service" /etc/systemd/system/piradio.service
systemctl daemon-reload
systemctl enable piradio
systemctl restart piradio

if [ "$FAST_BOOT" = 1 ]; then
  echo "== Shortening start-up (--fast-boot)"
  BOOT=/boot/firmware
  # 1. cloud-init only applies the Raspberry Pi Imager settings on the very
  #    first start, then costs a couple of seconds on every later one.
  if [ -f "$BOOT/cmdline.txt" ] && [ -d /etc/cloud ]; then
    if grep -q 'cloud-init=disabled' "$BOOT/cmdline.txt"; then
      echo "   cloud-init: already off"
    else
      cp "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.piradio-bak"
      sed -i '1 s/[[:space:]]*$/ cloud-init=disabled/' "$BOOT/cmdline.txt"
      echo "   cloud-init: switched off (the old file is kept as cmdline.txt.piradio-bak)"
    fi
  else
    echo "   cloud-init: not present, nothing to do"
  fi
  # 2. NetworkManager waits 6 seconds for a cable in the wired socket before it
  #    calls the network ready, even when Wi-Fi is already connected.
  if [ -d /etc/NetworkManager/conf.d ]; then
    printf '%s\n' '# PiRadio --fast-boot: wait 3 seconds, not 6, for a network cable at start-up.' \
      '[device-piradio-eth0]' 'match-device=interface-name:eth0' 'carrier-wait-timeout=3000' \
      > /etc/NetworkManager/conf.d/10-piradio-eth0.conf
    echo "   Wired-socket wait: 3 seconds"
  fi
  # 3. No screen or camera is attached, so skip looking for them.
  if [ -f "$BOOT/config.txt" ]; then
    [ -f "$BOOT/config.txt.piradio-bak" ] || cp "$BOOT/config.txt" "$BOOT/config.txt.piradio-bak"
    for key in camera_auto_detect display_auto_detect; do
      if grep -q "^$key=" "$BOOT/config.txt"; then
        sed -i "s/^$key=.*/$key=0/" "$BOOT/config.txt"
      else
        printf '%s=0\n' "$key" >> "$BOOT/config.txt"
      fi
    done
    echo "   Camera and display detection: off (the old file is kept as config.txt.piradio-bak)"
  fi
  echo "   These take effect at the next restart."
fi

echo "== 6/6  Checking"
sleep 2
for svc in mpd raspotify piradio; do
  printf '   %-10s %s\n' "$svc" "$(systemctl is-active "$svc" || true)"
done

if [ -n "$NEW_HOST" ] && [ "$NEW_HOST" != "$(hostname)" ]; then
  OLD_HOST="$(hostname)"
  echo "   Renaming this Pi on the network: $OLD_HOST -> $NEW_HOST"
  hostnamectl set-hostname "$NEW_HOST"
  sed -i "s/\\b$OLD_HOST\\b/$NEW_HOST/g" /etc/hosts
  systemctl restart avahi-daemon 2>/dev/null || true
fi

echo
echo "Done. \"$NAME\" is ready. On your phone (same Wi-Fi) open:  http://$(hostname).local"
echo "If that name doesn't load, use:                              http://$(hostname -I | awk '{print $1}')"
