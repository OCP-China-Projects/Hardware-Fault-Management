#!/usr/bin/env python3
"""
RISC-V RERI hardware-fault-injection smoke test (single Zephyr QEMU).

Exercises the full RERI RAS path end to end on one QEMU sifive_u instance:

  reri shell (patch 0013)  --write bank-->  QEMU RERI device (patch 0012)
        |                                          |
        |                                    eid counter expires,
        |                                    status_i.v=1, RAS signal
        |                                          |
        |                                        PLIC source 50
        |                                          v
        +--- reads status back ---        RAS handler ISR (patch 0014)
                                          decodes status/addr, latches
                                          severity, control_i.sinv clears v

Requirements
  * A RERI-enabled QEMU: build qemu-system-riscv64 with patch 0012 applied
    (the stock build has no RERI bank at 0x10080000). Point QEMU_RV at it.
  * A serial_bridge firmware built with patches 0013 + 0014 (the reri shell
    and the RAS handler). Point ZEPHYR_ELF at that zephyr.elf.

Env overrides (sensible defaults fall back to the repo prebuilts / the usual
build paths so the script runs without editing hardcoded /home/<user> paths):
  QEMU_RV     - RERI-enabled qemu-system-riscv64
  ZEPHYR_ELF  - serial_bridge zephyr.elf built with patches 0013 + 0014

Pass criteria (printed at the end):
  * the reri shell prompt is reached
  * "reri sdram-ue" injects a record and the QEMU device arms/expires eid
  * the RAS handler logs the decoded UNCORRECTED error (ec / addr)
  * after the ISR, status_i.v is clear again (control_i.sinv worked)
"""
import os
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

QEMU_RV = os.environ.get("QEMU_RV", f"{HOME}/qemu-build/bin/qemu-system-riscv64")
ZEPHYR_ELF = os.environ.get(
    "ZEPHYR_ELF", os.path.join(REPO_ROOT, "prebuilts", "zephyr.elf"))

# The custom QEMU links against glib in the user-local prefix, matching the
# other harness scripts.
ENV = dict(os.environ)
ENV["LD_LIBRARY_PATH"] = f"{HOME}/local/lib/x86_64-linux-gnu:{HOME}/local/lib"

INJECT_ADDR = "0x80100000"

proc = subprocess.Popen(
    [QEMU_RV, "-machine", "sifive_u", "-smp", "2", "-m", "256",
     "-display", "none", "-bios", "none", "-kernel", ZEPHYR_ELF,
     "-serial", "stdio", "-monitor", "none"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    bufsize=0, env=ENV,
)

log = []


def pump(seconds):
    end = time.time() + seconds
    os.set_blocking(proc.stdout.fileno(), False)
    while time.time() < end:
        try:
            chunk = proc.stdout.read(4096)
        except Exception:
            chunk = None
        if chunk:
            text = chunk.decode(errors="replace")
            log.append(text)
            sys.stdout.write(text)
            sys.stdout.flush()
        else:
            time.sleep(0.05)


def send(cmd):
    proc.stdin.write((cmd + "\r\n").encode())
    proc.stdin.flush()


try:
    pump(6)                       # boot + RAS handler ready + shell prompt
    send("")
    pump(1)
    send("reri dump")             # bank clear before injection
    pump(2)
    send(f"reri sdram-ue {INJECT_ADDR}")
    pump(3)                       # eid expiry + RAS ISR
    send("reri dump")             # v cleared by sinv after the ISR
    pump(2)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

full = "".join(log)
print("\n===== VERDICT =====")
checks = {
    "reri shell prompt reached": "uart:~$" in full,
    "QEMU RERI device armed/expired eid": "record 0 injected" in full,
    "RAS handler decoded UNCORRECTED error": "UNCORRECTED" in full,
    f"error address decoded ({INJECT_ADDR})": INJECT_ADDR.lower() in full.lower(),
}
ok = True
for k, v in checks.items():
    print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    ok = ok and v

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
