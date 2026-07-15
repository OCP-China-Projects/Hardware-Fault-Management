#!/bin/bash

CUR_DIR=$(pwd)
WORK_DIR=$(dirname $(pwd))
PREBUILTS_DIR=${WORK_DIR}/prebuilts
QEMU_RISCV_BIN=${QEMU_RISCV_BIN:-$HOME/qemu-build/bin/qemu-system-riscv64}
export LD_LIBRARY_PATH=$HOME/local/lib/x86_64-linux-gnu:$HOME/local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
[ -x "${QEMU_RISCV_BIN}" ] || QEMU_RISCV_BIN=qemu-system-riscv64
ZEPHYR_IMG=zephyr.elf

# Second UART (SiFive uart1) carries the MCTP-over-serial (DSP0253) link to the
# OpenBMC instance. Zephyr is the socket client; launch_openbmc.sh (the server)
# must be started first so the socket exists.
MCTP_SOCK=${MCTP_SOCK:-/tmp/hfm-mctp.sock}

CPUS=2
MEMORY=256
MACHINE=sifive_u

if [ ! -d "${PREBUILTS_DIR}" ]; then
    echo "Prebuild directory not found"
    exit 1
fi

if [ ! -f "${PREBUILTS_DIR}/${ZEPHYR_IMG}.gz" ]; then
    echo "zephyr image zip file not found"
    exit 1
fi

if [ -f "${PREBUILTS_DIR}/${ZEPHYR_IMG}" ]; then
    echo "zephyr image found, clean up"
    rm ${PREBUILTS_DIR}/${ZEPHYR_IMG}
fi

gunzip -k ${PREBUILTS_DIR}/${ZEPHYR_IMG}.gz

echo -e "\e[0;32mLaunching QEMU Hardware Fault Management instance, press 'Ctrl + a' then 'x' to exit\e[0m"
read -p "Press Enter to continue"

${QEMU_RISCV_BIN} \
    -machine ${MACHINE} \
    -smp ${CPUS} \
    -nographic \
    -m ${MEMORY} \
    -bios none \
    -kernel ${PREBUILTS_DIR}/${ZEPHYR_IMG} \
    -serial mon:stdio \
    -serial unix:${MCTP_SOCK}

echo "Cleaning up"
rm ${PREBUILTS_DIR}/${ZEPHYR_IMG}
