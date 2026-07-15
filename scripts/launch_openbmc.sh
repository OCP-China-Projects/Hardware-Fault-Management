#!/bin/bash

CUR_DIR=$(pwd)
WORK_DIR=$(dirname $(pwd))
PREBUILTS_DIR=${WORK_DIR}/prebuilts
QEMU_ARM_BIN=${QEMU_ARM_BIN:-$HOME/qemu-build/bin/qemu-system-arm}
export LD_LIBRARY_PATH=$HOME/local/lib/x86_64-linux-gnu:$HOME/local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
[ -x "${QEMU_ARM_BIN}" ] || QEMU_ARM_BIN=qemu-system-arm
OPENBMC_IMG=obmc-phosphor-image-evb-ast2600.mtd
SSH_PORT=3222
HTTP_PORT=1443
SNMP_PORT=1623

# Second UART (AST2600 UART1 -> /dev/ttyS0) is exported as a host unix socket
# that carries the MCTP-over-serial (DSP0253) link to the Zephyr instance.
# OpenBMC is the socket server; start it before launch_hfm.sh (the client).
MCTP_SOCK=${MCTP_SOCK:-/tmp/hfm-mctp.sock}

# Default settings for QEMU ARM instance
CPUS=2
MEMORY=512
MACHINE=ast2600-evb

if [ ! -d "${PREBUILTS_DIR}" ]; then
    echo "Prebuild directory not found"
    exit 1
fi

if [ ! -f "${PREBUILTS_DIR}/${OPENBMC_IMG}.gz" ]; then
    echo "openbmc image zip file not found"
    exit 1
fi

if [ -f "${PREBUILTS_DIR}/${OPENBMC_IMG}" ]; then
    echo "openbmc image found, clean up"
    rm ${PREBUILTS_DIR}/${OPENBMC_IMG}
fi

gunzip -k ${PREBUILTS_DIR}/${OPENBMC_IMG}.gz

# Clean up any stale socket from a previous run.
rm -f "${MCTP_SOCK}"

echo -e "\e[0;32mLaunching QEMU OpenBMC instance, press 'Ctrl + a' then 'x' to exit\e[0m"
echo -e "\e[0;32mMCTP-over-serial link server: ${MCTP_SOCK} (start launch_hfm.sh after this)\e[0m"
read -p "Press Enter to continue"

${QEMU_ARM_BIN} \
    -machine ${MACHINE} \
    -smp ${CPUS} \
    -nographic  \
    -m ${MEMORY} \
    -drive file=${PREBUILTS_DIR}/${OPENBMC_IMG},format=raw,if=mtd,id=hd0 \
    -net nic,model=ftgmac100,netdev=netdev1 \
    -netdev user,id=netdev1,hostfwd=::${SSH_PORT}-:22,hostfwd=::${HTTP_PORT}-:443,hostfwd=udp::${SNMP_PORT}-:623 \
    -chardev socket,id=mctp0,path=${MCTP_SOCK},server=on,wait=off \
    -serial mon:stdio \
    -serial chardev:mctp0

echo "Cleaning up"
rm ${PREBUILTS_DIR}/${OPENBMC_IMG}
rm -f "${MCTP_SOCK}"
