#!/bin/bash
#
# Wrapper for the locally-built QEMU 11.0.0.
#
# QEMU 11 requires glib >= 2.66, but the system glib on this host is 2.64.
# A newer glib (2.78) is installed under ~/local/, so we prepend that path
# to LD_LIBRARY_PATH before invoking the QEMU binary.
#
# Usage:
#   qemu11.sh riscv64 [args...]
#   qemu11.sh arm     [args...]
#   qemu11.sh img     [args...]   # qemu-img
#
set -e

QEMU_PREFIX=${QEMU_PREFIX:-$HOME/qemu-build}
GLIB_PREFIX=${GLIB_PREFIX:-$HOME/local}

if [ ! -d "${QEMU_PREFIX}/bin" ]; then
    echo "QEMU install not found at ${QEMU_PREFIX}" >&2
    exit 1
fi

export LD_LIBRARY_PATH=${GLIB_PREFIX}/lib/x86_64-linux-gnu:${GLIB_PREFIX}/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

target=$1
shift || true
case "$target" in
    riscv64|riscv32|arm|aarch64|x86_64|i386)
        exec "${QEMU_PREFIX}/bin/qemu-system-${target}" "$@"
        ;;
    img|nbd|io|ga|edid)
        exec "${QEMU_PREFIX}/bin/qemu-${target}" "$@"
        ;;
    ""|-h|--help)
        echo "Usage: $0 {riscv64|arm|aarch64|x86_64|img|nbd} [qemu-args...]"
        exit 0
        ;;
    *)
        echo "Unknown QEMU target: $target" >&2
        exit 1
        ;;
esac
