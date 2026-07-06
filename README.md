# OCP China Projects: Hardware Fault Management

## Description

The Hardware Fault Management China sub-project will align its objectives
with the global project, with the core goal of jointly addressing key pain
points in hardware fault management for large-scale data centers.

Specifically, this alignment manifests in three aspects:
Technical Objective Alignment Resource Coordination Targeted Problem Solving

## Requirements

The build was validated on Ubuntu 24.04. The dependency list below still
works on Ubuntu 22.04, but on 20.04 several packages have different names
(`libmagic1t64` -> `libmagic1`, `gcc-13-riscv64-linux-gnu` is not packaged,
etc.) so 22.04+ is recommended.

Root privileges are required for the OpenBMC build (bitbake needs to write
`/proc/self/uid_map` inside a user namespace during `do_unpack`). If your
environment mounts `/proc` read-only even for root (some container
sandboxes), see `patches/openbmc-bitbake-disable_network-erofs.patch`.

```shell
sudo apt update

# Install qemu-system-riscv64 and qemu-system-aarch64
sudo apt install -y qemu-system-riscv64 qemu-system-aarch64

# Install build dependencies and download tools
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

# Usage

## OpenBMC QEMU instance

Please use user name 'root' and password '0penBmc'
(its zero not captialized o) to login in OpenBMC and webUI

### Use prebuilt binaries

```shell
cd scripts
./launch_openbmc.sh
```
#### WebUI hyperlink
WebUI hyperlink for local development machine
```shell
https://127.0.0.1:1443
```

WebUI hyperlink for remote development machine
```shell
https://$TARGETIP:1443
```

### Build OpenBMC image from scratch

Requires ~50 GB of free disk and 3-8 hours depending on the machine and
network. Yocto downloads several GB of source archives on the first run.

```shell
git clone https://github.com/openbmc/openbmc.git openbmc

# Yocto's do_unpack uses user namespaces to disable network. On Ubuntu
# 24.04 with AppArmor these two knobs must be set once per boot:
sudo sh -c 'echo 0 > /proc/sys/kernel/apparmor_restrict_unprivileged_userns'
sudo sysctl -w kernel.unprivileged_userns_clone=1

cd openbmc
git checkout 2.18.0 -b 2.18.0
export TEMPLATECONF="meta-evb/meta-evb-aspeed/meta-evb-ast2600/conf"
. setup evb-ast2600
bitbake obmc-phosphor-image
```

If bitbake aborts with `OSError: [Errno 30] Read-only file system:
'/proc/self/uid_map'`, the sandbox blocks writing uid_map even for root.
Apply the patch under `patches/`:

```shell
cd openbmc
patch -p1 < ../patches/openbmc-bitbake-disable_network-erofs.patch
```

The OpenBMC MTD image will be generated at
```shell
$OPENBMC_CODE_BASE/build/evb-ast2600/tmp/deploy/images/evb-ast2600/obmc-phosphor-image-evb-ast2600-$BUILD_TIME.static.mtd
```

## Hardware Fault Management QEMU instance

Hardware Fault Management instance is based on zephyr porject, please use
following command to launch Hardware Fault Management QEMU instance

### Use prebuilt binaries

```shell
cd scripts
./launch_hfm.sh
```

### Build zephyr image from scratch

The prebuilt `prebuilts/zephyr.elf` is `v4.3.0-3029-g7cd0913f3120`. To
reproduce a Zephyr image that boots the same way, pin the tree to a
release tag (main requires Zephyr SDK >= 1.0, which the current SDK does
not provide) and pass the QEMU-friendly SRAM overlay to the build:

```shell
export ZEPHYR_DIR=zephyr_project

# Install west tool if not available
if ! command -v west >/dev/null 2>&1; then
    echo "Installing west tool..."
    pip3 install west --break-system-packages
fi

python3 -m venv $ZEPHYR_DIR/.venv
source $ZEPHYR_DIR/.venv/bin/activate

# Pin the manifest to a release tag; without --mr, west init pulls the
# main branch, which requires Zephyr SDK 1.0+.
west init --mr v4.3.0 $ZEPHYR_DIR
cd $ZEPHYR_DIR
west update

west zephyr-export

cd zephyr
west sdk install

# NOTE: hifive_unmatched's default DTS pins zephyr,sram to L2 LIM
# (0x08000000), which QEMU's sifive_u machine does not implement.
# The overlay redirects zephyr,sram to ram0 (DDR at 0x80000000) and
# shrinks ram0 to match `qemu -m 256`, otherwise the heap init trips
# a Store/AMO access fault at the top of the 16 GiB DDR region.
#
# QEMU's sifive_u_prci model also does not implement the PLL_LOCK
# bit, so soc_early_init_hook busy-loops forever waiting for it.
# Apply the timeout patch to keep the boot moving:
patch -p1 -d zephyr < ../../patches/zephyr-fu700-pll-lock-qemu-timeout.patch

west build -b hifive_unmatched/fu740/u74 -p always samples/hello_world/ \
    -- -DDTC_OVERLAY_FILE=$(pwd)/../../patches/zephyr-qemu-hifive.overlay

# `west build -t run` does not support hifive_unmatched (its board.cmake
# only lists renode as a supported emulator). Launch the produced ELF
# with the same QEMU command that scripts/launch_hfm.sh uses:
qemu-system-riscv64 \
    -machine sifive_u -smp 5 -nographic -m 256 -bios none \
    -kernel build/zephyr/zephyr.elf \
    -serial mon:stdio

deactivate
```

Expected output:

```
*** Booting Zephyr OS build v4.3.0 ***
Hello World! hifive_unmatched/fu740/u74
```

The Zephyr ELF will be generated at
```shell
 $ZEPHYR_DIR/zephyr/build/zephyr/zephyr.elf
```
