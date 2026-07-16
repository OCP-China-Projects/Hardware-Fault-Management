# HFM RAS API — Demo materials

Presenter kit for the end-to-end PLDM-over-MCTP demo: OpenBMC (AST2600) and
Zephyr (RISC-V) running as two live QEMU instances bridged over an MCTP-serial
link. Everything here is grounded in this repo (`README.md`,
`docs/pldm-mctp-i3c-design.md`) and was verified live on an internal dev
machine.

## Contents

| File | Purpose |
|---|---|
| `HFM_RASAPI_demo.pptx` | Slide deck (13 slides). Regenerate with `python3 build_deck.py`. |
| `build_deck.py` | Deck generator (`python-pptx`). Writes the `.pptx` next to itself. |
| `DEMO_GUIDE.md` | Step-by-step presenter walkthrough (three-terminal manual flow). |
| `drive_bmc_pldm.sh` | Terminal-3 driver: brings up the MCTP link, runs the forward `pldmtool` sequence, then starts the reverse-path responder over SSH. |

## Demo flow (three SSH terminals)

1. **Terminal 1** — `./launch_openbmc.sh` — boot OpenBMC (socket server, start first).
2. **Terminal 2** — `./launch_hfm.sh` — boot Zephyr (socket client); watch the boot + `-116` retries.
3. **Terminal 3** — `./drive_bmc_pldm.sh` — forward PLDM (`GetTID` / `GetPDR` / `GetSensorReading` …), then start the EID-8 responder.
4. Back on **Terminal 2**, watch `Reverse-direction PLDM probe to BMC complete` (reverse path, patch 0009).

Terminals 1 and 2 use `-serial mon:stdio`, so both QEMU boots are visible in
their own terminal. See `DEMO_GUIDE.md` for the full script, expected output,
and troubleshooting.

## Notes

- Host/account are shown as `<dev-host>` / `<user>` placeholders — substitute
  your own environment.
- `launch_*.sh` and the ARM helpers referenced by `drive_bmc_pldm.sh` live on
  the dev machine under `/tmp/hfm-verify/`; they are not committed here.
- Regenerating the deck requires `python-pptx` (`pip install python-pptx`).
