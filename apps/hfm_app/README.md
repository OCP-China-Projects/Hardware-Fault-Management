# HFM app skeleton

An empty Zephyr application scaffold for the Hardware Fault Management
firmware. Built with the parent project's `west` workspace at
`~/zephyrproject`.

## Layout

```
apps/hfm_app/
├── CMakeLists.txt   Zephyr build integration (target_sources on `app`)
├── prj.conf         Kconfig knobs for this build
├── app.overlay      Devicetree overlay (I3C etc. added here later)
├── src/main.c       Application entry point (`main()`)
└── README.md        You are here
```

## Build (RISC-V, HiFive Unmatched — matches the prebuilt Zephyr image)

The default hifive_unmatched device tree links Zephyr into L2 LIM
(0x08000000), which QEMU's `sifive_u` machine does not model. Pass the
project's SRAM overlay to redirect `zephyr,sram` to DDR (0x80000000):

```bash
source ~/zephyr-venv/bin/activate
export ZEPHYR_BASE=~/zephyrproject/zephyr
cd ~/workspace/Hardware-Fault-Management
west build -b hifive_unmatched/fu740/u74 -p always apps/hfm_app \
    -- -DDTC_OVERLAY_FILE=$(pwd)/patches/zephyr-qemu-hifive.overlay
```

Output: `build/zephyr/zephyr.elf`

## Run in QEMU 11

```bash
~/qemu-build/bin/qemu-system-riscv64 \
    -machine sifive_u -smp 2 -nographic -m 256 -bios none \
    -kernel build/zephyr/zephyr.elf
```

To swap this build into the launcher, gzip it and drop it in
`prebuilts/zephyr.elf.gz`, or point `launch_hfm.sh` at the fresh ELF.
