#!/bin/bash

CUR_DIR=$(pwd)
WORK_DIR=$(dirname $(pwd))
PREBUILTS_DIR=${WORK_DIR}/prebuilts
QEMU_ARM_BIN=qemu-system-arm
OPENBMC_IMG=obmc-phosphor-image-evb-ast2600.mtd
SSH_PORT=3222
HTTP_PORT=1443
SNMP_PORT=1623

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

echo -e "\e[0;32mLaunching QEMU OpenBMC instance, press 'Ctrl + a' then 'x' to exit\e[0m"
read -p "Press Enter to continue"

${QEMU_ARM_BIN} \
    -machine ${MACHINE} \
    -smp ${CPUS} \
    -nographic  \
    -m ${MEMORY} \
    -drive file=${PREBUILTS_DIR}/${OPENBMC_IMG},format=raw,if=mtd,id=hd0 \
    -net nic,model=ftgmac100,netdev=netdev1 \
    -netdev user,id=netdev1,hostfwd=::${SSH_PORT}-:22,hostfwd=::${HTTP_PORT}-:443,hostfwd=udp::${SNMP_PORT}-:623 \
    -serial mon:stdio

echo "Cleaning up"
rm ${PREBUILTS_DIR}/${OPENBMC_IMG}
