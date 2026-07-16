# OCP China Projects: Hardware Fault Management

## Description

The Hardware Fault Management China sub-project aligns with the global
project's core goal of jointly addressing key pain points in hardware
fault management for large-scale data centers. Alignment covers three
areas: technical objectives, resource coordination, and targeted problem
solving.

The tree contains four moving pieces that we build and validate
together:

1. **QEMU 11.x** built from source (needed for I3C support and modern
   AST2600/RISC-V virt features).
2. **Zephyr on RISC-V** — booting the Zephyr `qemu_riscv64` (aka
   `virt`) target with the QEMU we just built.
3. **I3C on QEMU virt + Zephyr** — DesignWare I3C controller support
   plumbed all the way from the QEMU device model to the Zephyr driver.
4. **MCTP + PLDM (the HFM stack)** — DMTF PMCI transport bindings
   (I3C / I2C / serial) and PLDM Type 0 (discovery) + Type 2 (platform
   monitoring) on both Zephyr and OpenBMC, bridged between two live QEMU
   instances over an MCTP-serial link. This is where Hardware Fault
   Management telemetry actually flows; see [Part 4](#part-4--mctp--pldm-the-hardware-fault-management-stack).

An OpenBMC AST2600 recipe is also documented at the end for reference.

Root privileges are required for the OpenBMC build (bitbake needs to
write `/proc/self/uid_map` inside a user namespace during `do_unpack`).
If your environment mounts `/proc` read-only even for root (some
container sandboxes), see
`patches/openbmc-bitbake-disable_network-erofs.patch`.

The build was validated on Ubuntu 24.04. The dependency list below
still works on Ubuntu 22.04, but on 20.04 several packages have
different names (`libmagic1t64` -> `libmagic1`, `gcc-13-riscv64-linux-gnu`
is not packaged, etc.) so 22.04+ is recommended.

```shell
sudo apt update
sudo apt install --no-install-recommends -y python3 python3-pip \
     python3-setuptools python3-wheel python3-pykwalify python3-venv \
     cmake ninja-build gperf ccache device-tree-compiler libsdl2-dev \
     libmagic1t64 dfu-util python3-tk xz-utils file make gcc \
     patool git build-essential libsdl1.2-dev \
     chrpath diffstat locales cpio python3-dev \
     python3-pexpect debianutils iputils-ping python3-git \
     python3-jinja2 python3-subunit gcc-13-riscv64-linux-gnu \
     mesa-common-dev zstd liblz4-tool libncurses5-dev flex \
     gcc-riscv64-linux-gnu binutils-riscv64-linux-gnu wget \
     bison texinfo gawk
pip install patool semver tqdm pyelftools --break-system-packages
```

---

## Upstream sources & versions

This repo does **not** vendor full copies of the three upstream trees.
Each is pinned to a public release below; every change this project
makes to them ships as a patch under
[patches/](patches)
(see [patches/README.md](patches/README.md)
for the full index and apply order). Clone the upstream at the pinned
ref, apply the patches, and you reproduce the exact tree used here.

| Tree | Pinned version | Get it | Local changes (patches) |
|---|---|---|---|
| QEMU | 11.0.0 (release tarball) | `https://download.qemu.org/qemu-11.0.0.tar.xz` | `qemu-riscv-virt-allow-dw-i3c.patch` |
| Zephyr | tag `v4.3.0` | `west init --mr v4.3.0` → `https://github.com/zephyrproject-rtos/zephyr` | `0001`,`0002`,`0003`,`0005`,`0007`,`0008` |
| libpldm (Zephyr module) | SHA `df0a2219` | `https://github.com/openbmc/libpldm` (pinned in `west.yml` by patch 0002) | — (vendored via west manifest) |
| OpenBMC | tag `2.18.0` | `https://github.com/openbmc/openbmc` | `0004`,`0006`, `openbmc-bitbake-disable_network-erofs.patch` |

The Zephyr SDK used is 0.17.4 (RISC-V toolchain only). Because the deltas
are small and the bases are public, pinning + patches is preferred over
committing multi-GB source trees.

---

## Part 1 — QEMU: version and build

### Why we build QEMU from source

| Source                       | Version          | I3C support | AST2600 completeness |
|------------------------------|------------------|-------------|----------------------|
| Ubuntu 20.04 `qemu-system-*` | 4.2.1            | no          | partial              |
| Ubuntu 22.04 `qemu-system-*` | 6.2              | no          | partial              |
| Ubuntu 24.04 `qemu-system-*` | 8.2              | Aspeed only | good                 |
| Upstream release (2026-07)   | **11.0.2**       | **full**    | full                 |
| This tree                    | **11.0.0** local | full + patch| full                 |

Since QEMU switched to a date-based `major.minor.micro` scheme starting
with 3.0, "11.0.x" is simply the current stable series (2025-Q4 →
2026-Q2). The Hardware Fault Management flow needs I3C, dynamic sysbus
devices on `virt`, and the AST2600 SoC additions that only landed
upstream — so we build 11.x locally instead of relying on distro
packages.

### Build QEMU 11 from source

Download and extract the pinned release tarball from
`https://download.qemu.org/` (this project uses 11.0.0):

QEMU 11 depends on **glib >= 2.66** (uses `g_uri_parse_params`). Ubuntu
20.04 ships glib 2.64, so we install a newer glib into `$HOME/local`
first and point QEMU at it via `LD_LIBRARY_PATH`.

```shell
# 0. Fetch and unpack QEMU 11.0.0
QEMU_SRC=$HOME/qemu-11-src
wget https://download.qemu.org/qemu-11.0.0.tar.xz
mkdir -p "$QEMU_SRC"
tar xf qemu-11.0.0.tar.xz -C "$QEMU_SRC" --strip-components=1

# 1. Build glib >= 2.66 into $HOME/local (only needed on Ubuntu 20.04)
GLIB_VER=2.78.6
wget https://download.gnome.org/sources/glib/2.78/glib-${GLIB_VER}.tar.xz
tar xf glib-${GLIB_VER}.tar.xz && cd glib-${GLIB_VER}
meson setup _build --prefix=$HOME/local -Dtests=false
ninja -C _build install
cd ..

# 2. Configure and build QEMU 11 for the targets we need.
#    QEMU ships its own ./configure wrapper (it drives meson internally),
#    so use that rather than calling `meson setup` directly — meson does
#    not understand QEMU's --target-list flag.
cd "$QEMU_SRC"

PKG_CONFIG_PATH=$HOME/local/lib/x86_64-linux-gnu/pkgconfig \
LD_LIBRARY_PATH=$HOME/local/lib/x86_64-linux-gnu \
./configure \
    --prefix=$HOME/qemu-build \
    --target-list=riscv64-softmmu,arm-softmmu,aarch64-softmmu \
    --disable-docs --disable-gtk --disable-sdl

make -j"$(nproc)"
make install
```

Result: `$HOME/qemu-build/bin/qemu-system-{riscv64,arm,aarch64}` (each
about 85 MB).

### The `qemu11.sh` wrapper

Because the local glib lives outside the loader path, direct invocation
of the freshly built binary aborts with
`undefined symbol: g_uri_parse_params`. Use the wrapper at
[scripts/qemu11.sh](scripts/qemu11.sh)
instead of calling `qemu-system-*` directly:

```shell
scripts/qemu11.sh riscv64 -machine virt -nographic ...
scripts/qemu11.sh arm     -machine ast2600-evb ...
```

The wrapper sets `LD_LIBRARY_PATH=$HOME/local/lib/x86_64-linux-gnu`
before `exec`ing the requested `qemu-system-<target>` so any script or
manual command line stays portable.

The `launch_hfm.sh` and `launch_openbmc.sh` scripts under `scripts/`
already prefer `$HOME/qemu-build/bin` and fall back to the distro
binary if the local install is missing.

---

## Part 2 — RISC-V Zephyr on QEMU virt

### Board choice: `qemu_riscv64` (a.k.a. QEMU `virt`)

Zephyr ships a first-class board named `qemu_riscv64` that targets
QEMU's `-machine virt` RISC-V board (the SoC-agnostic reference
platform). It is far friendlier than `hifive_unmatched` for emulation:

- `virt` is upstream QEMU's own board, so DTS/PLIC/CLINT/UART all match.
- No PLL_LOCK busy loops, no L2 LIM SRAM at fictitious addresses.
- Supports platform bus + dynamic sysbus devices — exactly what we
  need to plug an I3C controller in later (Part 3).

Board metadata:
`boards/qemu/riscv64/qemu_riscv64.yaml`.

### Build Zephyr for `qemu_riscv64` and boot it

> **Toolchain prerequisites (Ubuntu 20.04).** Zephyr v4.3 requires
> **Python >= 3.10** and **CMake >= 3.20**. Ubuntu 20.04 ships Python 3.8
> and CMake 3.16, so build the venv with a newer interpreter and install
> a recent CMake before `west build`:
>
> ```shell
> # Use a Python >= 3.10 interpreter for the venv (build your own or use
> # a distro backport such as the deadsnakes PPA).
> python3.12 -m venv $HOME/zephyrproject/.venv
> source $HOME/zephyrproject/.venv/bin/activate
> pip install --upgrade pip
> pip install cmake        # pulls CMake >= 3.20 into the venv
> cmake --version          # confirm >= 3.20
> ```
>
> Ubuntu 22.04+ already ships Python 3.10+/CMake 3.22+, so this step is
> only needed on 20.04.

```shell
export ZEPHYR_DIR=$HOME/zephyrproject

# west + venv (skip if already provisioned).
# NOTE: create the venv with Python >= 3.10 (see prerequisite box above).
python3 -m venv $ZEPHYR_DIR/.venv
source $ZEPHYR_DIR/.venv/bin/activate
pip install west --break-system-packages || true

west init --mr v4.3.0 $ZEPHYR_DIR
cd $ZEPHYR_DIR
west update
west zephyr-export
cd zephyr
# Install the Python build dependencies Zephyr needs at configure time.
pip install -r scripts/requirements.txt
west sdk install

# Build the hello_world sample for qemu_riscv64
west build -d build-qriscv -b qemu_riscv64 -p always samples/hello_world
```

Boot the resulting ELF with the QEMU 11 wrapper (unlike
`hifive_unmatched`, `west build -t run` also works because
`qemu_riscv64` lists qemu as its runner):

```shell
$HOME/workspace/Hardware-Fault-Management/scripts/qemu11.sh riscv64 \
    -machine virt -smp 1 -nographic -m 256 -bios none \
    -kernel build-qriscv/zephyr/zephyr.elf \
    -serial mon:stdio
```

Expected output:

```
*** Booting Zephyr OS build v4.3.0 ***
Hello World! qemu_riscv64/qemu_virt_riscv64
```

### Legacy: hifive_unmatched (`sifive_u` machine)

The prebuilt HFM ELF `prebuilts/zephyr.elf` is the **serial_bridge**
sample built for `hifive_unleashed/fu540/u54` (it boots under QEMU's
`sifive_u` machine and carries the PLDM Type 0 + Type 2 responders,
EID 18). Two patches are required to boot it under QEMU 11
because `sifive_u` is a partial FU540/FU740 model:

- [patches/zephyr-fu700-pll-lock-qemu-timeout.patch](patches/zephyr-fu700-pll-lock-qemu-timeout.patch)
  — bounds `soc_early_init_hook`'s wait for PLL_LOCK (QEMU's
  `sifive_u_prci` never asserts it).
- [patches/zephyr-qemu-hifive.overlay](patches/zephyr-qemu-hifive.overlay)
  — redirects `zephyr,sram` to DDR and shrinks `ram0` so heap init
  does not fault above `-m 256`.

```shell
patch -p1 -d zephyr < ../../patches/zephyr-fu700-pll-lock-qemu-timeout.patch
west build -b hifive_unmatched/fu740/u74 -p always samples/hello_world/ \
    -- -DDTC_OVERLAY_FILE=$(pwd)/../../patches/zephyr-qemu-hifive.overlay

scripts/qemu11.sh riscv64 \
    -machine sifive_u -smp 5 -nographic -m 256 -bios none \
    -kernel build/zephyr/zephyr.elf -serial mon:stdio
```

**New work should target `qemu_riscv64`** unless FU740-specific
peripherals are required.

---

## Part 3 — I3C: source, patches, and how to build

### Where the source comes from

**QEMU side** — DesignWare I3C controller model, in-tree since QEMU
11.x under `hw/i3c/`:

| File                                                                                                                    | Role                                            |
|-------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------|
| `hw/i3c/core.c`                                               | I3C bus core (arbitration, CCC, IBI plumbing)   |
| `hw/i3c/dw-i3c.c`                                           | Synopsys DesignWare I3C master (`TYPE_DW_I3C`)  |
| `hw/i3c/aspeed_i3c.c`                                   | Aspeed AST2600 wrapper around the DW core       |
| `hw/i3c/mock-i3c-target.c`                         | Simple loopback target for testing              |
| `include/hw/i3c/dw-i3c.h`                           | `TYPE_DW_I3C = "dw.i3c"` and register map       |

**Zephyr side** — DesignWare I3C driver, upstream since v3.6:

| File                                                                                                                              | Role                                          |
|-----------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------|
| `drivers/i3c/i3c_dw.c`                                  | Main DW I3C driver (matches the QEMU model)   |
| `dts/bindings/i3c/snps,designware-i3c.yaml` | Devicetree binding                    |
| `tests/drivers/build_all/i3c/boards/qemu_cortex_m3.overlay` | Reference overlay (borrow the node shape)     |

### QEMU virt: allow-list DW I3C on the platform bus

`virt` refuses to instantiate arbitrary `-device` types on its platform
bus — each device type has to be added to `allowed_dynamic_sysbus_dev`.
The two required edits are captured in
[patches/qemu-riscv-virt-allow-dw-i3c.patch](patches/qemu-riscv-virt-allow-dw-i3c.patch)
(apply from the QEMU source top with `patch -p1` or `git apply -p1`):

1. **`hw/riscv/virt.c`** — include the header
   and register the type in the machine class init:

   ```c
   #include "hw/i3c/dw-i3c.h"
   /* ... inside virt_machine_class_init() ... */
   machine_class_allow_dynamic_sysbus_dev(mc, TYPE_DW_I3C);
   ```

2. **`hw/riscv/Kconfig`** — pull the I3C
   modules into the `RISCV_VIRT` config so they get compiled and
   linked into `qemu-system-riscv64`:

   ```kconfig
   config RISCV_VIRT
       ...
       select I3C
       select DW_I3C
   ```

Rebuild QEMU (from the QEMU source top; `make` re-runs meson as needed):

```shell
cd "$QEMU_SRC"
PKG_CONFIG_PATH=$HOME/local/lib/x86_64-linux-gnu/pkgconfig \
LD_LIBRARY_PATH=$HOME/local/lib/x86_64-linux-gnu \
make -j"$(nproc)"
make install
```

Verify the device is registered:

```shell
scripts/qemu11.sh riscv64 -machine virt -device 'dw.i3c,help'
```

Expected: nine properties (`dev-addr-table-depth`, `dev-char-table-depth`,
`fifo-depth`, ...) instead of "Device 'dw.i3c' not found".

### Zephyr: overlay to expose the DW I3C node

Add a devicetree overlay at
`boards/qemu_riscv64.overlay` (or pass it via `-DDTC_OVERLAY_FILE=`)
using the platform-bus MMIO window (`0x04000000`, size `0x02000000` —
see `virt_memmap[VIRT_PLATFORM_BUS]` in
`virt.c`):

```dts
/ {
    soc {
        i3c0: i3c@4000000 {
            compatible = "snps,designware-i3c";
            reg = <0x04000000 0x1000>;
            interrupt-parent = <&plic>;
            interrupts = <20 1>;
            #address-cells = <3>;
            #size-cells = <0>;
            status = "okay";
        };
    };
};
```

Enable the driver in `prj.conf`:

```
CONFIG_I3C=y
CONFIG_I3C_DW=y
CONFIG_I3C_SHELL=y
```

Rebuild Zephyr and boot with the DW I3C device attached to the
platform bus:

```shell
west build -d build-qriscv-i3c -b qemu_riscv64 -p always samples/hello_world \
    -- -DDTC_OVERLAY_FILE=$PWD/qemu_riscv64.overlay

scripts/qemu11.sh riscv64 \
    -machine virt -smp 1 -nographic -m 256 -bios none \
    -kernel build-qriscv-i3c/zephyr/zephyr.elf \
    -device driver=dw.i3c,addr=0x04000000 \
    -serial mon:stdio
```

The Zephyr shell's `i3c` command tree can now enumerate and issue CCCs
against the mock target.

---

## Part 4 — MCTP + PLDM: the Hardware Fault Management stack

Parts 1–3 stand up the emulation substrate. Part 4 is the payload: a
DMTF PMCI stack (MCTP transport + PLDM application) that lets an OpenBMC
management controller discover and poll a Zephyr endpoint the way a real
BMC polls a satellite MCU. All of it is delivered as a numbered set of
patches under [patches/](patches);
the full per-patch index (files touched, Kconfig, apply order) lives in
[patches/README.md](patches/README.md),
and the protocol design in
[docs/pldm-mctp-i3c-design.md](docs/pldm-mctp-i3c-design.md).

### What the stack contains

| Layer | Spec | Where | Patch |
|---|---|---|---|
| MCTP over I3C | DSP0233 | Zephyr `subsys/pmci/mctp` | 0001 |
| MCTP over SMBus/I2C | DSP0237 | Zephyr `subsys/pmci/mctp` | 0003 |
| MCTP over serial | DSP0253 | Zephyr `subsys/pmci/mctp` | 0005 |
| MCTP control (Set/Get EID, msg-type) | DSP0236 | Zephyr `subsys/pmci/mctp` | 0008 |
| PLDM subsystem + Type 0 (discovery) | DSP0240 | Zephyr `subsys/pmci/pldm` | 0002 |
| PLDM Type 2 (platform monitoring) + PDR | DSP0248 | Zephyr `subsys/pmci/pldm` | 0007 |
| OpenBMC MCTP + PLDM over serial (evb-ast2600) | — | Yocto layers | 0004 |
| OpenBMC mctpd auto-discovery delta | — | Yocto layers | 0006 |

The Zephyr side vendors [openbmc/libpldm](https://github.com/openbmc/libpldm)
as a west module for wire encode/decode and layers on top of Zephyr's
existing libmctp. The OpenBMC side flips on `mctp` + `pldm`
`DISTRO_FEATURES` for the `evb-ast2600` machine and runs the stock
`mctpd` (kernel AF_MCTP) + `pldmd`.

### The realized topology — two QEMU instances over MCTP-serial

Upstream QEMU ships no I3C/I2C *target-mode* model, so the live
inter-QEMU link is **MCTP-over-serial (DSP0253)** across a `-serial
unix:` socket between the two QEMU processes (I3C/I2C bindings are still
built and loopback-tested, just not wired between two QEMUs):

```
  Zephyr QEMU (sifive_u)                    OpenBMC QEMU (ast2600-evb)
  +------------------------------+          +---------------------------+
  | serial_bridge sample         |          | pldmd (PLDM Type 0/2)     |
  |   PLDM Type 0 + Type 2 resp   |          | mctpd (AF_MCTP, EID 8)    |
  |   MCTP control responder      |          | kernel mctp-serial        |
  |   mctp_serial (DSP0253)       |          |                           |
  |  uart1 <------ unix socket ------------> UART1 = /dev/ttyS0          |
  +------------------------------+          +---------------------------+
        EID 18 (endpoint)                     console stays on UART5/ttyS4
```

### Run the bridge (prebuilt binaries)

`scripts/launch_openbmc.sh` is the socket **server** (start it first);
`scripts/launch_hfm.sh` is the **client**. Both share `MCTP_SOCK`
(default `/tmp/hfm-mctp.sock`) and put the MCTP link on each side's
**second** `-serial` backend, leaving the first as the console:

```shell
cd scripts
MCTP_SOCK=/tmp/hfm-mctp.sock ./launch_openbmc.sh    # terminal 1 (server)
MCTP_SOCK=/tmp/hfm-mctp.sock ./launch_hfm.sh        # terminal 2 (client)
```

Once both are up, `mctpd` on the BMC discovers the Zephyr endpoint by
itself (SetupEndpoint at boot) and installs the route to EID 18, so from
the BMC console you can talk PLDM to EID 18 directly:

```shell
mctp route          # eid min 18 max 18 dev mctpserial0 (auto-installed)
pldmtool base GetTID -m 18
pldmtool base GetPLDMTypes -m 18
pldmtool platform GetPDR -m 18 -d 0
pldmtool platform GetSensorReading -m 18 -i 1 --rearm 0
```

`scripts/two_qemu_smoke.py` automates this end to end (boots both
instances, waits for auto-discovery, runs `pldmtool`).

### Build the Zephyr endpoint from source

Apply the Zephyr patches (see
[patches/README.md](patches/README.md)
for the full apply order — most use `git am`, patch 0009 uses `git
apply`) and build the bridge sample for the
`sifive_u`-compatible board:

```shell
cd $ZEPHYR_DIR/zephyr
west build -b hifive_unleashed/fu540/u54 -p always \
    samples/subsys/pmci/pldm/serial_bridge

scripts/qemu11.sh riscv64 -machine sifive_u -smp 2 -m 256 -nographic \
    -bios none -kernel build/zephyr/zephyr.elf \
    -serial mon:stdio -serial unix:/tmp/hfm-mctp.sock
```

### mctpd auto-discovery (fully automatic)

On a cold boot `mctpd` discovers the Zephyr endpoint by itself and
installs the kernel route — no manual `mctp route add` and no raw-netlink
helper. Getting there took three fixes, all shipped in this repo:

- **Kernel (patch 0010).** The `evb-ast2600` kernel `6.6.92` had an
  AF_MCTP netlink-dump busy-loop: `for_each_netdev_dump()` used
  `xa_for_each_start()`, whose cursor is not advanced at the end of the
  walk, so an `RTM_GETADDR | NLM_F_DUMP` (`mctp_dump_addrinfo`) never
  emitted `NLMSG_DONE` and `mctpd` spun at 100% CPU. Backporting upstream
  `cfa7fa02078d` ("net: make `for_each_netdev_dump()` bug-proof") fixes it.
- **Zephyr control responder (patch 0011).** The endpoint replied to
  Get/Set Endpoint ID with the MCTP tag-owner (TO) bit set. A response
  must clear TO; with TO=1 the BMC kernel treated the reply as a fresh
  request and mctpd's discovery query timed out.
- **BMC unit ordering (patch 0006).** `mctpd` snapshots the kernel link
  map once at startup, so the serial link must exist *before* it starts.
  `mctp-local.service` now brings the link up ordered before mctpd, and a
  separate `mctp-setup-endpoint.service` runs `SetupEndpoint` after it.

Verified on a cold boot of the two QEMU instances (zero manual steps):
`SetupEndpoint` succeeds, `mctp route` shows `eid min 18 max 18 dev
mctpserial0`, `endpoints/18` is published on D-Bus, and `pldmtool -m 18`
`base GetTID`/`GetPLDMTypes` + `platform GetPDR`/`GetSensorReading`
(presentReading 31) all answer over the **real** kernel AF_MCTP transport.
Full evidence is in
[docs/pldm-mctp-i3c-design.md](docs/pldm-mctp-i3c-design.md)
§10c and the patch 0006/0010/0011 notes.

---

## Appendix — OpenBMC AST2600 QEMU instance

Login: user `root` / password `0penBmc` (leading zero, lowercase o).

### Use prebuilt binaries

```shell
cd scripts
./launch_openbmc.sh
```

WebUI (local):
```
https://127.0.0.1:1443
```

WebUI (remote):
```
https://$TARGETIP:1443
```

### Build OpenBMC image from scratch

Requires ~50 GB free disk and 3–8 hours. Yocto downloads several GB of
source archives on the first run.

> **Important — apply the HFM patches.** The stock `obmc-phosphor-image`
> for `evb-ast2600` ships **no** mctp/pldm binaries. You must apply
> patches **0004** (enable MCTP + PLDM over serial), **0006** (order the
> serial link before mctpd) and **0010** (kernel AF_MCTP dump fix) from
> `patches/` *before* `bitbake`, or the resulting image cannot run Part 4
> (no `mctpd`, `pldmd`, `mctp`, or `pldmtool`) or cannot auto-discover the
> endpoint. The prebuilt shipped in
> `prebuilts/obmc-phosphor-image-evb-ast2600.mtd.gz` is already patched
> — rebuild from scratch only if you need to change the stack.

```shell
git clone https://github.com/openbmc/openbmc.git openbmc

# Yocto's do_unpack uses user namespaces to disable network. On Ubuntu
# 24.04 with AppArmor these two knobs must be set once per boot:
sudo sh -c 'echo 0 > /proc/sys/kernel/apparmor_restrict_unprivileged_userns'
sudo sysctl -w kernel.unprivileged_userns_clone=1

cd openbmc
git checkout 2.18.0 -b 2.18.0

# Apply the HFM Yocto-layer patches (plain diffs — use git apply, not git am).
git apply ../patches/0004-openbmc-evb-ast2600-enable-mctp-pldm-serial.patch
git apply ../patches/0006-openbmc-evb-ast2600-mctp-local-auto-discovery-retry.patch
git apply ../patches/0010-openbmc-evb-ast2600-kernel-fix-mctp-netlink-dump-busy-loop.patch

# bitbake 2.12 requires Python 3.9+. Ubuntu 20.04 ships 3.8 as
# `python3`, so add a shim that points `python3` at 3.9 for this shell:
sudo apt install -y python3.9
mkdir -p /tmp/bb-pyshim
ln -sf /usr/bin/python3.9 /tmp/bb-pyshim/python3
export PATH=/tmp/bb-pyshim:$PATH

export TEMPLATECONF="meta-evb/meta-evb-aspeed/meta-evb-ast2600/conf/templates/default"
source ./poky/oe-init-build-env build
# Patch 0010 touches the kernel; force a clean kernel rebuild so the patch is
# picked up (only needed if you rebuild an existing tree — harmless on a fresh
# checkout).
bitbake -c cleansstate linux-aspeed
bitbake obmc-phosphor-image
```

If bitbake aborts with `OSError: [Errno 30] Read-only file system:
'/proc/self/uid_map'`, the sandbox blocks writing uid_map even for
root. Apply the patch under `patches/`:

```shell
cd openbmc
patch -p1 < ../patches/openbmc-bitbake-disable_network-erofs.patch
```

The MTD image lands at:

```
$OPENBMC_CODE_BASE/build/tmp/deploy/images/evb-ast2600/obmc-phosphor-image-evb-ast2600-$BUILD_TIME.static.mtd
```

To use it as the prebuilt for `launch_openbmc.sh` / `two_qemu_smoke.py`,
gzip the `.static.mtd` into `prebuilts/` under the expected name:

```shell
gzip -c -9 \
  build/tmp/deploy/images/evb-ast2600/obmc-phosphor-image-evb-ast2600.static.mtd \
  > prebuilts/obmc-phosphor-image-evb-ast2600.mtd.gz
```

`launch_openbmc.sh` gunzips it on startup; the smoke scripts decompress
to a writable scratch copy automatically.
