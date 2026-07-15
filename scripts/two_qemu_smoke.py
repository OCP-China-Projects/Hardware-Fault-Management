#!/usr/bin/env python3
"""
Live two-QEMU MCTP-over-serial (DSP0253) smoke test, SSH-driven.

  OpenBMC QEMU (ast2600-evb)  --- unix socket ---  Zephyr QEMU (sifive_u)
     pldmd + mctpd, EID 8                            PLDM Type 0 resp, EID 18

OpenBMC console -> file (we only watch for the login banner). We then SSH into
the BMC (hostfwd 3222->22, root/0penBmc) for clean command output. The BMC's
second UART (UART1 -> /dev/ttyS0) is a socket SERVER; Zephyr's second UART
(SiFive uart1) is the socket CLIENT.
"""
import os
import subprocess
import time

HOME = os.path.expanduser("~")
PREBUILTS = "/home/terry.gong/workspace/Hardware-Fault-Management/prebuilts"
QEMU_ARM = f"{HOME}/qemu-build/bin/qemu-system-arm"
QEMU_RV = f"{HOME}/qemu-build/bin/qemu-system-riscv64"
SOCK = "/tmp/hfm-mctp.sock"
OBMC_IMG = f"{PREBUILTS}/obmc-phosphor-image-evb-ast2600.mtd"
ZEPHYR_ELF = f"{PREBUILTS}/zephyr.elf"
ZEPHYR_LOG = "/tmp/zephyr-console.log"
OBMC_LOG = "/tmp/obmc-console.log"
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
    # OpenBMC dropbear is old; allow legacy algos if the client is new.
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
    """Run a single command over SSH via sshpass; return (rc, output)."""
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
    text = out.decode(errors="replace")
    return p.returncode, text


def ssh_send_file(local_path, remote_path, timeout=180):
    """Copy a local file to the BMC using sshpass + a raw stdin pipe.

    The BMC has no scp server and no `base64` applet, and the payload is far
    too large for argv (E2BIG). We stream the raw bytes to a remote `cat`.
    Without a PTY the SSH data channel is 8-bit clean, so no line discipline
    mangles the binary; a real subprocess pipe delivers EOF cleanly so `cat`
    terminates.
    """
    with open(local_path, "rb") as f:
        data = f.read()
    remote = (f"cat > {remote_path} && chmod +x {remote_path} "
              f"&& ls -l {remote_path} && echo XFER_OK")
    p = subprocess.Popen(
        ["sshpass", "-p", PASSWORD, "ssh"] + SSH_OPTS +
        ["root@127.0.0.1", remote],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)
    out, _ = p.communicate(input=data, timeout=timeout)
    text = out.decode(errors="replace")
    print(text[-400:], flush=True)
    return p.returncode, text


try:
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

    # Wait for the login banner to appear in the console log.
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
    # Poll for dropbear readiness instead of a fixed sleep: on this image
    # sshd can come up well after the login banner, and transferring too
    # early yields SSH rc=255 (connection refused).
    print("[*] waiting for BMC SSH to accept connections...", flush=True)
    ssh_deadline = time.time() + 180
    ssh_ready = False
    while time.time() < ssh_deadline:
        rc, out = ssh_run("echo SSH_READY", timeout=15)
        if rc == 0 and "SSH_READY" in out:
            ssh_ready = True
            break
        time.sleep(3)
    print(f"[*] BMC SSH ready: {ssh_ready}", flush=True)
    # Let mctp-local.service settle after sshd is up.
    time.sleep(5)

    def run(cmd, timeout=60):
        print(f"\n===== BMC# {cmd[:60]}... =====", flush=True)
        rc, out = ssh_run(cmd, timeout=timeout)
        print(out, flush=True)
        print(f"\n----- rc={rc} -----", flush=True)
        return rc, out

    # ---- Transfer the cross-compiled raw-netlink route helper -------------
    # This OpenBMC image's `mctp route add` CLI busy-loops (userspace bug: the
    # process spins at 100% CPU, wchan=0, syscall=running, empty kernel stack).
    # link set/up and addr add work fine, so we only replace the route step.
    # The BMC has no python/perl/compiler (busybox+awk only) and no base64
    # applet, so we ship a statically-linked ARM helper built on the host and
    # stream its raw bytes over SSH stdin (8-bit clean without a PTY).
    print("[*] transferring route helper (raw bytes over SSH stdin)...",
          flush=True)
    rc, out = ssh_send_file("/tmp/mctp_route_add", "/tmp/mctp_route_add",
                            timeout=180)
    print(f"[*] transfer rc={rc}; XFER_OK={'XFER_OK' in out}", flush=True)

    # Ship the AF_MCTP base responder so the BMC can *answer* inbound PLDM
    # (OpenBMC's pldmd is a requester and does not reply to base commands).
    print("[*] transferring PLDM base responder (raw bytes over SSH stdin)...",
          flush=True)
    rc, out = ssh_send_file("/tmp/pldm_base_responder",
                            "/tmp/pldm_base_responder", timeout=180)
    print(f"[*] responder transfer rc={rc}; XFER_OK={'XFER_OK' in out}",
          flush=True)

    bmc_script = r"""
exec > /tmp/hfm-result.txt 2>&1
set +e

tmo() {
    _t=$1; shift
    "$@" &
    _p=$!
    ( sleep "$_t"; kill -0 "$_p" 2>/dev/null && { echo "[tmo] '$*' HUNG >${_t}s"; kill -9 "$_p" 2>/dev/null; } ) &
    _w=$!
    wait "$_p" 2>/dev/null; _rc=$?
    kill "$_w" 2>/dev/null
    return $_rc
}

echo '### teardown stock unit'
systemctl kill --signal=SIGKILL mctp-local.service 2>/dev/null
systemctl reset-failed mctp-local.service 2>/dev/null
systemctl kill --signal=SIGKILL mctp-ldisc.service 2>/dev/null
systemctl reset-failed mctp-ldisc.service 2>/dev/null
pkill -9 -f 'mctp link serial' 2>/dev/null
sleep 1

echo '### start ldisc daemon + link up + addr (these CLI paths work)'
stty -F /dev/ttyS0 115200 litout -crtscts -ixon -echo raw
systemd-run --unit=mctp-ldisc --service-type=simple mctp link serial /dev/ttyS0
sleep 3
echo -n 'ldisc active? '; systemctl is-active mctp-ldisc.service
tmo 20 mctp link set mctpserial0 up ; echo "link-up rc=$?"
tmo 20 mctp addr add 8 dev mctpserial0 ; echo "addr rc=$?"

echo '### find mctpserial0 ifindex'
IFX=$(cat /sys/class/net/mctpserial0/ifindex 2>/dev/null)
echo "ifindex=$IFX"

echo '### install route via raw-netlink helper (bypasses buggy CLI)'
tmo 15 /tmp/mctp_route_add 18 "$IFX" ; echo "helper-route rc=$?"

echo '### pldmtool over EID 18 -- REAL kernel MCTP stack end-to-end'
tmo 30 pldmtool base GetTID -m 18 ; echo "GetTID rc=$?"
tmo 30 pldmtool base GetPLDMTypes -m 18 ; echo "GetPLDMTypes rc=$?"
tmo 30 pldmtool base GetPLDMVersion -m 18 -t 0 ; echo "GetPLDMVersion rc=$?"

echo '### reverse direction: BMC answers inbound PLDM from Zephyr (EID 18 -> EID 8)'
# OpenBMC pldmd is a requester and will not reply to base commands; it may also
# hold the PLDM MCTP type socket. Stop every pldm/mctp demux consumer, then run
# our AF_MCTP base responder.
echo '--- pldm-ish units ---'; systemctl list-units --all 2>/dev/null | grep -iE 'pldm|mctp' || echo none
for u in pldmd pldm xyz.openbmc_project.pldmd; do
    systemctl stop "$u" 2>/dev/null && echo "stopped $u"
done
pkill -9 -x pldmd 2>/dev/null && echo "pkilled pldmd"
sleep 1
echo '--- listeners on AF_MCTP (via /proc) ---'
for p in /proc/[0-9]*; do
    cmd=$(cat "$p/comm" 2>/dev/null)
    case "$cmd" in
        *pldm*|*mctp*) echo "still running: $cmd ($p)";;
    esac
done
echo '--- mctpserial0 rx BEFORE ---'; cat /sys/class/net/mctpserial0/statistics/rx_packets 2>/dev/null
chmod +x /tmp/pldm_base_responder
setsid /tmp/pldm_base_responder 8 < /dev/null > /tmp/responder.log 2>&1 &
RESP_PID=$!
echo "responder pid=$RESP_PID"
sleep 2
echo -n 'responder alive? '; kill -0 "$RESP_PID" 2>/dev/null && echo yes || echo no
echo '--- responder.log so far ---'; cat /tmp/responder.log 2>/dev/null
echo '### DONE (responder left running for the reverse probe)'
"""
    run(bmc_script, timeout=180)
    print("\n########## BMC RESULT FILE ##########", flush=True)
    run("cat /tmp/hfm-result.txt", timeout=40)

    # The BMC link/route are up now; the Zephyr node's requester thread keeps
    # retrying GetTID against EID 8. Dwell here polling the Zephyr console for
    # the reverse-direction success marker so the harness does not kill QEMU
    # before the reply arrives.
    print("\n[*] waiting for reverse-direction probe (Zephyr -> BMC)...",
          flush=True)
    rev_deadline = time.time() + 180
    rev_ok = False
    while time.time() < rev_deadline:
        try:
            with open(ZEPHYR_LOG, "r", errors="replace") as f:
                ztext = f.read()
            if "Reverse-direction PLDM probe to BMC complete" in ztext:
                rev_ok = True
                break
        except FileNotFoundError:
            pass
        time.sleep(3)
    print(f"[*] reverse-direction complete marker seen: {rev_ok}", flush=True)
    print("\n########## BMC RESPONDER LOG + RX COUNTERS ##########", flush=True)
    run("echo '--- responder.log ---'; cat /tmp/responder.log 2>/dev/null; "
        "echo '--- mctpserial0 rx AFTER ---'; "
        "cat /sys/class/net/mctpserial0/statistics/rx_packets 2>/dev/null; "
        "cat /sys/class/net/mctpserial0/statistics/rx_bytes 2>/dev/null; "
        "echo '--- rc-note ---'", timeout=40)
    print("\n########## ZEPHYR CONSOLE (tail) ##########", flush=True)
    try:
        with open(ZEPHYR_LOG, "r", errors="replace") as f:
            zlines = f.read().splitlines()
        for line in zlines[-25:]:
            print(line, flush=True)
    except FileNotFoundError:
        print("(no zephyr console log)", flush=True)

    print("\n[*] done issuing commands.", flush=True)
finally:
    print("[*] cleaning up QEMU instances.", flush=True)
    cleanup()
    time.sleep(1)
    print("[*] logs: BMC", OBMC_LOG, "| Zephyr", ZEPHYR_LOG, flush=True)
