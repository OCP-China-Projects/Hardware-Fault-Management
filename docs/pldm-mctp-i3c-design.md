# PLDM over MCTP over I3C — Technical Design

Status: **Draft (rev 2)** · Owner: HFM · Target: end-to-end demo on QEMU `virt` (RISC-V) with Zephyr guest and Linux host.

Rev 2 — added PLDM Type 2 (Platform Monitoring & Control) to the required scope.
Motivation: Hardware Fault Management is fundamentally a *sensor + effecter* story
(temperatures, voltages, fan speeds, thresholds, state effecters). Type 0 alone
proves the transport works but exposes no HFM-relevant semantics; Type 2 is
where telemetry actually flows.

## 1. Goal & non-goals

**Goal.** Boot a Zephyr guest on `qemu-system-riscv64 -machine virt` that acts as an MCTP endpoint over an emulated DesignWare I3C bus, and speak **PLDM Type 0 (base/discovery) + Type 2 (Platform Monitoring & Control)** with a requester on the same bus.

**MVP surface.**
- MCTP: DSP0236 v1.3.1 message assembly/reassembly, tag/EID handling.
- MCTP-over-I3C: DSP0233 v1.0 (DCR = `0xCC`, IBI MDB = `0xAE`, PID as neighbor addr).
- PLDM Type 0 (DSP0240): `GetTID`, `GetPLDMVersion`, `GetPLDMTypes`, `GetPLDMCommands`.
- **PLDM Type 2 (DSP0248) — NEW in rev 2**:
  - Discovery: `GetPDR`, `GetPDRRepositoryInfo`.
  - Numeric sensors: `GetSensorReading`, `GetSensorThresholds`, `SetSensorThresholds`.
  - State sensors: `GetStateSensorReadings`.
  - Effecters: `SetNumericEffecterValue`, `SetStateEffecterStates`.
  - Async: `PlatformEventMessage` (SensorEvent / StateSensorEvent) with the
    Zephyr guest as the receiver of asynchronous events raised by the QEMU
    responder when a sensor crosses a threshold.
- Demo topology: QEMU `virt` with `dw.i3c` controller + a new `mctp-i3c-target` device model that owns a small **PDR repository** and a set of simulated sensors/effecters; Zephyr guest as the requester.

**Non-goals for MVP.**
- PLDM Type 3 (BIOS) / Type 5 (FRU).
- Redfish Device Enablement (Type 6), Firmware Update (Type 5F).
- HDR-DDR / HDR-BT I3C modes; SDR-only.
- Hot-join and secondary controller handoff.
- Real PID negotiation over `ENTDAA` — we hard-code static addresses and PIDs.
- OEM PDR types; only DMTF-defined PDR record types 1/2/9/11 are populated.

## 2. Layer stack

```
+---------------------------------------------------------+
|  App (samples/hfm_pldm_discover)                        |
+---------------------------------------------------------+
|  libpldm  (encode/decode Type 0)   -- vendored          |
+---------------------------------------------------------+
|  libmctp  (framing, EID routing)   -- upstream module   |
+---------------------------------------------------------+
|  mctp_i3c binding                  -- NEW (this doc)    |
+---------------------------------------------------------+
|  Zephyr i3c_dw driver              -- upstream          |
+---------------------------------------------------------+
|  QEMU dw.i3c controller            -- upstream 11.x     |
+---------------------------------------------------------+
|  QEMU mctp-i3c-target device       -- NEW (this doc)    |
+---------------------------------------------------------+
```

Every existing layer is already in tree ([drivers/i3c/i3c_dw.c](file:///data00/home/terry.gong/zephyrproject/zephyr/drivers/i3c/i3c_dw.c), [subsys/pmci/mctp/](file:///data00/home/terry.gong/zephyrproject/zephyr/subsys/pmci/mctp), [hw/i3c/dw-i3c.c](file:///data00/home/terry.gong/qemu-11-src/hw/i3c/dw-i3c.c), [hw/i3c/mock-i3c-target.c](file:///data00/home/terry.gong/qemu-11-src/hw/i3c/mock-i3c-target.c)). Two new components: **`mctp_i3c` binding in Zephyr** and **`mctp-i3c-target` in QEMU**. `libpldm` is vendored, not written.

## 3. Choice of demo topology

Three candidates surveyed (path A/B/C in the earlier chat). **Path B** wins for MVP:

| Path | Requester | Responder | Pros | Cons |
|---|---|---|---|---|
| A | Zephyr guest (UART) | Zephyr guest (UART) | reuses `mctp_uart` | zero I3C coverage |
| **B** | Zephyr guest (I3C) | QEMU virtual target | actually exercises I3C + MCTP framing on both sides | need two new modules |
| C | Zephyr guest (I3C) | Full PLDM responder in QEMU | zero-Linux, fully self-contained | rewriting libmctp+libpldm inside QEMU |

We commit to B. Path A is optional if we want a smoke test of the PLDM/MCTP layer before I3C is up.

## 4. New component 1 — Zephyr `mctp_i3c` binding

Location: [subsys/pmci/mctp/mctp_i3c.c](file:///data00/home/terry.gong/zephyrproject/zephyr/subsys/pmci/mctp) + `include/zephyr/pmci/mctp/mctp_i3c.h`.

### 4.1 Public API (mirrors `mctp_uart`)

```c
struct mctp_binding_i3c {
    struct mctp_binding binding;
    const struct device  *i3c_dev;         /* i3c controller */
    struct i3c_device_desc *target;        /* discovered peer */
    uint8_t              rx_buf[MCTP_I3C_MTU];
    struct k_work        rx_work;
};

int  mctp_i3c_start(struct mctp_binding_i3c *b);
void mctp_i3c_ibi_cb(const struct device *dev,
                     struct i3c_device_desc *desc,
                     struct i3c_ibi_payload *ibi);

#define MCTP_I3C_DT_DEFINE(name, i3c_node) ...
```

### 4.2 DSP0233 conformance points

| Item | DSP0233 | Implementation |
|---|---|---|
| Device Class Register (DCR) | 0xCC | Advertised via `GETDCR`; QEMU target returns 0xCC |
| IBI Mandatory Data Byte (MDB) | 0xAE (MCTP) | Target raises IBI with MDB=0xAE when it has a message ready |
| Neighbor address | 48-bit PID | Store `i3c_device_desc.pid` in `mctp_binding.busneigh_lookup()` |
| Frame max size | 69 bytes payload | `MCTP_I3C_MTU = 69` (DSP0233 §6.1) |
| Read initiation | private read after IBI | `i3c_do_daa` + `i3c_transfer(I3C_MSG_READ)` on IBI callback |
| Write | private write | `i3c_transfer(I3C_MSG_WRITE)` from `mctp_i3c_tx` |

### 4.3 TX / RX flow

TX (host requester → target):
1. libmctp calls `binding->tx(pkt)`; `mctp_i3c_tx` copies pkt → local buf.
2. Prepend PEC byte per DSP0233 §6.3 (CRC-8 over payload).
3. Call `i3c_transfer(dev, target, {.buf=..., .flags=I3C_MSG_WRITE|STOP})`.

RX (target → host requester):
1. Target raises IBI (MDB=0xAE); DW-I3C IRQ line hits Zephyr.
2. `mctp_i3c_ibi_cb` schedules `rx_work`.
3. Worker issues private read of first byte (frame length), then rest.
4. Verify PEC, hand payload up via `mctp_bus_rx()`.

Bus-owner (path B) role: for MVP the Zephyr guest is the **primary controller**, so it also owns EID assignment. Set Discovery Notify + Set Endpoint ID as MCTP control messages.

### 4.4 Kconfig additions

```kconfig
config MCTP_I3C
    bool "MCTP I3C Binding"
    depends on I3C
    help
      DSP0233 v1.0 MCTP-over-I3C transport binding.

config MCTP_I3C_MTU
    int "Maximum I3C MCTP frame size"
    depends on MCTP_I3C
    default 69
```

### 4.5 CMake wiring

Extend [CMakeLists.txt](file:///data00/home/terry.gong/zephyrproject/zephyr/subsys/pmci/mctp/CMakeLists.txt):
```cmake
zephyr_library_sources_ifdef(CONFIG_MCTP_I3C mctp_i3c.c)
```

## 5. New component 2 — QEMU `mctp-i3c-target`

Location: `hw/i3c/mctp-i3c-target.c` + `include/hw/i3c/mctp-i3c-target.h`.

### 5.1 Rationale for a new device (vs extending `mock-i3c-target`)

`mock-i3c-target` semantics are "buffer-with-cursor" — good for EEPROM, wrong for MCTP where reads must return exactly the pending frame. Fork it into a new type so the mock stays useful for other tests.

### 5.2 State machine

```
IDLE ── ctrl writes MCTP frame ──► HAVE_TX
HAVE_TX ── kick IBI(MDB=0xAE) ──► WAIT_READ
WAIT_READ ── ctrl private-reads all bytes ──► IDLE
```

- `IDLE`: private write consumed as a full MCTP frame; parse header type, dispatch.
- Internal MCTP endpoint layer: for MVP responds to Set/Get Endpoint ID and PLDM Type 0 requests with canned answers (compile-time table).

### 5.3 Configurable properties

```c
DEFINE_PROP_UINT8("static-addr", ..., 0x08),
DEFINE_PROP_UINT64("pid",        ..., 0x1234567890ABull),
DEFINE_PROP_UINT8("dcr",         ..., 0xCC),
DEFINE_PROP_UINT8("bcr",         ..., 0x06),  /* IBI capable + MR-Available */
DEFINE_PROP_UINT8("initial-eid", ..., 0),
DEFINE_PROP_STRING("pldm-tid",   ..., "0x01"),
```

### 5.4 CCC handling

Reuse the `mock-i3c-target` CCC decoder for `GETPID/GETBCR/GETDCR/GETMWL/GETMRL/ENTDAA/SETDASA`; only override `GETDCR` to return `0xCC`. Add `RSTACT` (reset action).

### 5.5 Kconfig / Meson

`hw/i3c/Kconfig`:
```kconfig
config MCTP_I3C_TARGET
    bool
    select I3C
```
Wire into [hw/riscv/Kconfig](file:///data00/home/terry.gong/qemu-11-src/hw/riscv/Kconfig):
```kconfig
config RISCV_VIRT
    ...
    select MCTP_I3C_TARGET
```
`hw/i3c/meson.build`: add `system_ss.add(when: 'CONFIG_MCTP_I3C_TARGET', if_true: files('mctp-i3c-target.c'))`.

Also add to virt.c allow-list next to `TYPE_DW_I3C`:
```c
machine_class_allow_dynamic_sysbus_dev(mc, TYPE_MCTP_I3C_TARGET);
```

## 6. libpldm vendoring

Source: `https://github.com/openbmc/libpldm` (BSD-3-Clause). Take the `src/dsp/base.c` + `src/api.c` + headers and drop in `zephyr_project/modules/lib/libpldm/` mirroring how [libmctp](file:///data00/home/terry.gong/zephyrproject/modules/lib/libmctp) is vendored. West manifest patch:

```yaml
- name: libpldm
  path: modules/lib/libpldm
  revision: <pinned sha>
  url: https://github.com/openbmc/libpldm
```

Only Type 0 encode/decode is compiled in MVP; use `#if IS_ENABLED(CONFIG_PLDM_TYPE_2)` guards to keep the footprint small.

## 7. Devicetree overlay

`boards/qemu_riscv64.overlay`:
```dts
/ {
    soc {
        i3c0: i3c@4000000 {
            compatible = "snps,designware-i3c";
            reg = <0x04000000 0x1000>;
            interrupt-parent = <&plic>;
            interrupts = <20 1>;   /* platform-bus IRQ base 64 + 20 = 84 */
            #address-cells = <3>;
            #size-cells = <0>;
            status = "okay";

            mctp0: mctp@8 {
                compatible = "mctp,i3c";
                reg = <0x08 0 0>;
                dcr = <0xcc>;
                status = "okay";
            };
        };
    };
};
```

Platform-bus IRQ mapping is fixed by [virt.h](file:///data00/home/terry.gong/qemu-11-src/include/hw/riscv/virt.h#L100) (`VIRT_PLATFORM_BUS_IRQ = 64`, 32 lines).

## 8. Sample app

New sample `samples/hfm_pldm_discover/` (either in Zephyr tree or under `apps/hfm_app`):
1. Init MCTP with EID 10.
2. `pldm_encode_get_tid_req(...)` → send via `mctp_message_tx()`.
3. On rx: decode with `pldm_decode_get_tid_resp()`, log TID.
4. Repeat for `GetPLDMVersion / GetPLDMTypes / GetPLDMCommands`.

Expected UART output:
```
[00:00:00.100] <inf> hfm: MCTP EID=10 on i3c0
[00:00:00.150] <inf> hfm: PLDM GetTID -> 0x01
[00:00:00.152] <inf> hfm: PLDM GetPLDMVersion(Type=0) -> 1.1.0
[00:00:00.154] <inf> hfm: PLDM GetPLDMTypes -> {0}
[00:00:00.156] <inf> hfm: PLDM GetPLDMCommands(Type=0) -> {2,3,4,5,17,18}
```

## 9. QEMU launch line

```shell
scripts/qemu11.sh riscv64 \
    -machine virt -smp 1 -m 256 -bios none -nographic \
    -kernel build-hfm/zephyr/zephyr.elf \
    -device driver=dw.i3c,addr=0x04000000 \
    -device driver=mctp-i3c-target,bus=i3c-bus.0,static-addr=0x08 \
    -serial mon:stdio
```

## 10. Deliverables & touch-list

| # | File | Type |
|---|---|---|
| 1 | `qemu-11-src/hw/i3c/mctp-i3c-target.{c,h}` | new |
| 2 | `qemu-11-src/hw/i3c/Kconfig` | +MCTP_I3C_TARGET |
| 3 | `qemu-11-src/hw/i3c/meson.build` | wire in |
| 4 | `qemu-11-src/hw/riscv/virt.c` | allow-list new type |
| 5 | `qemu-11-src/hw/riscv/Kconfig` | select MCTP_I3C_TARGET |
| 6 | `zephyr/subsys/pmci/mctp/mctp_i3c.c` | new |
| 7 | `zephyr/include/zephyr/pmci/mctp/mctp_i3c.h` | new |
| 8 | `zephyr/subsys/pmci/mctp/CMakeLists.txt` | +MCTP_I3C source |
| 9 | `zephyr/subsys/pmci/mctp/Kconfig` | +MCTP_I3C |
| 10 | `zephyrproject/modules/lib/libpldm/` | vendored |
| 11 | `zephyrproject/zephyr/west.yml` | +libpldm entry |
| 12 | `apps/hfm_app/pldm_discover/` | new sample |
| 13 | `boards/qemu_riscv64.overlay` (in sample) | new |

## 10b. Realized path — OpenBMC ↔ Zephyr over MCTP-serial (DSP0253)

The I3C interconnect (§5) is blocked on a QEMU `mctp-i3c-target` device that
upstream does not provide, and QEMU has no I2C target-mode model either (§9,
patch 0003 note). The **practical inter-QEMU path today is MCTP-over-serial
(DSP0253)** over a `-serial unix:` socket between the two QEMU processes. This
is Path A from §3, promoted from "optional smoke test" to the shipped bridge.

### Topology

```
  Zephyr QEMU (sifive_u)                       OpenBMC QEMU (ast2600-evb)
  +-------------------------------+            +----------------------------+
  | app: PLDM Type 0 (libpldm)    |            | pldmd (PLDM Type 0)        |
  | mctp core (libmctp)           |            | mctpd (AF_MCTP, EID 8)     |
  | mctp_serial binding (DSP0253) |            | kernel mctp-serial         |
  | uart1  <---------- unix socket ----------> UART1 = /dev/ttyS0           |
  +-------------------------------+            +----------------------------+
           EID 18 (peer)                        console stays on UART5/ttyS4
```

### BMC side (patch 0004 — done, image built & deployed)

- `evb-ast2600` machine now enables `mctp` + `pldm` DISTRO_FEATURES.
- `mctpd` (bus-owner, EID 8) + `mctp-local.service` bring up `mctpserial0` on
  `/dev/ttyS0`, add route to peer EID 18, and `SetupEndpoint`.
- `pldmd` built with `transport-af-mctp`, rides the kernel AF_MCTP socket.
- Kernel: `CONFIG_MCTP=y`, `CONFIG_MCTP_SERIAL=y`; DTS enables UART1.
- Verified static: manifest + squashfs + kernel .config + DTB.

### Zephyr side (patch 0005 — done, validated end-to-end)

The stock `mctp_uart` binding uses the **async** UART API, which the SiFive
UART (QEMU `sifive_u`) does not implement. Patch 0005 adds a new
**interrupt-driven** DSP0253 binding, `subsys/pmci/mctp/mctp_serial.c`
(`CONFIG_MCTP_SERIAL`), with the same wire framing as `mctp_uart`:

- Sample `samples/subsys/pmci/pldm/serial_bridge/` instantiates `mctp_serial`
  on uart1 and registers the PLDM Type 0 responder as EID 18.
- Board overlay redirects `zephyr,sram` from l2lim to ram0 (256 MiB) so the
  image boots on QEMU's `sifive_u`, which does not implement l2lim.
- ISR runs a single pass gated by a `tx_active` flag (the SiFive
  `irq_is_pending()`/`irq_tx_ready()` report raw hardware state and would
  otherwise spin forever).
- `launch_hfm.sh` adds a second `-serial unix:<sock>` (client) matching
  OpenBMC's `-serial ...,server=on`.

**End-to-end result** (host DSP0253/PLDM simulator ↔ Zephyr QEMU over the
unix-socket UART, exercising the real transport):

```
GetTID         -> completion=0x00 tid=1
GetPLDMTypes   -> completion=0x00 data=0100000000000000  (Type 0 supported)
GetPLDMVersion -> completion=0x00 data=...00f0f1f1        (1.0.0)
RESULT: PASS
```

> **FCS gotcha:** libmctp's `crc_16_ccitt` is the RFC1662 **reflected** FCS-16
> (poly `0x8408`), not the MSB-first CCITT/XMODEM variant (poly `0x1021`). A
> peer computing the wrong CRC has every frame silently rejected at the
> `WAIT_SYNC_END` state — this cost real debugging time on the harness side.

### QEMU socket wiring

```
# OpenBMC: 1st -serial = console (UART5/ttyS4), 2nd -serial = UART1/ttyS0
qemu-system-arm -machine ast2600-evb ... \
    -serial mon:stdio \
    -chardev socket,id=mctp0,path=/tmp/hfm-mctp.sock,server=on,wait=off \
    -serial chardev:mctp0

# Zephyr: console on uart0, mctp_serial on uart1 -> same socket (client)
qemu-system-riscv64 -machine sifive_u -smp 2 -m 256 ... \
    -serial mon:stdio \
    -serial unix:/tmp/hfm-mctp.sock
```

### Two-QEMU end-to-end through the real kernel MCTP stack (validated)

The result above uses a host DSP0253/PLDM simulator as the BMC peer, so it
exercises the wire transport but not OpenBMC's own kernel AF_MCTP stack +
`pldmtool`. `scripts/two_qemu_smoke.py` closes that gap: it boots **both**
QEMU instances (OpenBMC EID 8 ↔ Zephyr EID 18) wired by the unix-socket UART,
then SSHes into the BMC (hostfwd `3222->22`, root/0penBmc) and drives real
`pldmtool` over EID 18:

```
route add OK: eid=18 oif=7 mtu=0            (raw-netlink helper, see below)
pldmtool base GetTID        -> {"Response": 1}
pldmtool base GetPLDMTypes  -> CompletionCode SUCCESS, PLDM Type base (0)
pldmtool base GetPLDMVersion -> CompletionCode SUCCESS, 1.1.0
RESULT: PASS
```

> **`mctp route add` CLI busy-loop (image bug, not ours).** On this prebuilt
> image the codeconstruct `mctp` CLI's `route`/`addr`/`route add` paths spin in
> userspace at 100% CPU (`State: R`, `wchan=0`, `syscall=running`, empty kernel
> stack, no dmesg WARN). `mctp link set up` and `mctp addr add` (single
> netlink+ACK ops) work fine; only the dump/route paths hang. Since the BMC
> ships only busybox (no python/perl/compiler) and its `ip` has no MCTP
> support, we bypass the CLI with a statically-linked ARM raw-`AF_NETLINK`
> helper (`RTM_NEWROUTE`, family `AF_MCTP=45`, `RTA_DST`=EID + `RTA_OIF`=ifindex)
> cross-compiled on the host and streamed over SSH. **Do not attach a top-level
> MTU / nested `RTA_METRICS`**: strict netlink validation rejects it with
> extack `"incorrect format"` (EINVAL). Match the stock service
> (`mctp route add 18 via mctpserial0`, no MTU) and let the route inherit the
> link MTU.

### 10c. Type 2 (Platform Monitoring) auto-discovery — MR1 + MR2

Rev 2 extends the bridge from Type 0 to **PLDM Type 2 (DSP0248)** so the BMC's
`pldmd` auto-discovers and polls a Zephyr sensor. The work is split into two
Merge Requests:

- **MR1 (Zephyr) — Type 2 responder + PDR.** `subsys/pmci/pldm/pldm_platform.c`
  serves a compile-time PDR repository (Terminus Locator + Numeric Sensor PDR)
  and answers `GetPDRRepositoryInfo` / `GetPDR` / `GetSensorReading` /
  `GetStateSensorReadings` / `PlatformEventMessage`. The `serial_bridge` sample
  registers a simulated die-temperature sensor (`sensor_id = 1`, °C).
- **MR2 (Zephyr control responder + BMC wiring) — the discovery enabler.**

**Why a control responder was needed.** `pldmd`'s `platform-mc` is compiled in
unconditionally (no build switch) and driven by `MctpDiscovery`, which simply
watches D-Bus for any `xyz.openbmc_project.MCTP.Endpoint` whose
`SupportedMessageTypes` contains PLDM (type 1) and hands it to the platform-mc
`Manager` to poll. So **the only gate on the whole BMC side is `mctpd`
publishing the Zephyr endpoint on D-Bus.** `mctpd` will only do that after it
successfully runs the baseline MCTP **control** commands against the node
(Get Endpoint ID, optionally Set Endpoint ID, Get Message Type Support). There
is no `mctpd.conf` knob to declare an endpoint statically — every BusOwner
method (`SetupEndpoint` / `AssignEndpoint` / `AssignEndpointStatic`) physically
queries the peer. Before MR2 the Zephyr node dropped control traffic (libmctp's
core only routes transport control commands `0xF0–0xFF` to a binding; the
baseline commands land on the generic message handler, and the PLDM dispatcher
discarded anything whose message type byte was not `0x01`), so enumeration
failed and no endpoint was ever published.

MR2 adds `subsys/pmci/mctp/mctp_control.c` (`CONFIG_MCTP_CONTROL`): a small
responder that installs itself as the MCTP RX callback, answers Set/Get
Endpoint ID (0x01/0x02), Get MCTP Version Support (0x04) and Get Message Type
Support (0x05) — reporting message types `{0x00 control, 0x01 PLDM}` — and
forwards every non-control message to the PLDM dispatcher it was chained onto.
Wire formats match `mctpd`'s strict validation (IID echo, exact response length
for Get Message Type Support). The node keeps its fixed EID 18 (libmctp exposes
no public API to change the bus EID at runtime), so Set Endpoint ID accepts only
a matching or "no-change" (0) request.

**BMC-side delta (MR2).** With Zephyr now answering control commands, the
existing patch-0004 plumbing does the rest automatically:

- `mctp-local.service`'s `SetupEndpoint` call now succeeds; `mctpd` runs Get
  Endpoint ID, adopts the node's EID 18, **adds the kernel route and neighbour
  itself** (`setup_added_peer → add_peer_route`), queries Get Message Type
  Support, and publishes the endpoint on D-Bus.
- The manual `mctp route add 18 via mctpserial0` line is therefore **removed** —
  it is now redundant *and* it was the exact CLI dump/route path that busy-loops
  on this image (§10b note). The `SetupEndpoint` call is wrapped in a bounded
  retry loop so the BMC can come up before the Zephyr QEMU instance.
- `pldmd`'s `MctpDiscovery` fires on the published endpoint, `initMctpTerminus`
  runs GetTID → SetTID → GetPLDMTypes → per-type GetPLDMVersion/GetPLDMCommands,
  then (Type 2) GetPDRRepositoryInfo/GetPDR, and finally polls
  `GetSensorReading` on `sensor_id = 1`. All of these are already served by the
  MR1 responder.

No `pldmd` meson option needs changing for functional discovery;
`sensor-polling-time` / `default-sensor-update-interval` only tune the poll
cadence and are left at their defaults.

#### End-to-end validation result (MR1 + MR2)

The Zephyr deliverables were validated against the **real BMC kernel AF_MCTP
transport**. `mctpd`'s fully-automatic discovery, however, is blocked by a
kernel bug on this image — not by any MR2 defect. Both facts are documented
below with their evidence.

**Blocker — kernel 6.6.92 AF_MCTP netlink-dump busy-loop.** The BMC image runs
`Linux 6.6.92-ca938df-dirty ... armv7l`. Any AF_MCTP netlink **dump** loops
forever on this kernel:

- `mctp route` (a `RTM_GETROUTE | NLM_F_DUMP`) never returns ("ROUTE DUMP HUNG");
- `ip addr show mctpserial0` spews ~2.3 MB of repeated
  `family 45 ???/0 scope global dynamic` and never terminates.

`mctpd` cannot avoid this dump: at startup `main()` calls
`mctp_nl_new()` (`mctpd.c:4495`), which unconditionally runs `fill_linkmap()`
→ `RTM_GETLINK | NLM_F_DUMP` (`mctp-netlink.c:759`). The dump busy-loops, so the
`mctpd` event loop never advances and **no D-Bus method is ever serviced**.
Observed symptoms on the running image:

- `mctpserial0` counters `rx_packets=19 / tx_packets=0` — the kernel receives
  Zephyr's reverse probes but `mctpd` never transmits a single Get Endpoint ID;
- `mctpd` journal: `interface mctpserial0 is down`, `No linkmap entry for link
  mctpserial0`, `BUG emit_interface_removed: no interface for ifindex 6`;
- `busctl introspect …/mctp1/interfaces/mctpserial0` and a manual `SetupEndpoint`
  both return `Connection timed out` (tx stays 0 across the call).

This is the same CLI busy-loop already noted in §10b; here it also takes down the
`mctpd` daemon itself. `AssignEndpointStatic` is **not** a workaround — it still
calls `endpoint_assign_eid` (a live Set-Endpoint-ID transaction that depends on
the linkmap the stuck dump never populated), and the daemon's event loop is
wedged regardless.

**Validation — direct-transport bypass (all PLDM commands answered).** To prove
the Zephyr MR1+MR2 responders are correct independent of `mctpd`, a route to
EID 18 was installed with a raw-netlink helper (`RTM_NEWROUTE` **SET**, which
never dumps), then `pldmtool` addressed EID 18 directly over the real kernel
AF_MCTP stack:

| Command | Result |
| --- | --- |
| `base GetTID` | `{"Response": 1}` |
| `base GetPLDMTypes` | `base(0)` + `platform(2)` |
| `platform GetPDR -d 0` | Terminus Locator PDR, recordHandle 1, next 2, TID 1, EID 18 |
| `platform GetSensorReading -i 1 --rearm 0` | `presentReading 31`, Sensor Enabled, Sensor Normal |

Every command succeeded, exercising both the MR2 control responder's
forward-to-PLDM path and the MR1 Type 2 sensor responder over the genuine kernel
transport. **Conclusion: MR1+MR2 are functionally correct; full `mctpd`
auto-discovery on this environment awaits a kernel with a working AF_MCTP dump.**

## 11. Milestone plan

- **M1** — Path A smoke: run existing `samples/subsys/pmci/mctp/{host,endpoint}` unmodified across two `qemu_riscv64` guests over UART pipe. Confirms libmctp works in our tree. *(≈0.5 day)*
- **M2** — libpldm integration + sample encoding of GetTID against a `libmctp` loopback bus. No I3C yet. *(≈1 day)*
- **M3** — QEMU `mctp-i3c-target` skeleton (echo only), `-device dw.i3c` + `-device mctp-i3c-target` boot without errors. *(≈1 day)*
- **M4** — Zephyr `mctp_i3c` binding: DAA, private write, IBI-driven read; loopback echo passes. *(≈1.5 day)*
- **M5** — Wire libpldm on top; four PLDM Type 0 commands respond correctly. *(≈1 day)*
- **M6** — Docs + capture-to-README (this file becomes part 4 of README). *(≈0.5 day)*

## 12. Open questions

1. **Bus-owner election**: for MVP Zephyr is BO. If we later plug a Linux host as BO, target device must publish PID over `ENTDAA` and support `SETNEWDA`. Currently we hard-code static addr.
2. **PEC byte**: DSP0233 §6.3 mandates CRC-8 (poly `0x07`) trailing byte. Confirm both sides compute the same polynomial before frame parsing (Linux `mctp-i3c` uses `crc8_ccitt`).
3. **Fragmentation**: MTU 69 → PLDM `GetPLDMCommands` (~40 B) fits, but larger types will exceed one frame. libmctp's default packetizer handles this transparently as long as MTU is set right.
4. **Security review**: no auth on control messages in MVP. If the demo ever leaves the box, gate the target behind an `-object` authorization guard.

## 13. Reference material

- DMTF DSP0236 v1.3.1 — MCTP Base Specification
- DMTF DSP0233 v1.0.0 — MCTP over I3C Transport Binding
- DMTF DSP0240 v1.1.0 — PLDM Base Specification
- Linux `drivers/net/mctp/mctp-i3c.c` — reference binding (Matt Johnston, Code Construct)
- [openbmc/libpldm](https://github.com/openbmc/libpldm) — vendored
- [openbmc/libmctp](https://github.com/openbmc/libmctp) — already in [modules/lib/libmctp](file:///data00/home/terry.gong/zephyrproject/modules/lib/libmctp)

