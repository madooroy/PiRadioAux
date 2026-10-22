"""The network check (config/netcheck.sh), run as a dry run:  python3 tests/test_netcheck.py

Nothing on the network or the system changes: the check runs against a
stand-in folder for / and prints the commands it would run. Needs bash.
"""
import os, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "config", "netcheck.sh")
T = tempfile.mkdtemp()
ROOT, LIB, SYS = (os.path.join(T, x) for x in ("root", "lib", "sys"))
WIFI = os.path.join(ROOT, "etc/NetworkManager/system-connections/HomeWiFi.nmconnection")
os.makedirs(os.path.dirname(WIFI)); os.makedirs(os.path.join(ROOT, "etc/netplan"))
os.makedirs(os.path.join(SYS, "module/brcmfmac/holders/brcmfmac_cyw"))
clock = [1_800_000_000]


def run(ping, wifi="yes", uptime=3600):
    env = dict(os.environ, PIRADIO_NETCHECK_STATE=os.path.join(T, "run"), PIRADIO_NETCHECK_LIB=LIB,
               PIRADIO_NETCHECK_ROOT=ROOT, PIRADIO_NETCHECK_SYS=SYS, PIRADIO_NETCHECK_RUN="echo",
               PIRADIO_NETCHECK_PAUSE="0", PIRADIO_NETCHECK_PING=ping, PIRADIO_NETCHECK_HAS_WIFI=wifi,
               PIRADIO_NOW=str(clock[0]), PIRADIO_UPTIME=str(uptime))
    clock[0] += 60
    out = subprocess.run(["bash", SCRIPT], env=env, capture_output=True, text=True, check=True)
    return out.stdout.split("\n")[:-1]


def log():
    path = os.path.join(LIB, "actions.log")
    return open(path).read() if os.path.exists(path) else ""


# just started: does nothing
assert run("fail", uptime=60) == [] and log() == ""
# an outage: network restart at 3, driver reload at 5 (the chip-family module first), Pi restart at 8
steps = {m: run("fail") for m in range(1, 11)}
assert steps[3] == ["nmcli networking off", "nmcli networking on"], steps[3]
assert steps[5] == ["modprobe -r brcmfmac_cyw", "modprobe -r brcmfmac", "modprobe brcmfmac"], steps[5]
assert steps[8] == ["systemctl reboot"], steps[8]
assert all(not steps[m] for m in (1, 2, 4, 6, 7, 9, 10))
run("ok"); assert "connection back after 10 minute(s)" in log()
# a second outage within 6 hours: no second Pi restart
assert [run("fail") for _ in range(8)][7] == [] and "not restarting the Pi again so soon" in log()
run("ok")
print("outage steps passed")

# the Wi-Fi settings are kept while all is well, and only rewritten when they change
open(WIFI, "w").write("[wifi]\nssid=HomeWiFi\n")
open(os.path.join(ROOT, "etc/netplan/90-NM-empty.yaml"), "w").close()   # an empty file is not kept
run("ok")
kept = os.path.join(LIB, "wifi-backup/etc/NetworkManager/system-connections/HomeWiFi.nmconnection")
assert open(kept).read() == "[wifi]\nssid=HomeWiFi\n"
assert not os.path.exists(os.path.join(LIB, "wifi-backup/etc/netplan/90-NM-empty.yaml"))
assert oct(os.stat(kept).st_mode & 0o777) == "0o600"
before = os.stat(kept).st_mtime_ns; run("ok"); assert os.stat(kept).st_mtime_ns == before
print("keeping a copy passed")

# the settings go empty (as after the Oct 22 shutdown): put back at the first failed minute, once
open(WIFI, "w").close()
first = run("fail", wifi="no")
assert open(WIFI).read() == "[wifi]\nssid=HomeWiFi\n"
assert first == ["nmcli connection reload", "nmcli device connect wlan0"], first
assert "the Wi-Fi settings were missing: put back the saved copy" in log()
assert run("fail", wifi="no") == []           # not tried again in the same outage
run("ok")
# missing, and the copy has nothing newer to put back
os.remove(WIFI); open(kept, "w").close()
assert run("fail", wifi="no") == [] and "the saved copy has nothing to put back" in log()
run("ok")
print("putting the settings back passed")
