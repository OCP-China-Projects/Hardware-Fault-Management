# Local MR set — MCTP bindings + PLDM (Type 0 & Type 2) for Zephyr, and MCTP/PLDM for OpenBMC

The OCP HFM prototype is delivered as eleven numbered patches split across two
trees. Zephyr patches stack on tag `v4.3.0`; OpenBMC patches are Yocto-layer
diffs rooted at the OpenBMC tree top.

With patches 0006, 0010 and 0011 in place the stack now runs **fully
automatic** end to end: on a cold boot mctpd discovers the Zephyr endpoint by
itself (SetupEndpoint), installs the kernel route and neighbour, and publishes
it on D-Bus for `pldmd`. No manual `mctp route add` or raw-netlink helper is
needed.

**Zephyr side** (branch chain
`hfm/mctp-i3c-binding` → `hfm/pldm-type0` → `hfm/mctp-i2c-binding` →
`hfm/pldm-type2`):

- 0001 MCTP-over-I3C binding (DSP0233)
- 0002 PLDM subsystem + Type 0 (DSP0240)
- 0003 MCTP-over-SMBus/I2C binding (DSP0237)
- 0005 MCTP-over-serial binding (DSP0253)
- 0007 PLDM Type 2 platform responder + PDR (DSP0248) — MR1
- 0008 MCTP control responder (DSP0236) + serial_bridge Type 2 wiring — MR2
- 0009 set MCTP tag-owner bit on requester TX — reverse-path fix (MR3)
- 0011 clear MCTP tag-owner bit on control responses — auto-discovery fix (MR4)

**OpenBMC side** (Yocto layers):

- 0004 enable MCTP + PLDM over serial on `evb-ast2600`
- 0006 order the serial link before mctpd so auto-discovery installs the route
- 0010 backport the AF_MCTP netlink-dump fix to the `evb-ast2600` kernel

| # | Patch | Target / Branch (local) | Apply |
|---|---|---|---|
| 1 | `0001-subsys-pmci-mctp-add-MCTP-over-I3C-binding-DSP0233-v.patch` | Zephyr `hfm/mctp-i3c-binding` | `git am` |
| 2 | `0002-subsys-pmci-add-PLDM-subsystem-and-DSP0240-Type-0-su.patch`  | Zephyr `hfm/pldm-type0` | `git am` |
| 3 | `0003-subsys-pmci-mctp-add-MCTP-over-SMBus-I2C-binding-DSP.patch`  | Zephyr `hfm/mctp-i2c-binding` | `git am` |
| 4 | `0004-openbmc-evb-ast2600-enable-mctp-pldm-serial.patch`           | OpenBMC (Yocto layers) | `git apply` |
| 5 | `0005-subsys-pmci-mctp-add-MCTP-over-serial-binding-DSP025.patch`  | Zephyr `hfm/mctp-i2c-binding` | `git am` |
| 6 | `0006-openbmc-evb-ast2600-mctp-local-auto-discovery-retry.patch`   | OpenBMC (Yocto layers) | `git apply` |
| 7 | `0007-subsys-pmci-pldm-add-DSP0248-Type-2-Platform-Monitor.patch`  | Zephyr `hfm/pldm-type2` | `git am` |
| 8 | `0008-subsys-pmci-mctp-add-DSP0236-control-responder-wire-.patch`  | Zephyr `hfm/pldm-type2` | `git am` |
| 9 | `0009-subsys-pmci-pldm-set-MCTP-tag-owner-bit-on-requester.patch`   | Zephyr `hfm/pldm-type2` | `git apply` |
| 10 | `0010-openbmc-evb-ast2600-kernel-fix-mctp-netlink-dump-busy-loop.patch` | OpenBMC (Yocto layers) | `git apply` |
| 11 | `0011-subsys-pmci-mctp-clear-tag-owner-bit-on-control-response.patch`   | Zephyr `hfm/pldm-type2` | `git am` |
| 13 | `0013-zephyr-serial_bridge-add-riscv-reri-injection-shell.patch`         | Zephyr `hfm/pldm-type2` | `git am` |
| 14 | `0014-zephyr-serial_bridge-add-riscv-reri-ras-handler.patch`             | Zephyr `hfm/pldm-type2` | `git am` |

Patches 0012-0014 form the RISC-V RERI hardware-fault-injection demo and apply
together: 0012 (QEMU) adds the RERI error bank to `sifive_u`; 0013 (Zephyr)
adds the `reri` injection shell; 0014 (Zephyr) adds the RAS handler that
decodes the error and reports it over PLDM Type 2. Apply 0013 before 0014.

Auxiliary (non-numbered) patches, applied per README Part 1/3 rather than
as part of the MR stack:

| Patch | Target | Purpose |
|---|---|---|
| `qemu-riscv-virt-allow-dw-i3c.patch` | QEMU 11.0.0 tree top | allow-list `TYPE_DW_I3C` on the `virt` platform bus + `select I3C/DW_I3C` |
| `0012-qemu-sifive_u-add-riscv-reri-error-bank.patch` | QEMU 11.0.0 tree top | add a RISC-V RERI (v1.0) error bank device to `sifive_u` at 0x10080000, RAS signal wired to PLIC source 50 (hardware fault-injection demo) |
| `zephyr-fu700-pll-lock-qemu-timeout.patch` | Zephyr tree | bound PLL_LOCK wait so the prebuilt `hifive_unmatched` ELF boots on QEMU |
| `zephyr-qemu-hifive.overlay` | Zephyr build overlay | redirect `zephyr,sram` to DDR, shrink `ram0` to 256 MiB |
| `openbmc-bitbake-disable_network-erofs.patch` | OpenBMC tree | let `do_unpack` run where `/proc/self/uid_map` is read-only |

---

## Patch 0001 — MCTP over I3C binding (DSP0233 v1.0)

Adds a new libmctp transport binding on top of Zephyr's I3C controller API:

- IBI-driven RX (MDB = 0xAE) → deferred private I3C read → PEC verify → `mctp_bus_rx()`.
- Private I3C write TX with PEC append (CRC-8, poly `0x07`, seed `0x00`).
- New Kconfig `CONFIG_MCTP_I3C` (depends on `I3C_CONTROLLER`, `select I3C_USE_IBI`).
- New Kconfig `CONFIG_MCTP_I3C_MTU`, default 69 B (per DSP0233 §6.1).
- Static instantiation macro `MCTP_I3C_DT_DEFINE(name, i3c_dev, target_desc)`.

### Files

| File | Change |
|---|---|
| `include/zephyr/pmci/mctp/mctp_i3c.h` | new — binding descriptor + API + macro |
| `subsys/pmci/mctp/mctp_i3c.c` | new — DSP0233 impl (start/tx/ibi_cb/rx_worker) |
| `subsys/pmci/mctp/CMakeLists.txt` | wire `mctp_i3c.c` under `CONFIG_MCTP_I3C` |
| `subsys/pmci/mctp/Kconfig` | add `MCTP_I3C` and `MCTP_I3C_MTU` |

---

## Patch 0002 — PLDM subsystem + DSP0240 Type 0

Layers DMTF DSP0240 (PLDM Messaging Control and Discovery) on top of the
existing MCTP core. Uses [openbmc/libpldm](https://github.com/openbmc/libpldm)
as a Zephyr module for wire encode/decode.

### Public API (`include/zephyr/pmci/pldm/pldm.h`)

```c
int  pldm_ctx_init(struct pldm_ctx *ctx, struct mctp *mctp, mctp_eid_t eid);
int  pldm_register_type(struct pldm_ctx *ctx, uint8_t pldm_type,
                        pldm_type_handler_fn fn);
int  pldm_send_request_sync(struct pldm_ctx *ctx, mctp_eid_t peer_eid,
                            uint8_t pldm_type, uint8_t command,
                            const void *req_body, size_t req_len,
                            void *resp_buf, size_t resp_size, size_t *resp_len);
```

### Type 0 helpers (`include/zephyr/pmci/pldm/pldm_base.h`)

- Responder: `pldm_base_responder_register()` handles
  `SetTID` / `GetTID` / `GetPLDMVersion` / `GetPLDMTypes` / `GetPLDMCommands`.
- Requester: `pldm_base_get_tid_sync()`, `pldm_base_get_types_sync()`,
  `pldm_base_get_version_sync()`.

### Kconfig

```
CONFIG_PLDM_SUBSYS                   Enable subsystem (depends on MCTP)
CONFIG_PLDM_RESPONDER                Enable responder path
CONFIG_PLDM_REQUESTER                Enable requester path
CONFIG_PLDM_TYPE_BASE                Type 0 responder + requester helpers
CONFIG_PLDM_REQUESTER_TIMEOUT_MS     Per-request timeout (default 2000)
CONFIG_PLDM_MAX_INSTANCES            In-flight requests (default 4, max 32)
CONFIG_PLDM_BASE_INITIAL_TID         TID advertised before SetTID
```

### Sample

`samples/subsys/pmci/pldm/type0_loopback/` — wires two MCTP contexts to an
in-memory loopback binding and round-trips `GetTID` / `GetPLDMTypes` /
`GetPLDMVersion`. Verified on `qemu_riscv32`:

```
*** Booting Zephyr OS build v4.3.0 ***
GetTID -> 0x01
GetPLDMTypes -> byte0=0x01
GetPLDMVersion(BASE) -> 1.1.0
PLDM Type 0 loopback OK
```

### west manifest change

Adds openbmc/libpldm as a Zephyr module (`modules/lib/libpldm`, pinned to
`df0a2219`). Libpldm's upstream tree already ships a `zephyr/` glue
directory, so no in-tree wrapper is required.

---

## Patch 0003 — MCTP over SMBus/I2C binding (DSP0237 v1.2)

Adds the standard DMTF DSP0237 MCTP-over-SMBus/I2C transport binding, wire
compatible with the Linux kernel `mctp-i2c` driver used by OpenBMC. Unlike the
pre-existing `mctp_i2c_gpio_*` bindings (a custom GPIO + pseudo-register FIFO
scheme), this is the real SMBus block-write framing.

- Wire frame: `[0x0F][byte_count][src<<1|1][MCTP packet][PEC]`.
- TX: guest is the bus controller → `i2c_write()` block write to the peer.
- RX: guest registers as an I2C target; target callbacks reassemble the block
  write and a single decode entry point (`mctp_i2c_input`) validates command
  code, byte count and SMBus PEC (CRC-8, poly `0x07`, seed `0x00` over the
  destination write address + frame) before `mctp_bus_rx()`.
- Optional `link_write` hook routes the framed buffer without a real
  controller (used by the loopback sample).
- New Kconfig `CONFIG_MCTP_I2C` (depends on `I2C`) and `CONFIG_MCTP_I2C_MTU`
  (default 68 B = 4 B MCTP header + 64 B BTU).
- Static instantiation macro `MCTP_I2C_DEFINE(name, i2c_dev, own_addr, dest_addr)`.

### Files

| File | Change |
|---|---|
| `include/zephyr/pmci/mctp/mctp_i2c.h` | new — binding descriptor + API + macro |
| `subsys/pmci/mctp/mctp_i2c.c` | new — DSP0237 impl (start/tx/target cbs/input) |
| `subsys/pmci/mctp/CMakeLists.txt` | wire `mctp_i2c.c` under `CONFIG_MCTP_I2C` |
| `subsys/pmci/mctp/Kconfig` | add `MCTP_I2C` and `MCTP_I2C_MTU` |
| `samples/subsys/pmci/pldm/i2c_loopback/` | new — PLDM Type 0 over DSP0237 loopback |

### Sample

`samples/subsys/pmci/pldm/i2c_loopback/` — cross-wires two real
`mctp_binding_i2c` instances via the `link_write` hook so each transmitted
SMBus block write is decoded by the peer's `mctp_i2c_input()`, then
round-trips PLDM Type 0. Exercises the full DSP0237 framing/PEC path.
Verified on `qemu_riscv32`:

```
*** Booting Zephyr OS build v4.3.0 ***
GetTID -> 0x01
GetPLDMTypes -> byte0=0x01
GetPLDMVersion(BASE) -> 1.1.0
PLDM over MCTP over I2C loopback OK
```

> **Inter-QEMU note:** connecting two live QEMU instances over I2C still needs
> an I2C controller model with target-mode support, which upstream QEMU does
> not provide (same wall as I3C). This binding makes Zephyr wire-compatible
> with OpenBMC's `mctp-i2c`; the UART / `mctp-serial` bridge remains the
> practical inter-QEMU path today.

---

## Patch 0004 — OpenBMC evb-ast2600: MCTP + PLDM over serial

Enables the DMTF PMCI stack on the QEMU `ast2600-evb` machine and brings up an
MCTP-over-serial (DSP0253) link so the BMC can talk PLDM Type 0 to an external
endpoint (the Zephyr QEMU) over a `-serial unix:` socket. This is the BMC-side
counterpart to the Zephyr `mctp_uart` (DSP0253) binding.

The stock `evb-ast2600` image ships **no** mctp/pldm — the enable chain
(`DISTRO_FEATURES` → `df-mctp`/`df-pldm` overrides → `packagegroup-obmc-apps-dmtf-pmci`)
is off for this machine. This patch turns it on, mirroring `meta-evb-fvp-base`.

### Files

| File | Change |
|---|---|
| `meta-aspeed/conf/machine/evb-ast2600.conf` | `require conf/distro/include/pldm.inc` (chains mctp.inc) + `IMAGE_INSTALL:append = " pldm"` |
| `meta-evb/.../recipes-phosphor/mctp/mctp_%.bbappend` | ship + enable `mctp-local.service`; install `mctpd.conf` |
| `meta-evb/.../recipes-phosphor/mctp/files/mctp-local.service` | new — bus-owner serial link on `/dev/ttyS0` |
| `meta-evb/.../recipes-phosphor/mctp/files/mctpd.conf` | new — `mode = "bus-owner"` |
| `meta-evb/.../recipes-kernel/linux/linux-aspeed_%.bbappend` | `SRC_URI:append:df-mctp` DTS patch |
| `meta-evb/.../recipes-kernel/linux/linux-aspeed/0001-...uart1-for-mctp-serial.patch` | new — enable AST2600 UART1 (`serial0` → `/dev/ttyS0`) |

### What ships

`mctpd` + `mctp` CLI (Code Construct, kernel AF_MCTP), `pldmd` + `pldmtool` +
`libpldm`/`libpldmresponder`. `pldm` is built with
`PACKAGECONFIG:append:df-mctp = " transport-af-mctp"`, so it rides the kernel
AF_MCTP socket fed by the serial link. `mctp-local.service` (wired into
`mctpd.service.wants`) runs:

```
mctp link serial /dev/ttyS0       # -> mctpserial0
mctp link set mctpserial0 up
mctp addr add 8 dev mctpserial0    # BMC local EID = 8
mctp route add 18 via mctpserial0  # peer (Zephyr) EID = 18
busctl call ... SetupEndpoint      # discover the remote endpoint
```

### UART mapping (QEMU ast2600-evb)

`serial_hd(0)` → UART5 = console/`ttyS4`; the **second** `-serial` backend →
UART1 = `ttyS0` (alias `serial0`). The DTS patch flips UART1 `status = "okay"`;
console stays on UART5. So the bridge peer connects to the machine's second
`-serial`.

### Verification (static, on the build host)

- `bitbake obmc-phosphor-image` → 5854 tasks, all succeeded (rc=0).
- manifest carries `mctp`, `pldm`, `pldmtool`, `pldm-libs`, `libpldm0`.
- squashfs rootfs ships `/usr/sbin/mctpd`, `/usr/bin/{mctp,pldmd,pldmtool}`,
  `libpldm*.so`, and `mctp-local.service` symlinked from `mctpd.service.wants`.
- kernel `.config`: `CONFIG_MCTP=y`, `CONFIG_MCTP_SERIAL=y`.
- built DTB: `serial@1e783000` (UART1) `status = "okay"`, alias `serial0`.

### How to apply

The patch is a plain unified diff rooted at the OpenBMC tree top
(`meta-aspeed/...`, `meta-evb/...`). From the OpenBMC checkout:

```sh
cd /path/to/openbmc
git apply /path/to/Hardware-Fault-Management/patches/0004-openbmc-evb-ast2600-enable-mctp-pldm-serial.patch
# then: source oe-init-build-env build && bitbake obmc-phosphor-image
```

> **Build note:** on a 31 GB RAM host with no swap, the C++23 + LTO daemon
> builds (pldm, phosphor-*) OOM under BitBake defaults. Cap parallelism in
> `build/conf/local.conf`: `BB_NUMBER_THREADS = "4"` and `PARALLEL_MAKE = "-j 4"`.

---

## Patch 0005 — MCTP over serial binding (DSP0253), interrupt-driven

Adds the DMTF DSP0253 MCTP-over-serial transport binding on top of Zephyr's
interrupt-driven UART API, plus a PLDM Type 0 sample that bridges a Zephyr
QEMU instance to the OpenBMC QEMU instance over a host unix socket. This is
the Zephyr-side counterpart to patch 0004 and the practical inter-QEMU MCTP
path today (I3C/I2C need target-mode QEMU models that upstream lacks).

- Wire frame: identical to `mctp_uart` — `0x7e` flags, `0x7d` byte stuffing,
  RFC1662 FCS-16 (reflected, poly `0x8408`, seed `0xffff`). Wire compatible
  with the Linux kernel `mctp-serial` driver used by OpenBMC.
- I/O path uses only `uart_fifo_read` / `uart_fifo_fill` (interrupt-driven
  API), so it runs on controllers without async/DMA — notably the SiFive UART
  on QEMU's `sifive_u` machine.
- RX: per-byte DSP0253 decode state machine feeding `mctp_bus_rx()`.
- TX: DSP0253-framed packet pushed into a ring buffer, drained from the ISR.
- New Kconfig `CONFIG_MCTP_SERIAL` (depends on `SERIAL` + `UART_INTERRUPT_DRIVEN`,
  selects `RING_BUFFER`).
- Static instantiation macro `MCTP_SERIAL_DT_DEFINE(name, uart_dev)`.

### SiFive UART / QEMU caveats baked into the binding

- `irq_is_pending()` / `irq_tx_ready()` report the **raw** hardware condition:
  TXWM stays asserted whenever the TX FIFO is below the watermark, and
  `irq_update()` always returns 1. Looping on `irq_is_pending()` would never
  terminate, so the ISR runs a **single pass** and gates the TX FIFO behind a
  `tx_active` flag.
- QEMU's `sifive_u` model does not implement l2lim (`0x08000000`), where the
  stock fu540 devicetree pins `zephyr,sram`. The sample's board overlay
  redirects `zephyr,sram` to `ram0` (DDR at `0x80000000`) and shrinks it to
  256 MiB to match `qemu -m 256`.

### Files

| File | Change |
|---|---|
| `include/zephyr/pmci/mctp/mctp_serial.h` | new — binding descriptor + RX state enum + API + macro |
| `subsys/pmci/mctp/mctp_serial.c` | new — DSP0253 impl (consume/tx/isr/start_rx) |
| `subsys/pmci/mctp/CMakeLists.txt` | wire `mctp_serial.c` under `CONFIG_MCTP_SERIAL` |
| `subsys/pmci/mctp/Kconfig` | add `MCTP_SERIAL` |
| `samples/subsys/pmci/pldm/serial_bridge/` | new — PLDM Type 0 endpoint (EID 18) over the serial link |

### Sample

`samples/subsys/pmci/pldm/serial_bridge/` — registers the PLDM Type 0
responder as EID 18 on `hifive_unleashed/fu540/u54` and answers requests from
the BMC (EID 8). Built with the python3.12 venv + Zephyr SDK 0.17.4:

```sh
west build -b hifive_unleashed/fu540/u54 -p always \
    samples/subsys/pmci/pldm/serial_bridge
```

Run on QEMU with a second `-serial unix:` UART:

```sh
qemu-system-riscv64 -machine sifive_u -smp 2 -m 256 -nographic -bios none \
    -kernel build/zephyr/zephyr.elf \
    -serial mon:stdio -serial unix:/tmp/hfm-mctp.sock
```

### Verification (end-to-end over a unix socket)

A host DSP0253/PLDM simulator plays OpenBMC's `mctpd` + `pldmtool` (opens the
socket as server, frames PLDM Type 0 requests to EID 18, verifies responses).
Against the deployed `prebuilts/zephyr.elf`:

```
GetTID         -> completion=0x00 tid=1
GetPLDMTypes   -> completion=0x00 data=0100000000000000 (Type 0 supported)
GetPLDMVersion -> completion=0x00 data=...00f0f1f1       (1.0.0)
RESULT: PASS
```

> **FCS note:** libmctp's `crc_16_ccitt` is the RFC1662 **reflected** FCS-16
> (poly `0x8408`), *not* the MSB-first CCITT/XMODEM variant (poly `0x1021`).
> Both the Zephyr binding and the OpenBMC `mctp-serial` driver use the RFC1662
> form; a peer that computes the MSB-first CRC will have every frame rejected.

### Live two-QEMU bridge

`scripts/launch_openbmc.sh` (socket **server**, start first) and
`scripts/launch_hfm.sh` (socket **client**) share `MCTP_SOCK`
(`/tmp/hfm-mctp.sock`). On each side the **second** `-serial` backend carries
the MCTP link (Zephyr uart1 / AST2600 UART1 → `/dev/ttyS0`); the first stays
the console. Once both are up, `pldmtool base GetTID` from the BMC targets
EID 18.

`scripts/two_qemu_smoke.py` automates the full end-to-end check: it boots both
QEMU instances, SSHes into the BMC, brings up `mctpserial0`, and runs real
`pldmtool` (GetTID / GetPLDMTypes / GetPLDMVersion). With the fixed kernel
(patch 0010) and the fixed unit ordering (patch 0006), mctpd discovers the
endpoint and installs the route itself; the smoke test then talks to EID 18
through OpenBMC's **kernel** AF_MCTP stack:

```
pldmtool base GetTID         -> {"Response": 1}
pldmtool base GetPLDMTypes   -> SUCCESS, PLDM Type base (0)
pldmtool base GetPLDMVersion -> SUCCESS, 1.1.0
```

> **History — `mctp route add` CLI busy-loop.** On the *original* prebuilt
> image the codeconstruct `mctp` CLI's `route`/`addr` dump paths spun forever
> in userspace (100% CPU). The root cause was a kernel AF_MCTP netlink-dump bug
> in 6.6.92, now fixed by patch 0010 (backport of upstream `cfa7fa02078d`).
> With that fix `mctp route`/`mctp addr` return immediately and mctpd's own
> discovery works, so the old raw-netlink route helper is no longer needed.

---

## Patch 0006 — OpenBMC evb-ast2600: order the serial link before mctpd

Rev-2 BMC-side delta on top of patch 0004, paired with the Zephyr MR2 control
responder (`CONFIG_MCTP_CONTROL`) and its tag-owner fix (patch 0011). Once the
Zephyr node answers the baseline MCTP control commands with TO=0 responses,
mctpd's `SetupEndpoint` can enumerate it end to end and install the route
itself — provided the serial link exists **before** mctpd starts.

The stock unit ordered itself `After=mctpd.service`, so mctpd came up before
`mctpserial0` existed. mctpd snapshots the kernel link map exactly once at
startup into its route-query netlink handle (`ctx->nl_query`) and never
refreshes it from the monitor socket, so a link that appears afterwards is
invisible to `peer_route_update()` — it fails with `BUG: Unknown ifindex` and
the route to the discovered endpoint is never installed (`pldmtool -m 18` then
returns `rc=-7`). This patch fixes the ordering:

- Split `mctp-local.service` into two units. `mctp-local.service` creates the
  serial link, brings `mctpserial0` up and assigns the local EID (8); it is
  ordered `Before=mctp-local.target`, and mctpd.service is already
  `After=mctp-local.target`, so the interface exists before mctpd enumerates.
- `mctp-setup-endpoint.service` (oneshot) runs the `SetupEndpoint` busctl call
  after mctpd is up, wrapped in a bounded 40 × 3 s retry loop since the
  endpoint (a separate Zephyr QEMU instance) may boot after the BMC.
- Drop the manual `mctp route add 18 via mctpserial0` line — mctpd installs the
  route itself on a successful `SetupEndpoint`.

### Files

| File | Change |
|---|---|
| `.../recipes-phosphor/mctp/files/mctp-local.service` | order `Before=mctp-local.target`; link/EID only |
| `.../recipes-phosphor/mctp/files/mctp-setup-endpoint.service` | new — oneshot SetupEndpoint retry loop, after mctpd |
| `.../recipes-phosphor/mctp/mctp_%.bbappend` | ship + enable the second unit |

### Verification (cold boot, zero manual steps)

With the fixed kernel image (patch 0010) and Zephyr firmware (patch 0011), a
cold boot of the two QEMU instances brings up auto-discovery with no manual
steps: `mctp-local` / `mctpd` / `mctp-setup-endpoint` all active, SetupEndpoint
succeeds once, mctpd auto-installs the kernel route `eid min 18 max 18 dev
mctpserial0` and the neighbour, `endpoints/18` is published on D-Bus, and
`pldmtool base GetTID`/`GetPLDMTypes` + `platform GetPDR`/`GetSensorReading`
over `-m 18` all answer `rc=0` (presentReading 31).

The paired Zephyr side is patches 0007 (Type 2 responder), 0008 (control
responder) and 0011 (control-response tag-owner fix) below; the kernel fix is
patch 0010.

---

## Patch 0007 — PLDM Type 2 (Platform Monitoring) responder + PDR (MR1)

Extends the PLDM subsystem with a DMTF DSP0248 Type 2 responder so a
management controller (OpenBMC's `pldmd` platform-mc) can discover a sensor
terminus and poll it.

- Serves a **compile-time PDR repository** — one Terminus Locator PDR plus one
  Numeric Sensor PDR per registered sensor — via `GetPDRRepositoryInfo` (0x50)
  and `GetPDR` (0x51).
- `GetSensorReading` (0x11) returns live values through each sensor's read
  callback; `GetStateSensorReadings` and `PlatformEventMessage` are handled
  minimally.
- `pldm_base_register_type()` lets the Type 2 responder announce its version
  and command bitmap through the Type 0 discovery commands, so `GetPLDMTypes` /
  `GetPLDMVersion` / `GetPLDMCommands` advertise Type 2.
- **ISR-safety fix:** the requester slot lock is taken from libmctp's RX path,
  which can run in interrupt context, so `pldm_ctx.req_lock` is switched from
  `k_mutex` to `k_spinlock` (a mutex must not be locked in an ISR).
- New Kconfig `CONFIG_PLDM_TYPE_PLATFORM` (selects `REQUIRES_STD_C11` for the
  libpldm platform encoders) and `CONFIG_PLDM_PLATFORM_MAX_SENSORS`.

### Files

| File | Change |
|---|---|
| `include/zephyr/pmci/pldm/pldm_platform.h` | new — Type 2 responder API + sensor descriptor |
| `subsys/pmci/pldm/pldm_platform.c` | new — DSP0248 responder (PDR repo, GetSensorReading) |
| `include/zephyr/pmci/pldm/pldm_base.h` | add `pldm_base_register_type()` |
| `subsys/pmci/pldm/pldm_base.c` | advertise extra types in discovery commands |
| `include/zephyr/pmci/pldm/pldm.h` | `req_lock` → `req_slock` (spinlock) |
| `subsys/pmci/pldm/pldm.c` | ISR-safe requester slot locking |
| `subsys/pmci/pldm/Kconfig` | add `PLDM_TYPE_PLATFORM`, `PLDM_PLATFORM_MAX_SENSORS` |
| `subsys/pmci/pldm/CMakeLists.txt` | wire `pldm_platform.c` under `CONFIG_PLDM_TYPE_PLATFORM` |

Build-verified in the `serial_bridge` sample (see patch 0008); validated over
the real BMC kernel AF_MCTP transport (`GetPDR` → Terminus Locator PDR EID 18;
`GetSensorReading -i 1` → presentReading 31). See design doc §10c.

---

## Patch 0008 — MCTP control responder (DSP0236) + serial_bridge Type 2 wiring (MR2)

Adds the baseline MCTP control-protocol responder a bus owner needs to
enumerate a statically-addressed endpoint, and wires the Type 2 sensor into the
`serial_bridge` sample so `pldmd` can discover and poll it end to end.

- libmctp only routes transport-specific control commands (`0xF0-0xFF`) to a
  binding; the baseline commands land on the generic message handler. The
  responder installs itself as the MCTP `rx_all` callback, answers Set/Get
  Endpoint ID (0x01/0x02), Get MCTP Version Support (0x04) and Get Message
  Type Support (0x05) — reporting message types `{0x00 control, 0x01 PLDM}` —
  and forwards every non-control message to the downstream PLDM dispatcher it
  was chained onto.
- Wire formats match `mctpd`'s strict validation (IID echo, exact response
  length). The node keeps its fixed EID 18, so Set Endpoint ID accepts only a
  matching or no-change (0) request.
- `serial_bridge` enables `CONFIG_MCTP_CONTROL` + `CONFIG_PLDM_TYPE_PLATFORM`,
  registers a simulated die-temperature Numeric Sensor (`sensor_id 1`), chains
  the control responder above the PLDM dispatcher, and adds a reverse-direction
  probe thread that queries the BMC (EID 8) to exercise the host→BMC path.
- New Kconfig `CONFIG_MCTP_CONTROL` and `CONFIG_MCTP_CONTROL_MAX_MSG_TYPES`.

### Files

| File | Change |
|---|---|
| `include/zephyr/pmci/mctp/mctp_control.h` | new — control responder API + context |
| `subsys/pmci/mctp/mctp_control.c` | new — DSP0236 baseline command handlers |
| `subsys/pmci/mctp/Kconfig` | add `MCTP_CONTROL`, `MCTP_CONTROL_MAX_MSG_TYPES` |
| `subsys/pmci/mctp/CMakeLists.txt` | wire `mctp_control.c` under `CONFIG_MCTP_CONTROL` |
| `samples/subsys/pmci/pldm/serial_bridge/prj.conf` | enable control + Type 2 |
| `samples/subsys/pmci/pldm/serial_bridge/src/main.c` | register sensor; chain responder; reverse probe |

With this, `mctpd` runs Get Endpoint ID / Get Message Type Support against the
node and publishes it on D-Bus for `pldmd` platform-mc to poll. The BMC-side
enablement is patch 0006. Note that this responder replies with the tag-owner
bit set (TO=1), which patch 0011 fixes; without that fix mctpd's discovery
query never matches the reply and `SetupEndpoint` times out.

---

## Patch 0009 — set the MCTP tag-owner bit on requester TX (reverse-path fix, MR3)

Fixes the reverse-direction PLDM path (Zephyr requester EID 18 → OpenBMC
responder EID 8). `pldm_send_request_sync()` transmitted requests with
`MCTP_MESSAGE_TO_DST` (tag-owner bit TO = 0). A PLDM requester owns the message
tag (DSP0236 §8.1), so requests must be sent with TO = 1.

- The BMC runs the Linux kernel AF_MCTP stack (not libmctp). Its input path
  (`net/mctp/route.c` `mctp_route_input`) only hands an inbound SOM frame to a
  bound listening socket when the frame has TO = 1 and no existing key matches
  (`if (!key && !msk && (tag & MCTP_HDR_FLAG_TO)) msk = mctp_lookup_bind(...)`).
  A TO = 0 frame is treated as an orphan response and dropped with `-ENOENT`
  before reaching the PLDM responder socket. The mctp-serial line discipline
  still counts it in `rx_packets` — exactly the symptom seen: `rx_packets` grew
  but the responder received nothing and `tx_packets` stayed 0.
- The forward path always worked because Zephyr's responder replies with
  `MCTP_MESSAGE_TO_SRC` (TO = 1), and the libmctp↔libmctp Type 0 loopback sample
  never exercises the kernel's TO gate, so the bug was invisible in unit tests.

### Files

| File | Change |
|---|---|
| `subsys/pmci/pldm/pldm.c` | `pldm_send_request_sync()`: `MCTP_MESSAGE_TO_DST` → `MCTP_MESSAGE_TO_SRC` |

Validated end to end on the two-QEMU bridge: Zephyr prints `BMC GetTID -> 0x08`,
`BMC GetPLDMTypes -> byte0=0x01`, `BMC GetPLDMVersion(BASE) -> 0.0.1`,
`Reverse-direction PLDM probe to BMC complete`; the BMC base responder receives
the inbound requests (`tag 0x08` == TO set) and answers each, and `mctpserial0`
shows matching `rx_packets=3 / tx_packets=3` (design doc §10c).

---

## Patch 0010 — OpenBMC evb-ast2600: fix the AF_MCTP netlink-dump busy-loop (kernel)

Backports the upstream fix for a kernel AF_MCTP netlink-dump busy-loop present
in the `evb-ast2600` kernel (6.6.92). The bug made `mctpd` (and the `mctp`
CLI's `route`/`addr` dumps) spin at 100% CPU forever, so automatic endpoint
discovery could never complete.

Root cause: `for_each_netdev_dump()` was implemented with
`xa_for_each_start()`, whose cursor is not advanced when the walk ends, so an
`RTM_GETADDR | NLM_F_DUMP` over AF_MCTP (`mctp_dump_addrinfo`) restarted from
the same index on every netlink recvmsg and never emitted `NLMSG_DONE`.
Upstream commit `cfa7fa02078d` (v6.6-stable backport of `f22b4b55edb5`, "net:
make for_each_netdev_dump() bug-proof", first in v6.6.95) reworks the macro to
`xa_find(...); ifindex++`, terminating the dump correctly. Our base SRCREV
`ca938df` (v6.6.92-421) carries the CVE dependency `2d45eeb7d5d7` but not this
macro fix, so we ship it as a layer patch.

### Files

| File | Change |
|---|---|
| `.../recipes-kernel/linux/linux-aspeed/0010-net-make-for_each_netdev_dump-bug-proof.patch` | new — backport `cfa7fa02078d` |
| `.../recipes-kernel/linux/linux-aspeed_%.bbappend` | `SRC_URI:append:df-mctp` mounts the patch |

The kernel patch carries an `Upstream-Status: Backport [...]` header (required
or `do_patch` fails the Yocto QA check). After applying, rebuild the kernel:

```sh
bitbake -c cleansstate linux-aspeed && bitbake obmc-phosphor-image
```

Verified on the rebuilt image: with the interface addressed, `mctp addr` /
`mctp route` dumps return `rc=0` immediately, mctpd's CPU delta over 2 s is ~0,
and the busy-loop is gone (`uname -r` = `6.6.92-ca938df-dirty-...`).

---

## Patch 0011 — clear the MCTP tag-owner bit on control responses (auto-discovery fix, MR4)

Fixes mctpd's automatic endpoint discovery (`SetupEndpoint`) against the Zephyr
serial_bridge node. The control-protocol responder replied to Get/Set Endpoint
ID with `MCTP_MESSAGE_TO_SRC` (tag-owner bit TO = 1). An MCTP *response* must
clear TO: the requester (the BMC bus owner, mctpd) owns the message tag
(DSP0236 §8.1), so the reply must reuse the requester-supplied tag with TO = 0.
This is the mirror image of patch 0009 (requests must set TO = 1).

- mctpd enumerates a static endpoint with physical addressing (null EID) over a
  dedicated query socket. The kernel AF_MCTP stack matches the reply to that
  socket by `(src, dest, tag)`, and the match key **includes** the TO bit
  (`net/mctp/route.c` `mctp_lookup_key`:
  `tag = flags_seq_tag & (MCTP_HDR_TAG_MASK | MCTP_HDR_FLAG_TO)`).
- A response with TO = 1 fails that match, so the kernel treats the frame as a
  fresh request and routes it to mctpd's bound daemon socket (`mctp_lookup_bind`)
  instead of the query socket. mctpd logs `Got control request command code 2`
  and the query socket never sees the answer, so `SetupEndpoint` times out.

### Files

| File | Change |
|---|---|
| `subsys/pmci/mctp/mctp_control.c` | `ctrl_send()`: `MCTP_MESSAGE_TO_SRC` → `MCTP_MESSAGE_TO_DST` |

With this fix (plus patch 0010 and patch 0006), a cold boot completes fully
automatic discovery: mctpd adopts EID 18, auto-installs the route and neighbour,
publishes `endpoints/18` on D-Bus, and `pldmtool -m 18` (base + platform) all
answer `rc=0`. mctpd logs `emit_endpoint_added .../endpoints/18` and `Adding
route to peer eid 18`.

---

## How to apply (Zephyr patches)

```sh
cd $ZEPHYR_BASE
git checkout v4.3.0
git am /path/to/Hardware-Fault-Management/patches/0001-subsys-pmci-mctp-add-MCTP-over-I3C-binding-DSP0233-v.patch
git am /path/to/Hardware-Fault-Management/patches/0002-subsys-pmci-add-PLDM-subsystem-and-DSP0240-Type-0-su.patch
git am /path/to/Hardware-Fault-Management/patches/0003-subsys-pmci-mctp-add-MCTP-over-SMBus-I2C-binding-DSP.patch
git am /path/to/Hardware-Fault-Management/patches/0005-subsys-pmci-mctp-add-MCTP-over-serial-binding-DSP025.patch
git am /path/to/Hardware-Fault-Management/patches/0007-subsys-pmci-pldm-add-DSP0248-Type-2-Platform-Monitor.patch
git am /path/to/Hardware-Fault-Management/patches/0008-subsys-pmci-mctp-add-DSP0236-control-responder-wire-.patch
git am /path/to/Hardware-Fault-Management/patches/0011-subsys-pmci-mctp-clear-tag-owner-bit-on-control-response.patch
# 0009 has no mbox header — apply it as a plain diff:
git apply /path/to/Hardware-Fault-Management/patches/0009-subsys-pmci-pldm-set-MCTP-tag-owner-bit-on-requester.patch
# (west.yml already updated by 0002; run `west update` to fetch libpldm)
west update libpldm
```

Or use the branches already prepared locally in your Zephyr workspace
(`$ZEPHYR_DIR/zephyr`) — tip `hfm/pldm-type2`, which
descends from `hfm/mctp-i2c-binding` → `hfm/pldm-type0` → `hfm/mctp-i3c-binding`.

## How to apply (OpenBMC patches)

```sh
cd /path/to/openbmc
git apply /path/to/Hardware-Fault-Management/patches/0004-openbmc-evb-ast2600-enable-mctp-pldm-serial.patch
git apply /path/to/Hardware-Fault-Management/patches/0006-openbmc-evb-ast2600-mctp-local-auto-discovery-retry.patch
git apply /path/to/Hardware-Fault-Management/patches/0010-openbmc-evb-ast2600-kernel-fix-mctp-netlink-dump-busy-loop.patch
source oe-init-build-env build && bitbake -c cleansstate linux-aspeed && bitbake obmc-phosphor-image
```

## Follow-ups (out of scope for this MR set)

- QEMU `mctp-i3c-target` / I2C target-mode device model (see
  `docs/pldm-mctp-i3c-design.md` §5) to enable two-QEMU interconnect.
- DTS overlay wiring a peer target to `i3c@4000000` / an I2C bus node.
- Sample apps `hfm_pldm_discover` / `hfm_pldm_sensors`.
