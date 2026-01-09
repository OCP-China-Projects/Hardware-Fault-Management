# OCP China Projects: Hardware Fault Management

## Description

The Hardware Fault Management China sub-project will align its objectives
with the global project, with the core goal of jointly addressing key pain
points in hardware fault management for large-scale data centers.

Specifically, this alignment manifests in three aspects:
Technical Objective Alignment Resource Coordination Targeted Problem Solving

## Requirements
Please use Ubuntu 24.04 to reproduce whole project, and install following
dependencies:

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
```shell
git clone https://github.com/openbmc/openbmc.git openbmc


sudo sh -c 'echo 0 > /proc/sys/kernel/apparmor_restrict_unprivileged_userns'
sudo sysctl -w kernel.unprivileged_userns_clone=1

cd openbmc
git checkout 2.18.0 -b 2.18.0
TEMPLATECONF="meta-evb/meta-evb-aspeed/meta-evb-ast2600/conf"
. setup evb-ast2600
bitbake obmc-phosphor-image
```

OpenBMC MTD image will be generated at
```shell
$OPENBMC_CODE_BASE/build/evb-ast2600/tmp/deploy/images/evb-ast2600/obmc-phosphor-image-evb-ast2600-$BUILD_TIME.static.mtd
```
