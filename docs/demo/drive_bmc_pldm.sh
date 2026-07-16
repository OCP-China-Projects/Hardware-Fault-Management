#!/bin/bash
# Terminal 3 — drive PLDM on the OpenBMC side over SSH (clean output).
#
# Terminals 1 & 2 (launch_openbmc.sh / launch_hfm.sh) show the two QEMU boots.
# This script SSHes into the already-running BMC (hostfwd 3222->22) and simply
# *observes* the fully-automatic discovery that has already happened, then runs
# forward pldmtool over EID 18.
#
# With the three fixes now baked into the image (kernel patch 0010 +
# Zephyr control-responder patch 0011 + BMC systemd ordering patch 0006),
# mctpd discovers the Zephyr endpoint on its own at boot: it runs
# SetupEndpoint, installs the kernel route + neighbour for EID 18, and
# publishes it on D-Bus for pldmd. There is NO manual `mctp route add`,
# no raw-netlink helper, and no pldmd surgery — the reverse probe is also
# answered automatically by the stock pldmd.
#
# Run it AFTER both QEMUs are up and the BMC shows its login banner.
set -u

SSH_PORT=3222
PASS=0penBmc

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

say "observing auto-discovery + forward pldmtool over EID 18 ..."
sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" root@127.0.0.1 'bash -s' <<'BMC_EOF'
set +e
# mctp-setup-endpoint.service retries SetupEndpoint 40x3s at boot; give it a
# moment to land the route in case terminal 2 (Zephyr) only just connected.
echo '### waiting for mctpd to auto-discover EID 18 (mctp-setup-endpoint.service) ...'
for i in $(seq 1 40); do
  mctp route show 2>/dev/null | grep -q 'eid min 18' && break
  sleep 3
done
echo '### mctpd auto-discovered route (no manual route add):'
mctp route show
echo '### MCTP neighbours:'
mctp neigh show 2>/dev/null
echo '### D-Bus endpoints published by mctpd:'
busctl tree au.com.codeconstruct.MCTP1 2>/dev/null | grep -o 'endpoints/[0-9]*' | sort -u
echo '### mctp-setup-endpoint.service status:'
systemctl is-active mctp-setup-endpoint.service
echo '========== FORWARD: BMC (EID 8) -> Zephyr (EID 18) =========='
pldmtool base GetTID -m 18 ; echo "GetTID rc=$?"
pldmtool base GetPLDMTypes -m 18 ; echo "GetPLDMTypes rc=$?"
pldmtool base GetPLDMVersion -m 18 -t 0 ; echo "GetPLDMVersion rc=$?"
pldmtool platform GetPDR -m 18 -d 0 ; echo "GetPDR rc=$?"
pldmtool platform GetSensorReading -m 18 -i 1 --rearm 0 ; echo "GetSensorReading rc=$?"
echo '### DONE. Reverse probe already completed on the Zephyr console (terminal 2).'
BMC_EOF
say "done. discovery + forward path are fully automatic; reverse probe is answered by the stock pldmd."
