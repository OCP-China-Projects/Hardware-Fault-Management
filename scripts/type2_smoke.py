#!/usr/bin/env python3
"""
Type 2 (Platform Monitoring) auto-discovery smoke test, SSH-driven.

  OpenBMC QEMU (ast2600-evb)  --- unix socket ---  Zephyr QEMU (sifive_u)
     pldmd + mctpd, EID 8                            PLDM Type 0+2 resp, EID 18
     platform-mc discovers                           MCTP control responder
     + polls the temp sensor                         + Numeric Sensor PDR

Unlike two_qemu_smoke.py (the Type 0 harness), this does NOT tear down the
stock mctp-local.service. That service now bounds-retries SetupEndpoint; mctpd
enumerates the Zephyr endpoint (Get Endpoint ID over physical addressing),
assigns EID 18, adds the kernel route + neighbour itself, queries Get Message
Type Support (PLDM = type 1), and publishes it on D-Bus. pldmd's platform-mc
MctpDiscovery then discovers the terminus and polls GetSensorReading for the
temperature sensor (sensor_id = 1). We just observe that with busctl/pldmtool.

Points at the FRESH build artifacts directly (not the git-tracked prebuilts):
  Zephyr:  build-type2/zephyr/zephyr.elf (Type 2 responder + control responder)
  OpenBMC: the just-rebuilt deploy image (mctp-local.service w/o manual route)
We copy the OpenBMC image to a scratch path because QEMU mtd writes back.
"""
import os
import shutil
import subprocess
import time

HOME = os.path.expanduser("~")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QEMU_ARM = os.environ.get("QEMU_ARM", f"{HOME}/qemu-build/bin/qemu-system-arm")
QEMU_RV = os.environ.get("QEMU_RV", f"{HOME}/qemu-build/bin/qemu-system-riscv64")
SOCK = "/tmp/hfm-mctp-t2.sock"

# Type 2 needs the freshly built artifacts (the Type 2 responder + the OpenBMC
# image whose mctp-local.service does auto-discovery). Point at them via the
# environment; sensible defaults fall back to the repo prebuilts so the script
# runs on any machine without editing hardcoded /home/<user> paths.
#   ZEPHYR_ELF  - Zephyr build with the Type 2 responder (build-type2/.../zephyr.elf)
#   OBMC_DEPLOY - OpenBMC deploy image with auto-discovery mctp-local.service
ZEPHYR_ELF = os.environ.get(
    "ZEPHYR_ELF", os.path.join(REPO_ROOT, "prebuilts", "zephyr.elf"))
OBMC_DEPLOY = os.environ.get(
    "OBMC_DEPLOY",
    os.path.join(REPO_ROOT, "prebuilts",
                 "obmc-phosphor-image-evb-ast2600.mtd"))
OBMC_IMG = "/tmp/obmc-type2.mtd"  # writable scratch copy

ZEPHYR_LOG = "/tmp/zephyr-t2.log"
OBMC_LOG = "/tmp/obmc-t2.log"
SSH_PORT = "3222"
PASSWORD = "0penBmc"

ENV = dict(os.environ)
ENV["LD_LIBRARY_PATH"] = f"{HOME}/local/lib/x86_64-linux-gnu:{HOME}/local/lib"

for p in (SOCK,):
    try:
        os.unlink(p)
    except FileNotFoundError:
        pass

SSH_OPTS = [
    "-p", SSH_PORT,
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "GlobalKnownHostsFile=/dev/null",
    "-o", "PubkeyAuthentication=no",
    "-o", "PreferredAuthentications=password,keyboard-interactive",
    "-o", "ConnectTimeout=10",
    "-o", "HostKeyAlgorithms=+ssh-rsa,ssh-dss",
    "-o", "KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1",
]

bmc = None
zephyr = None
zlog = None
blog = None


def cleanup():
    for proc in (zephyr, bmc):
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
    for f in (zlog, blog):
        if f is not None:
            try:
                f.close()
            except Exception:
                pass


def ssh_run(cmd, timeout=60):
    p = subprocess.Popen(
        ["sshpass", "-p", PASSWORD, "ssh"] + SSH_OPTS +
        ["root@127.0.0.1", cmd],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    return p.returncode, out.decode(errors="replace")


def run(cmd, timeout=60):
    print(f"\n===== BMC# {cmd[:70]} =====", flush=True)
    rc, out = ssh_run(cmd, timeout=timeout)
    print(out, flush=True)
    print(f"----- rc={rc} -----", flush=True)
    return rc, out


try:
    print(f"[*] resolving OpenBMC deploy image: "
          f"{os.path.realpath(OBMC_DEPLOY)}", flush=True)
    # If only the compressed .mtd.gz is present (fresh clone), decompress it
    # into the writable scratch copy; otherwise copy the raw image as-is.
    if not os.path.exists(OBMC_DEPLOY) and os.path.exists(OBMC_DEPLOY + ".gz"):
        import gzip
        print(f"[*] decompressing {OBMC_DEPLOY}.gz -> {OBMC_IMG}", flush=True)
        with gzip.open(OBMC_DEPLOY + ".gz", "rb") as src, open(OBMC_IMG, "wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        shutil.copyfile(OBMC_DEPLOY, OBMC_IMG)
    print(f"[*] scratch image: {OBMC_IMG} "
          f"({os.path.getsize(OBMC_IMG)} bytes)", flush=True)

    print("[*] launching OpenBMC QEMU (console -> file, MCTP socket server)...",
          flush=True)
    blog = open(OBMC_LOG, "w")
    bmc = subprocess.Popen(
        [QEMU_ARM, "-machine", "ast2600-evb", "-smp", "2", "-m", "512",
         "-display", "none",
         "-drive", f"file={OBMC_IMG},format=raw,if=mtd,id=hd0",
         "-net", "nic,model=ftgmac100,netdev=netdev1",
         "-netdev", f"user,id=netdev1,hostfwd=::{SSH_PORT}-:22",
         "-chardev", f"socket,id=mctp0,path={SOCK},server=on,wait=off",
         "-serial", "file:" + OBMC_LOG, "-serial", "chardev:mctp0",
         "-monitor", "none"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV,
    )
    print(f"[*] OpenBMC pid={bmc.pid}", flush=True)

    for _ in range(100):
        if os.path.exists(SOCK):
            break
        time.sleep(0.1)
    print(f"[*] socket present: {os.path.exists(SOCK)}", flush=True)

    print("[*] launching Zephyr QEMU client...", flush=True)
    zlog = open(ZEPHYR_LOG, "w")
    zephyr = subprocess.Popen(
        [QEMU_RV, "-machine", "sifive_u", "-smp", "2", "-m", "256",
         "-display", "none", "-bios", "none", "-kernel", ZEPHYR_ELF,
         "-serial", "stdio", "-serial", f"unix:{SOCK}", "-monitor", "none"],
        stdin=subprocess.DEVNULL, stdout=zlog, stderr=subprocess.STDOUT,
        env=ENV,
    )
    print(f"[*] Zephyr pid={zephyr.pid}", flush=True)

    print("[*] waiting for BMC login banner...", flush=True)
    deadline = time.time() + 300
    seen = False
    while time.time() < deadline:
        try:
            with open(OBMC_LOG, "r", errors="replace") as f:
                if "login:" in f.read():
                    seen = True
                    break
        except FileNotFoundError:
            pass
        time.sleep(2)
    print(f"[*] login banner seen: {seen}", flush=True)

    # mctp-local.service bounds-retries SetupEndpoint every 3s (up to 40x).
    # pldmd polls every ~250ms once discovered. Give it time to converge.
    print("[*] letting mctpd enumerate + pldmd discover (90s dwell)...",
          flush=True)
    time.sleep(90)

    # NOTE: this image's `mctp link` / `mctp addr` CLI busy-loops (userspace
    # bug), and BusyBox `head` rejects `head -N`; use journals, sysfs and
    # busctl/tail only.

    # --- Did mctp-local.service run + did SetupEndpoint succeed? ---
    run("echo '### mctp-local.service journal (SetupEndpoint retries)'; "
        "journalctl -u mctp-local.service --no-pager 2>&1 | tail -50")
    run("echo '### mctpd journal (enumeration of the serial peer)'; "
        "journalctl -u mctpd.service --no-pager 2>&1 | tail -50")
    run("echo '### mctpserial0 sysfs (link present + rx/tx counters)'; "
        "cat /sys/class/net/mctpserial0/ifindex 2>&1; "
        "echo -n 'rx_packets='; cat /sys/class/net/mctpserial0/statistics/rx_packets 2>&1; "
        "echo -n 'tx_packets='; cat /sys/class/net/mctpserial0/statistics/tx_packets 2>&1; "
        "echo -n 'operstate='; cat /sys/class/net/mctpserial0/operstate 2>&1")

    # --- Did mctpd publish the Zephyr endpoint (EID 18) on D-Bus? ---
    run("echo '### D-Bus MCTP object tree (mctpd)'; "
        "busctl tree au.com.codeconstruct.MCTP1 2>&1")
    run("echo '### Endpoint objects + SupportedMessageTypes (PLDM = 1)'; "
        "for o in $(busctl tree au.com.codeconstruct.MCTP1 2>/dev/null "
        "| grep -oE '/au/com/codeconstruct/mctp1/networks/[0-9]+/endpoints/[0-9]+'); do "
        "echo \"== $o ==\"; "
        "busctl introspect au.com.codeconstruct.MCTP1 \"$o\" "
        "xyz.openbmc_project.MCTP.Endpoint 2>&1; done")

    # --- Did pldmd platform-mc discover + poll the sensor? ---
    run("echo '### pldmtool GetTID / GetPLDMTypes over EID 18'; "
        "pldmtool base GetTID -m 18 2>&1; "
        "pldmtool base GetPLDMTypes -m 18 2>&1")
    run("echo '### PDR repository info + numeric sensor reading (Type 2)'; "
        "pldmtool platform GetPDRRepositoryInfo -m 18 2>&1; "
        "echo '--- GetSensorReading sensor_id=1 ---'; "
        "pldmtool platform GetSensorReading -i 1 -m 18 2>&1")
    run("echo '### pldmd journal (platform-mc discovery + polling)'; "
        "journalctl -u pldmd.service --no-pager 2>&1 | tail -50")

    print("\n########## ZEPHYR CONSOLE (tail) ##########", flush=True)
    try:
        with open(ZEPHYR_LOG, "r", errors="replace") as f:
            for line in f.read().splitlines()[-30:]:
                print(line, flush=True)
    except FileNotFoundError:
        print("(no zephyr console log)", flush=True)

    print("\n[*] done issuing commands.", flush=True)
finally:
    print("[*] cleaning up QEMU instances.", flush=True)
    cleanup()
    time.sleep(1)
    try:
        os.unlink(OBMC_IMG)
    except FileNotFoundError:
        pass
    print("[*] logs: BMC", OBMC_LOG, "| Zephyr", ZEPHYR_LOG, flush=True)
