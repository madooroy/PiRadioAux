# PiRadioAux

A Raspberry Pi that turns any radio, speaker, or amplifier with an aux input into an internet radio with presets and Spotify Connect. You control it from a web page on your phone: up to 20 preset buttons, Stop Playing, and volume.

- **Presets** for internet radio stations, with a station search and a check that an address really works.
- **Spotify Connect**: the Pi shows up as a speaker in the Spotify app (needs Spotify Premium).
- **Artist and song** shown when the station provides them.
- **Keeps itself running**: restarts a station whose stream drops, installs updates overnight, and starts by itself after a power cut.

**Why the aux input.** The Pi connects to the radio by cable for three reasons:

- **Sound.** A cable carries the audio as it is. Bluetooth compresses it a second time on the way to the speaker, on top of the compression the station or Spotify already applied, and that second pass is the one you can sometimes hear.
- **Reliability.** A cable has no pairing, no dropouts, and no reconnecting after a power cut.
- **Simplicity.** The radio stays on its aux source permanently, and everything else is chosen from the phone.

It works with any radio, speaker, or amplifier that has an aux or line input. How good it sounds depends on the audio adapter between the Pi and the cable; an inexpensive USB adapter is already well ahead of the Pi's built-in headphone jack.

**What has and hasn't been tested:** everything here has been run on one Raspberry Pi 4 using its built-in headphone jack: the install, radio presets, Spotify Connect and the handover with radio, the song display, automatic reconnection, station search and the address check, the nightly update check, `--fast-boot`, and the Power off button. Not yet tested: a USB audio adapter and its shared audio mode, any other Pi model, an update that actually installs something, and the automatic 3 AM restart. The troubleshooting section covers the places most likely to need a tweak.

## What you need

- A Raspberry Pi 3, 4, or 5 with its power supply, a case, and a microSD card (a Pi 4 kit has all of these)
- A USB audio adapter with a 3.5mm headphone socket (about $10)
- A 3.5mm to 3.5mm cable, or 3.5mm to RCA if your radio's input is RCA
- A computer with a microSD slot or reader
- Spotify Premium, if you want Spotify Connect

### Why a USB audio adapter

The headphone jack on a Pi 3 or 4 works, and the installer uses it when no USB adapter is plugged in. It is fine for trying things out. A USB adapter is recommended for regular use because:

- The built-in jack is a simple circuit that shares power with the rest of the board, so it has more background hiss, which is easier to hear through a radio or amplifier turned up in a quiet room.
- The Pi 5, Pi 400, and Zero 2 W have no headphone jack at all.

### Which Raspberry Pi

Only the Pi 4 has been tested. The rest of this table is what should happen, not what has been confirmed.

| Model | Expected | Notes |
|---|---|---|
| Pi 4 | Works (tested) | Built-in jack or USB adapter |
| Pi 3 | Should work | Built-in jack or USB adapter |
| Pi 5, Pi 400 | Should work | No headphone jack, so a USB adapter is required |
| Zero 2 W | Should work | No headphone jack; needs a USB adapter and a micro-USB adapter cable. Slower, and Wi-Fi only |
| Pi 2 | Probably | Needs the 32-bit version of Raspberry Pi OS Lite, and has no built-in Wi-Fi |
| Pi 1, original Zero | No | Spotify Connect (Raspotify) does not support these processors |

On a Pi with no headphone jack and no USB adapter, the installer stops with "No audio output found".

## 1. Prepare the card

1. Install **Raspberry Pi Imager** on your computer (raspberrypi.com/software).
2. Choose your Pi model, then OS **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**.
3. When asked to customise settings, set:
   - Hostname: `piradio`
   - A username and password (the examples below use `pi`)
   - Your Wi-Fi name, password, and country
   - Under Services, enable **SSH** with password login
4. Write the card.

## 2. Connect everything

1. Put the card in the Pi.
2. Plug the USB audio adapter into any USB port on the Pi.
3. Run the cable from the adapter's headphone socket (green, or marked with a headphone symbol) to the radio's aux input.
4. Power the Pi and wait about two minutes for the first boot.
5. Set the radio to its aux source, with its volume low for now.

## 3. Install

In a terminal on your computer (Terminal on Mac, Command Prompt or PowerShell on Windows), connect to the Pi and bring it up to date:

```
ssh pi@piradio.local
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Wait a minute, connect again, then download the project and run the installer:

```
ssh pi@piradio.local
sudo apt install -y git
git clone https://github.com/madooroy/PiRadioAux.git
cd PiRadioAux
sudo bash install.sh
```

The installer takes a few minutes. It sets up the radio player (MPD), Spotify Connect (Raspotify), the audio output, and the preset page, and makes them all start on boot. It ends by printing the status of the three services and the address to open.

It is safe to run again, and it keeps presets you have edited from the phone.

**A different name.** The page and the Spotify speaker are called "PiRadio". To choose another name, for a second one in the house say, add it to the install command. `--hostname` also renames the Pi on the network:

```
sudo bash install.sh --name "Kitchen Radio" --hostname kitchenradio
```

**Updating later.** On the Pi:

```
cd ~/PiRadioAux
git pull
sudo bash install.sh
```

**Without Git.** Copy the project folder from your computer and install from it:

```
scp -r PiRadioAux pi@piradio.local:~
ssh pi@piradio.local
cd PiRadioAux
sudo bash install.sh
```

On Windows, `update-pi.bat` in the project folder does those steps when you double-click it.

## 4. Use it

1. On your phone (same Wi-Fi) open **http://piradio.local**
2. Tap a preset. Raise the page's volume slider to about 80, then set the radio's own volume to a comfortable loud level. After that, leave it alone and use the phone.
3. Add the page to your home screen:
   - iPhone: Share → **Add to Home Screen**
   - Android: menu → **Add to Home screen**
4. For Spotify, open the Spotify app, tap the devices icon, and choose **PiRadio**.

Starting Spotify stops the radio. Tapping a preset or Stop Playing ends the Spotify session. While Spotify is playing through the Pi, the page shows "Spotify" with the artist and song in the Now-playing box, and no preset is highlighted. Radio volume is the slider on the page; Spotify volume is in the Spotify app.

**If a station drops.** Internet streams stop now and then, because of a hiccup at the station or on your network. The Pi checks every 10 seconds that the station you chose is still playing, and restarts it if it has stopped or frozen. The page shows "Reconnecting…" while it does. If the station is down for a while, the Pi keeps trying, up to once a minute, until it returns or you press Stop Playing. It only does this for a station you started; Stop Playing, Power off, and starting Spotify all end it.

## 5. The presets

The page starts with four example stations. Replace them with your own from **Edit presets**: you can rename, reorder, remove, or add presets, up to 20. **Sort by frequency** puts them in dial order using the number in each name (92.5 before 103.9); stations with no number in the name go last, in alphabetical order. With more than eight presets the list scrolls, while the now-playing box, Stop Playing, volume, and Power off stay in place.

| # | Example station | Stream address |
|---|---|---|
| 1 | SomaFM Groove Salad | `https://ice1.somafm.com/groovesalad-128-mp3` |
| 2 | KEXP 90.3 Seattle | `https://kexp-mp3-128.streamguys1.com/kexp128.mp3` |
| 3 | Radio Paradise | `https://stream.radioparadise.com/aac-320` |
| 4 | WFMU 91.1 | `https://stream0.wfmu.org/freeform-128k` |

Station addresses belong to the stations and change from time to time, so none of them is guaranteed to keep working. This project has no connection with any station or directory it mentions.

**Find a station.** In Edit presets, tap **Find a station**, type part of the name, and tap a result to add it. The search uses [Radio Browser](https://www.radio-browser.info/), a free community-run directory. Big and mid-sized stations are usually there; small local ones often are not, and for those you add the address by hand (see below). If a search finds nothing, it is tried again without the frequency and "FM", so `WXYZ-FM` or `wxyz 92.5` still finds a station listed as "WXYZ".

**Address check.** When you save, the Pi tries each new or changed address and tells you what it found: live audio, a web page that is not a stream, an error from the server, a chunked address, or a station that blocks listeners outside its own country. You can fix the address or tap **Save anyway**.

**Avoid chunked (HLS) addresses.** A station's web player often uses an address ending in `.m3u8`, where the audio arrives as a playlist of ten-second chunks. The Pi's radio player (MPD) plays these but keeps stopping at commercial breaks: in a one-hour test it had to be restarted 11 times. Use a plain continuous stream instead. For stations on AmperWave (addresses containing `live.amperwave.net`), change `manifest/NAME-hlscN.m3u8` to `direct/NAME-ibcN`, keeping the same number, or no number if there was none. The address check offers this change for you when the continuous address answers.

**Artist and song.** The "Now playing" box shows the artist and song when the stream carries them, and `---` when it doesn't. Some stations publish what is playing in a separate now-playing feed instead; a preset can have one in its optional third field. Only AmperWave's feed format is understood, for example `https://api-nowplaying.amperwave.net/api/v1/prtplus/nowplaying/20/STATION-ID/nowplaying.json`, where the station ID is the number in the station's web player address. These feeds belong to the stations and could change without notice, in which case the line shows `---`.

**Keeping your own list.** To have a fresh install start with your stations, save them as `presets.local.json` next to `presets.json`, in the same format. The installer uses it when present, and Git ignores it. The installer never overwrites presets already on the Pi.

**To find the address of another station** (Chrome or Edge on a computer):

1. Open the station's web player and start it playing.
2. Press F12 and click the **Console** tab.
3. Paste this line and press Enter (type `allow pasting` first if Chrome asks):

   ```
   console.log([...document.querySelectorAll('audio,video')].map(a => a.currentSrc).concat(performance.getEntriesByType('resource').map(e => e.name).filter(n => !/\.(png|jpe?g|gif|svg|css|js|woff2?|ico)(\?|$)/i.test(n) && !/google|doubleclick|adsrvr|rubicon|sync\./.test(n))).join('\n'))
   ```

4. Look for an address on a streaming server, and drop everything from the `?` onward (that part is often a temporary pass that expires within a minute). One ending in `.aac` or `.mp3`, or with no ending, is best. If the only one ends in `.m3u8`, see the note above about chunked addresses.

## Leave the Pi on

The Pi is meant to stay powered all the time. It uses about 3 watts idle, is ready the moment you open the page or pick PiRadio in Spotify, and needs to be on in the evening for the automatic updates. Switch the radio off as you like; the Pi doesn't mind.

If you ever need to unplug the Pi (moving it, for example), shut it down first so the memory card isn't damaged:

1. On the preset page, tap **Power off PiRadio** at the bottom (it shows the name you gave the device) and confirm.
2. Wait until the Pi's green light stops flickering and stays off, about 30 seconds.
3. Unplug it.

It starts again by itself when you plug it back in, and is ready in under a minute. The same shutdown from a computer is `ssh pi@piradio.local` then `sudo shutdown -h now`.

After a power cut the Pi also restarts by itself; that is nearly always fine.

## Keeping it updated

Updates are automatic. The installer sets the Pi to check every night around 11 PM and install whatever is available: the system, the radio player, and Spotify Connect. If an update needs a restart, the Pi restarts itself at 3 AM. Your presets are not affected.

What this means in practice:

- If something is playing around 11 PM, it may stop for a moment when the radio player or Spotify Connect is updated. Tap the preset again, or press play in Spotify.
- The Pi must be powered and on Wi-Fi at 11 PM for that night's update to happen. If it misses one, it catches up the next time it can.
- To see what was updated: `cat /var/log/unattended-upgrades/unattended-upgrades.log`
- To update by hand at any time: `sudo apt update && sudo apt full-upgrade -y`
- To turn automatic updates off: `sudo systemctl disable --now apt-daily-upgrade.timer`
- To change the time, edit the two `OnCalendar` times and the `Reboot-Time` near the top of `install.sh` and run it again.

## Faster start-up (optional)

The Pi is meant to stay on, so start-up time only matters after a power cut, an overnight update restart, or when you move it. If you want it shorter, run the installer once with `--fast-boot`:

```
sudo bash install.sh --fast-boot
sudo reboot
```

On the Pi 4 used for testing this brought the time from power-on to a working page down from about 20 seconds to about 14. It makes three changes, all for a Pi with no screen attached:

| Change | Why | To undo |
|---|---|---|
| Switches off cloud-init | It applies the Raspberry Pi Imager settings on the very first start, then costs about 2 seconds on every later one | Delete the word `cloud-init=disabled` from `/boot/firmware/cmdline.txt` |
| Waits 3 seconds, not 6, for a network cable | The Pi otherwise waits the full 6 seconds on an empty wired socket even when Wi-Fi is already connected | `sudo rm /etc/NetworkManager/conf.d/10-piradio-eth0.conf` |
| Stops looking for a camera and a display | Nothing is attached | Set `camera_auto_detect` and `display_auto_detect` back to `1` in `/boot/firmware/config.txt` |

The installer keeps the original files beside the changed ones as `cmdline.txt.piradio-bak` and `config.txt.piradio-bak`. Both are on the card's small boot partition, which a Windows or Mac computer can open, so if the Pi ever fails to come back you can put the card in a computer and restore them.

Run it only after the Pi has started at least once and is on your Wi-Fi, which is always the case by the time you can run the installer.

## Troubleshooting

Run these on the Pi after `ssh pi@piradio.local`.

| Problem | Try |
|---|---|
| Page won't load | Use the Pi's IP address (shown at the end of the installer, or in your router's device list) in place of `piradio.local`. |
| Page says it can't reach the player | `sudo systemctl status mpd` |
| Preset selected but silent | `speaker-test -c 2 -t wav -l 1` plays a voice through the same output. If that is silent too, run `aplay -l` to confirm the adapter is listed, then `sudo bash install.sh` again. |
| One station fails, others work | That stream address is wrong or has moved. `mpc status` shows the error. |
| "…couldn't be played" under the volume slider | The station never produced sound, so PiRadio stopped retrying after about a minute. The message gives the reason when it can find one. A station that "blocks listeners outside its own country" cannot be fixed with a different address. |
| A station keeps showing "Reconnecting…" | Its address is probably the chunked (`.m3u8`) kind; see "Avoid chunked (HLS) addresses" above. `journalctl -u piradio -n 30` lists each restart. |
| PiRadio missing in Spotify | `sudo systemctl status raspotify` and `journalctl -u raspotify -n 30`. Phone and Pi must be on the same Wi-Fi network. |
| Spotify shows playing but silent, or radio keeps playing under Spotify | Look at `journalctl -u raspotify -n 30`. The likely cause is Raspotify's security sandbox blocking the audio output or the hand-over script (`/usr/local/bin/piradio-spotify-event`). |
| Hum through the radio | A ground loop isolator (about $10) on the audio cable. |

## What's in the folder

| File | Purpose |
|---|---|
| `app.py`, `templates/index.html` | The preset page |
| `static/` | Home screen icon |
| `presets.json` | The example presets a fresh install starts with |
| `presets.local.json` | Optional: your own starting presets, used in place of the examples |
| `update-pi.bat` | Windows helper that copies the folder to the Pi and installs |
| `LICENSE` | GNU General Public License, version 3 |
| `install.sh` | Sets everything up on the Pi |
| `config/asound-shared.conf` | Audio output with a software mixer, for the USB adapter |
| `config/asound-direct.conf` | Audio output used directly, for the Pi's built-in jack |
| `config/mpd.conf` | Radio player settings |
| `config/spotify-event.sh` | Hands over to Spotify: stops the radio and tells the preset page |
| `config/piradio.service` | Starts the preset page on boot |

On the Pi the app lives in `/opt/piradio`, and its presets in `/opt/piradio/presets.json`.

## License

Copyright (C) 2026 madooroy

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. It is distributed in the hope that it will be useful, but without any warranty; see the `LICENSE` file for the full text.
