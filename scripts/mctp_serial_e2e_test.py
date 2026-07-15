#!/usr/bin/env python3
"""
Host-side validation for the Zephyr MCTP-over-serial (DSP0253) PLDM endpoint.

Plays the role OpenBMC's mctpd + pldmtool would: opens the shared unix socket
as a server, waits for the Zephyr QEMU instance to connect its second UART
(SiFive uart1), then sends DSP0253-framed PLDM Type 0 (base) requests to EID 18
and verifies the responses.

This exercises the real transport (QEMU unix-socket UART -> mctp_serial binding
-> libmctp -> PLDM responder) without needing the full OpenBMC boot.
"""

import os
import socket
import struct
import subprocess
import sys
import time

SOCK = "/tmp/hfm-mctp-test.sock"
ELF = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/terry.gong/workspace/Hardware-Fault-Management/prebuilts/zephyr.elf"
QEMU = os.path.expanduser("~/qemu-build/bin/qemu-system-riscv64")

BMC_EID = 8
ZEPHYR_EID = 18

FLAG = 0x7E
ESCAPE = 0x7D
REVISION = 0x01

# ---- RFC1662 FCS-16 (reflected, poly 0x8408), matches
#      modules/lib/libmctp/crc-16-ccitt.c (NOT the MSB-first CCITT/XMODEM) ----
def crc16_ccitt(data, crc=0xFFFF):
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc & 0xFFFF


def frame(mctp_pkt: bytes) -> bytes:
    """Wrap an MCTP packet in a DSP0253 serial frame."""
    length = len(mctp_pkt)
    fcs = crc16_ccitt(bytes([REVISION, length]) + mctp_pkt)
    body = bytearray()
    for c in mctp_pkt:
        if c in (FLAG, ESCAPE):
            body.append(ESCAPE)
            body.append(c ^ 0x20)
        else:
            body.append(c)
    return bytes([FLAG, REVISION, length]) + bytes(body) + \
        bytes([(fcs >> 8) & 0xFF, fcs & 0xFF, FLAG])


def mctp_packet(dest, src, msg: bytes, som=True, eom=True, tag_owner=True,
                tag=0) -> bytes:
    ver = 0x01
    flags = 0
    if som:
        flags |= 0x80
    if eom:
        flags |= 0x40
    if tag_owner:
        flags |= 0x08
    flags |= (tag & 0x07)
    return bytes([ver, dest, src, flags]) + msg


def pldm_request(instance, pldm_type, command, payload=b"") -> bytes:
    """MCTP message body: type byte 0x01 (PLDM) + PLDM header + payload."""
    rq = 0x80  # request
    b0 = rq | (instance & 0x1F)
    b1 = pldm_type & 0x3F  # hdr ver 00
    return bytes([0x01, b0, b1, command]) + payload


class Deframer:
    """Incremental DSP0253 deframer -> yields MCTP packets."""

    def __init__(self):
        self.state = "SYNC"
        self.length = 0
        self.buf = bytearray()
        self.escaped = False

    def feed(self, data):
        out = []
        for c in data:
            r = self._byte(c)
            if r is not None:
                out.append(r)
        return out

    def _byte(self, c):
        if self.state == "SYNC":
            if c == FLAG:
                self.state = "REV"
            return None
        if self.state == "REV":
            if c == REVISION:
                self.state = "LEN"
            elif c == FLAG:
                pass
            else:
                self.state = "SYNC"
            return None
        if self.state == "LEN":
            self.length = c
            self.buf = bytearray()
            self.escaped = False
            self.state = "DATA"
            return None
        if self.state == "DATA":
            if self.escaped:
                self.buf.append(c ^ 0x20)
                self.escaped = False
            elif c == ESCAPE:
                self.escaped = True
            else:
                self.buf.append(c)
            if len(self.buf) == self.length:
                self.state = "FCS1"
            return None
        if self.state == "FCS1":
            self.state = "FCS2"
            return None
        if self.state == "FCS2":
            self.state = "END"
            return None
        if self.state == "END":
            pkt = bytes(self.buf)
            self.state = "SYNC"
            return pkt
        return None


def parse_pldm(pkt):
    # pkt = mctp hdr(4) + msg type(1) + pldm hdr(3) + data
    if len(pkt) < 8:
        return None
    body = pkt[4:]
    if body[0] != 0x01:
        return None
    cc = body[4]
    data = body[5:]
    return cc, data


def main():
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    srv.listen(1)

    print(f"[host] listening on {SOCK}; launching Zephyr QEMU ...")
    qemu = subprocess.Popen(
        [QEMU, "-machine", "sifive_u", "-smp", "2", "-m", "256",
         "-nographic", "-bios", "none", "-kernel", ELF,
         "-serial", "file:/tmp/zephyr_e2e_console.log",
         "-serial", f"unix:{SOCK}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT)

    srv.settimeout(15)
    try:
        conn, _ = srv.accept()
    except socket.timeout:
        print("[host] FAIL: Zephyr never connected to the UART socket")
        qemu.terminate()
        return 2
    print("[host] Zephyr connected the second UART; giving it time to boot")
    conn.settimeout(5)
    time.sleep(2)

    deframer = Deframer()
    results = {}
    tests = [
        ("GetTID", 0x02, b""),
        ("GetPLDMTypes", 0x04, b""),
        ("GetPLDMVersion", 0x03,
         struct.pack("<IBB", 0, 0x01, 0x00)),  # handle=0, opflag=GetFirstPart, type BASE
    ]

    instance = 0
    for name, cmd, payload in tests:
        req = pldm_request(instance, 0, cmd, payload)
        pkt = mctp_packet(ZEPHYR_EID, BMC_EID, req)
        conn.sendall(frame(pkt))
        instance = (instance + 1) & 0x1F

        got = None
        deadline = time.time() + 4
        while time.time() < deadline:
            try:
                data = conn.recv(256)
            except socket.timeout:
                break
            if not data:
                break
            for p in deframer.feed(data):
                parsed = parse_pldm(p)
                if parsed:
                    got = parsed
                    break
            if got:
                break
        results[name] = got
        if got:
            cc, d = got
            print(f"[host] {name}: completion=0x{cc:02x} data={d.hex()}")
        else:
            print(f"[host] {name}: NO RESPONSE")

    conn.close()
    qemu.terminate()
    try:
        qemu.wait(timeout=5)
    except subprocess.TimeoutExpired:
        qemu.kill()
    os.unlink(SOCK)

    ok = results.get("GetTID") and results["GetTID"][0] == 0x00
    print("\n[host] RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
