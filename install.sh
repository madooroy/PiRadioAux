#!/bin/bash
# PiRadioAux installer. Run on the Pi from inside the project folder:
#     sudo bash install.sh
# Options:
#     --name "Kitchen Radio"   the name shown on the page and in Spotify (default: PiRadio)
#     --hostname kitchenradio  also rename the Pi on the network (page becomes http://kitchenradio.local)
#     --fast-boot              shorten start-up (see the README; the HAT screen is not affected)
#     --undo-fast-boot         put the start-up settings back, and do nothing else
#     --display                use a Pimoroni Display HAT Mini (screen and buttons, see the README)
#     --flip-display           the same, with the picture turned the other way up
#     --no-display             stop using the screen and buttons
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
UNDO_FAST_BOOT=0
DISPLAY_CHOICE=""   # "on", "off" or "" (keep what an earlier run chose)
FLIP_CHOICE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name)     NAME="${2:-}"; shift 2 ;;
    --hostname) NEW_HOST="${2:-}"; shift 2 ;;
    --fast-boot) FAST_BOOT=1; shift ;;
    --undo-fast-boot) UNDO_FAST_BOOT=1; shift ;;
    --display) DISPLAY_CHOICE=on; FLIP_CHOICE="${FLIP_CHOICE:-0}"; shift ;;
    --flip-display) DISPLAY_CHOICE=on; FLIP_CHOICE=1; shift ;;
    --no-display) DISPLAY_CHOICE=off; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
# Keep the name chosen on an earlier run unless a new one is given.
if [ -z "$NAME" ] && [ -f /etc/default/piradio ]; then
  NAME="$(sed -n 's/^PIRADIO_NAME="\(.*\)"$/\1/p' /etc/default/piradio)"
fi
NAME="${NAME:-PiRadio}"
# The same for the screen-and-buttons choice.
saved_setting() { sed -n "s/^$1=\"\(.*\)\"\$/\1/p" /etc/default/piradio 2>/dev/null; }
if [ -z "$DISPLAY_CHOICE" ]; then
  [ "$(saved_setting PIRADIO_DISPLAY)" = 1 ] && DISPLAY_CHOICE=on || DISPLAY_CHOICE=off
fi
FLIP_CHOICE="${FLIP_CHOICE:-$(saved_setting PIRADIO_DISPLAY_FLIP)}"
FLIP_CHOICE="${FLIP_CHOICE:-0}"
if ! printf '%s' "$NAME" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$'; then
  echo "The name can use letters, numbers, spaces, dots, dashes and underscores (up to 32)." >&2
  exit 1
fi
if [ -n "$NEW_HOST" ] && ! printf '%s' "$NEW_HOST" | grep -Eq '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'; then
  echo "The hostname can use lowercase letters, numbers and dashes." >&2
  exit 1
fi

# ---- Start-up settings (--fast-boot / --undo-fast-boot) ----------------------
# PIRADIO_BOOT_DIR and PIRADIO_NM_DIR let these be tried on copies of the files.
BOOT="${PIRADIO_BOOT_DIR:-/boot/firmware}"
NM_CONF="${PIRADIO_NM_DIR:-/etc/NetworkManager/conf.d}/10-piradio-eth0.conf"
INITRAMFS_CONF="${PIRADIO_INITRAMFS_DIR:-/etc/initramfs-tools/conf.d}/piradio-fast-boot"

# Started without an initramfs (auto_initramfs=0), Linux names the system drive
# /dev/root, and update-initramfs can then no longer tell which drive it is: it
# fails, and so does the nightly update that triggered it. MODULES=most builds
# the file without needing to know. The file is bigger, but it isn't loaded.
initramfs_guard() {
  [ -f "$BOOT/config.txt" ] && grep -q '^auto_initramfs=0' "$BOOT/config.txt" || return 0
  [ -d "$(dirname "$INITRAMFS_CONF")" ] || return 0
  if [ ! -f "$INITRAMFS_CONF" ]; then
    printf '%s\n' '# PiRadio --fast-boot: the Pi starts without the initramfs, so build it' \
      '# without needing to know the system drive (see install.sh).' 'MODULES=most' > "$INITRAMFS_CONF"
    echo "   Initramfs: set to build without looking up the system drive"
  fi
}

# The Pi 4's bootloader pauses at power-on in case a keyboard asks for a network
# install. The setting lives on the board itself, not on the card.
set_net_install() {
  local value="$1" current tmp
  [ -z "${PIRADIO_BOOT_DIR:-}" ] || return 0
  if ! grep -q "Raspberry Pi 4" /proc/device-tree/model 2>/dev/null || ! command -v rpi-eeprom-config >/dev/null; then
    echo "   Bootloader: skipped (not a Raspberry Pi 4, or rpi-eeprom-config is missing)"
    return 0
  fi
  current="$(rpi-eeprom-config 2>/dev/null)" || { echo "   Bootloader: could not be read, left alone"; return 0; }
  if printf '%s\n' "$current" | grep -q "^NET_INSTALL_AT_POWER_ON=$value\$"; then
    echo "   Bootloader: NET_INSTALL_AT_POWER_ON=$value already set"
    return 0
  fi
  tmp="$(mktemp)"
  printf '%s\n' "$current" | grep -v '^NET_INSTALL_AT_POWER_ON=' > "$tmp" || true
  echo "NET_INSTALL_AT_POWER_ON=$value" >> "$tmp"
  if rpi-eeprom-config --apply "$tmp" >/dev/null 2>&1; then
    echo "   Bootloader: NET_INSTALL_AT_POWER_ON=$value (written at the next restart)"
  else
    echo "   Bootloader: could not be changed, left alone"
  fi
  rm -f "$tmp"
}

# The Display HAT Mini's screen is driven over SPI, which is off on a new card.
# Its data/command line is GPIO 9, which is also SPI's MISO (data-in) line; the
# screen never sends data back, so SPI is switched on without MISO (no_miso),
# leaving GPIO 9 free. With plain dtparam=spi=on, current kernels refuse to hand
# GPIO 9 over ("Invalid argument") and the screen stays blank.
SPI_RESTART=0
SPI_LINE="dtoverlay=spi0-2cs,no_miso"
ensure_spi() {
  [ -f "$BOOT/config.txt" ] || return 0
  if grep -qx "$SPI_LINE" "$BOOT/config.txt"; then
    echo "   SPI (for the screen): already on"
  elif grep -q '^dtparam=spi=on' "$BOOT/config.txt"; then
    sed -i "s/^dtparam=spi=on.*/$SPI_LINE/" "$BOOT/config.txt"
    echo "   SPI (for the screen): switched to leave GPIO 9 free (takes effect after a restart)"
    SPI_RESTART=1
  else
    { echo; echo "# Screen on the Display HAT Mini (PiRadio install.sh --display)"; echo "[all]"; echo "$SPI_LINE"; } >> "$BOOT/config.txt"
    echo "   SPI (for the screen): switched on"
    SPI_RESTART=1
  fi
  [ -e /dev/spidev0.1 ] || SPI_RESTART=1
}

fast_boot_apply() {
  echo "== Shortening start-up (--fast-boot)"
  # 1. cloud-init only applies the Raspberry Pi Imager settings on the very
  #    first start, then costs a couple of seconds on every later one.
  if [ -f "$BOOT/cmdline.txt" ]; then
    if grep -qw 'cloud-init=disabled' "$BOOT/cmdline.txt"; then
      echo "   cloud-init: already off"
    else
      [ -f "$BOOT/cmdline.txt.piradio-bak" ] || cp "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.piradio-bak"
      sed -i '1 s/[[:space:]]*$/ cloud-init=disabled/' "$BOOT/cmdline.txt"
      echo "   cloud-init: switched off"
    fi
  fi
  # 2. NetworkManager waits 6 seconds for a cable in the wired socket before it
  #    calls the network ready, even when Wi-Fi is already connected.
  if [ -d "$(dirname "$NM_CONF")" ]; then
    printf '%s\n' '# PiRadio --fast-boot: wait 3 seconds, not 6, for a network cable at start-up.' \
      '[device-piradio-eth0]' 'match-device=interface-name:eth0' 'carrier-wait-timeout=3000' > "$NM_CONF"
    echo "   Wired-socket wait: 3 seconds"
  fi
  # 3. Firmware settings: no pause or splash screen, no looking for a camera or
  #    display, and no initramfs when starting from an SD card (not needed there).
  if [ -f "$BOOT/config.txt" ]; then
    [ -f "$BOOT/config.txt.piradio-bak" ] || cp "$BOOT/config.txt" "$BOOT/config.txt.piradio-bak"
    local settings="camera_auto_detect=0 display_auto_detect=0 boot_delay=0 disable_splash=1" setting key missing=""
    case "${PIRADIO_ROOT_DEV:-$(findmnt -n -o SOURCE / 2>/dev/null)}" in
      /dev/mmcblk*) settings="$settings auto_initramfs=0" ;;
      *) echo "   Not starting from an SD card: the initramfs setting is left alone" ;;
    esac
    for setting in $settings; do
      key="${setting%%=*}"
      if grep -q "^$key=" "$BOOT/config.txt"; then
        sed -i "s/^$key=.*/$setting/" "$BOOT/config.txt"
      else
        missing="$missing $setting"
      fi
    done
    if [ -n "$missing" ]; then
      # Under [all], so they apply whichever Pi model section the file ends in.
      { echo; echo "# Faster start-up (PiRadio install.sh --fast-boot)"; echo "[all]"; printf '%s\n' $missing; } >> "$BOOT/config.txt"
    fi
    echo "   config.txt: $settings"
  fi
  initramfs_guard
  # 4. Bootloader (Raspberry Pi 4).
  set_net_install 0
  echo "   The original files are kept beside the changed ones as *.piradio-bak."
  echo "   These take effect at the next restart:  sudo reboot"
}

fast_boot_undo() {
  echo "== Putting the start-up settings back (--undo-fast-boot)"
  if [ -f "$BOOT/config.txt.piradio-bak" ]; then
    cp "$BOOT/config.txt.piradio-bak" "$BOOT/config.txt"
    echo "   config.txt: restored"
  else
    echo "   config.txt: no saved copy, left as it is"
  fi
  if [ -f "$BOOT/cmdline.txt" ] && grep -qw 'cloud-init=disabled' "$BOOT/cmdline.txt"; then
    sed -i 's/[[:space:]]*cloud-init=disabled//g' "$BOOT/cmdline.txt"
    echo "   cloud-init: switched back on"
  else
    echo "   cloud-init: was not switched off"
  fi
  if [ -f "$NM_CONF" ]; then
    rm -f "$NM_CONF"
    echo "   Wired-socket wait: back to the default"
  fi
  if [ -f "$INITRAMFS_CONF" ]; then
    rm -f "$INITRAMFS_CONF"
    echo "   Initramfs: back to the default"
  fi
  set_net_install 1
  # The restored config.txt predates the screen; keep the screen working.
  [ "$(saved_setting PIRADIO_DISPLAY)" = 1 ] && ensure_spi
  echo "Done. Restart to apply:  sudo reboot"
}

if [ "$UNDO_FAST_BOOT" = 1 ]; then
  fast_boot_undo
  exit 0
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
apt-get install -y mpd mpc alsa-utils curl iputils-ping iw python3-flask python3-mpd python3-waitress

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
# A new install starts the radio at full volume, which is far too loud through a
# good audio adapter. Start at 50 the first time; after that the level is yours.
if [ ! -f "$APP_DIR/presets.json" ]; then
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if mpc -q --host 127.0.0.1 volume 50 >/dev/null 2>&1; then
      echo "   Radio volume set to 50 for the first start"
      break
    fi
    sleep 1
  done
fi

echo "== 4/6  Setting up Spotify Connect (Raspotify)"
if ! dpkg -s raspotify >/dev/null 2>&1; then
  curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
fi
install -m 755 "$HERE/config/spotify-event.sh" /usr/local/bin/piradio-spotify-event
CONF=/etc/raspotify/conf
[ -f "$CONF.orig" ] || cp "$CONF" "$CONF.orig"
sed -i '/# --- PiRadio start/,/# --- PiRadio end/d' "$CONF"
# Earlier hand-made copies of the two volume settings would sit above the block.
sed -i -E '/^LIBRESPOT_(INITIAL_VOLUME|VOLUME_RANGE)=/d' "$CONF"
cat >> "$CONF" <<EOF
# --- PiRadio start
LIBRESPOT_NAME="$NAME"
LIBRESPOT_BITRATE="320"
LIBRESPOT_DEVICE="default"
LIBRESPOT_ONEVENT="/usr/local/bin/piradio-spotify-event"
# Volume: Spotify starts every session at 50, and its slider follows a curve
# chosen to sit close to the radio's slider (Spotify's default is much quieter
# through the middle, so switching from radio to Spotify sounded like a drop).
LIBRESPOT_INITIAL_VOLUME="50"
LIBRESPOT_VOLUME_RANGE="30"
# --- PiRadio end
EOF
systemctl enable raspotify
systemctl restart raspotify

echo "== 5/6  Installing the preset page"
id piradio >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin piradio
mkdir -p "$APP_DIR/templates" "$APP_DIR/static"
cp "$HERE/app.py" "$HERE/display.py" "$APP_DIR/"
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
DISPLAY_ON=0; [ "$DISPLAY_CHOICE" = on ] && DISPLAY_ON=1
printf 'PIRADIO_NAME="%s"\nPIRADIO_DISPLAY="%s"\nPIRADIO_DISPLAY_FLIP="%s"\n' "$NAME" "$DISPLAY_ON" "$FLIP_CHOICE" > /etc/default/piradio
echo 'piradio ALL=(root) NOPASSWD: /usr/bin/systemctl restart raspotify, /usr/bin/systemctl poweroff' > /etc/sudoers.d/piradio
chmod 440 /etc/sudoers.d/piradio
visudo -cf /etc/sudoers.d/piradio >/dev/null
cp "$HERE/config/piradio.service" /etc/systemd/system/piradio.service
systemctl daemon-reload
systemctl enable piradio
systemctl restart piradio

echo "== Keeping it on: Wi-Fi power saving, network check, watchdog"
# The Pi 4's Wi-Fi can hang with power saving on, leaving the Pi running but
# unreachable and silent. Power saving off, at once and for every connection.
if [ -d /etc/NetworkManager/conf.d ]; then
  printf '%s\n' '# PiRadio: Wi-Fi power saving off (it can make the Pi 4 Wi-Fi hang).' \
    '[connection]' 'wifi.powersave = 2' > /etc/NetworkManager/conf.d/20-piradio-wifi-powersave.conf
  iw dev wlan0 set power_save off 2>/dev/null || true
  echo "   Wi-Fi power saving: off"
fi
# If the router stops answering anyway: restart the network, then the Wi-Fi
# driver, then the Pi (see config/netcheck.sh).
install -m 755 "$HERE/config/netcheck.sh" /usr/local/bin/piradio-netcheck
cp "$HERE/config/piradio-netcheck.service" "$HERE/config/piradio-netcheck.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now piradio-netcheck.timer >/dev/null 2>&1
echo "   Network check: every minute"
# The hardware watchdog restarts the Pi if it freezes completely.
if [ -e /dev/watchdog ]; then
  mkdir -p /etc/systemd/system.conf.d
  printf '%s\n' '# PiRadio: restart the Pi if it freezes.' '[Manager]' 'RuntimeWatchdogSec=15s' 'RebootWatchdogSec=2min' \
    > /etc/systemd/system.conf.d/50-piradio-watchdog.conf
  systemctl daemon-reexec
  echo "   Hardware watchdog: on (15 seconds)"
fi

# Keep the system log across restarts (capped at 50 MB), so the reason for an
# unexpected stop can be looked up afterwards.
mkdir -p /etc/systemd/journald.conf.d
printf '%s\n' '# PiRadio: keep the log across restarts.' '[Journal]' 'Storage=persistent' 'SystemMaxUse=50M' \
  > /etc/systemd/journald.conf.d/50-piradio-persistent.conf
systemctl restart systemd-journald
journalctl --flush 2>/dev/null || true
echo "   System log: kept across restarts (up to 50 MB)"

# Screen and buttons (Pimoroni Display HAT Mini), only when asked for.
cp "$HERE/config/piradio-display.service" /etc/systemd/system/piradio-display.service
systemctl daemon-reload
SHUTDOWN_HOOK=/usr/lib/systemd/system-shutdown/piradio-display.shutdown
if [ "$DISPLAY_ON" = 1 ]; then
  echo "== Screen and buttons (Display HAT Mini)"
  apt-get install -y python3-pil python3-numpy python3-spidev python3-gpiozero python3-lgpio python3-libgpiod fonts-dejavu-core
  ensure_spi
  # Draws "Safe to unplug" at the very end of a shutdown.
  install -D -m 755 "$HERE/config/piradio-display.shutdown" "$SHUTDOWN_HOOK"
  echo "   Shutdown screen: \"Safe to unplug\" when the Pi has stopped"
  systemctl enable piradio-display
  if [ "$SPI_RESTART" = 1 ]; then
    echo "   The screen starts after a restart:  sudo reboot"
  else
    systemctl restart piradio-display
  fi
  [ "$FLIP_CHOICE" = 1 ] && echo "   Picture turned the other way up (undo: run install.sh --display)"
else
  rm -f "$SHUTDOWN_HOOK"
  if systemctl is-enabled --quiet piradio-display 2>/dev/null; then
    echo "== Screen and buttons: switched off"
    systemctl disable --now piradio-display
  fi
fi

if [ "$FAST_BOOT" = 1 ]; then
  fast_boot_apply
else
  initramfs_guard   # a Pi set up with an earlier --fast-boot gets this on its next update
fi

echo "== 6/6  Checking"
sleep 2
SERVICES="mpd raspotify piradio"
[ "$DISPLAY_ON" = 1 ] && [ "$SPI_RESTART" = 0 ] && SERVICES="$SERVICES piradio-display"
for svc in $SERVICES; do
  printf '   %-16s %s\n' "$svc" "$(systemctl is-active "$svc" || true)"
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
echo "To check the whole setup at any time:        sudo bash $HERE/tools/health_check.sh"
