#!/bin/bash
# Terminal 3 — drive PLDM on the OpenBMC side over SSH (clean output).
#
# Terminals 1 & 2 (launch_openbmc.sh / launch_hfm.sh) show the two QEMU boots.
# This script SSHes into the already-running BMC (hostfwd 3222->22), streams
# the two ARM helpers, brings up the MCTP serial link, installs the EID-18
# route with the raw-netlink helper (the stock `mctp route add` CLI busy-loops
# on this image), runs pldmtool over EID 18 (FORWARD path), then starts the
# base responder so the Zephyr node's REVERSE probe can be answered.
#
# Run it AFTER both QEMUs are up and the BMC shows its login banner.
set -u

SSH_PORT=3222
PASS=0penBmc
HELPER_ROUTE=/tmp/mctp_route_add
HELPER_RESP=/tmp/pldm_base_responder

SSH_OPTS=(-p "$SSH_PORT"
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
  -o GlobalKnownHostsFile=/dev/null -o PubkeyAuthentication=no
  -o PreferredAuthentications=password,keyboard-interactive
  -o ConnectTimeout=10
  -o HostKeyAlgorithms=+ssh-rsa,ssh-dss
  -o KexAlgorithms=+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1)

say(){ echo -e "\e[0;36m[drive]\e[0m $*"; }

say "waiting for BMC SSH on port $SSH_PORT ..."
for i in $(seq 1 60); do
  if sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" root@127.0.0.1 'echo READY' 2>/dev/null | grep -q READY; then
    say "BMC SSH is up"; break
  fi
  sleep 3
done

say "streaming route helper -> BMC:/tmp/mctp_route_add"
sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" root@127.0.0.1 \
  'cat > /tmp/mctp_route_add && chmod +x /tmp/mctp_route_add && echo XFER_OK' < "$HELPER_ROUTE"
say "streaming base responder -> BMC:/tmp/pldm_base_responder"
sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" root@127.0.0.1 \
  'cat > /tmp/pldm_base_responder && chmod +x /tmp/pldm_base_responder && echo XFER_OK' < "$HELPER_RESP"

say "bringing up MCTP link + forward pldmtool over EID 18 ..."
sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" root@127.0.0.1 'bash -s' <<'BMC_EOF'
set +e
echo '### teardown stock unit'
systemctl kill --signal=SIGKILL mctp-local.service 2>/dev/null
systemctl reset-failed mctp-local.service 2>/dev/null
systemctl kill --signal=SIGKILL mctp-ldisc.service 2>/dev/null
systemctl reset-failed mctp-ldisc.service 2>/dev/null
pkill -9 -f 'mctp link serial' 2>/dev/null
sleep 1
echo '### start ldisc + link up + addr'
stty -F /dev/ttyS0 115200 litout -crtscts -ixon -echo raw
systemd-run --unit=mctp-ldisc --service-type=simple mctp link serial /dev/ttyS0
sleep 3
echo -n 'ldisc active? '; systemctl is-active mctp-ldisc.service
mctp link set mctpserial0 up ; echo "link-up rc=$?"
mctp addr add 8 dev mctpserial0 ; echo "addr rc=$?"
IFX=$(cat /sys/class/net/mctpserial0/ifindex 2>/dev/null); echo "ifindex=$IFX"
echo '### install route via raw-netlink helper (CLI route add busy-loops here)'
/tmp/mctp_route_add 18 "$IFX" ; echo "helper-route rc=$?"
echo '========== FORWARD: BMC (EID 8) -> Zephyr (EID 18) =========='
pldmtool base GetTID -m 18 ; echo "GetTID rc=$?"
pldmtool base GetPLDMTypes -m 18 ; echo "GetPLDMTypes rc=$?"
pldmtool base GetPLDMVersion -m 18 -t 0 ; echo "GetPLDMVersion rc=$?"
pldmtool platform GetPDR -m 18 -d 0 ; echo "GetPDR rc=$?"
pldmtool platform GetSensorReading -m 18 -i 1 --rearm 0 ; echo "GetSensorReading rc=$?"
echo '========== REVERSE prep: stop pldmd, start base responder on EID 8 =========='
for u in pldmd pldm xyz.openbmc_project.pldmd; do systemctl stop "$u" 2>/dev/null && echo "stopped $u"; done
pkill -9 -x pldmd 2>/dev/null && echo "pkilled pldmd"
sleep 1
setsid /tmp/pldm_base_responder 8 < /dev/null > /tmp/responder.log 2>&1 &
sleep 2
echo -n 'responder alive? '; pgrep -f pldm_base_responder >/dev/null && echo yes || echo no
echo '### DONE. Watch the Zephyr console (terminal 2) for the reverse probe.'
BMC_EOF
say "done. reverse probe is answered by the responder now running on the BMC."
