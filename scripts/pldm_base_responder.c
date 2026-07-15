/*
 * Copyright (c) 2026 OCP Hardware-Fault-Management project
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal PLDM Type 0 (base) responder over Linux AF_MCTP, for the OpenBMC
 * side of the two-QEMU bridge. OpenBMC's own pldmd is a PLDM *requester*
 * (management controller) and does not answer inbound base commands, so the
 * reverse-direction test (Zephyr EID 18 -> BMC EID 8) has nobody to reply.
 *
 * This helper binds the local EID for MCTP message type 1 (PLDM) and answers
 * GetTID / GetPLDMTypes / GetPLDMVersion so the Zephyr requester completes,
 * proving host->BMC delivery through the real kernel MCTP stack.
 *
 * The kernel headers for AF_MCTP are not present in every cross sysroot, so
 * the tiny ABI we need is declared inline (kernel uapi linux/mctp.h).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/time.h>

#ifndef AF_MCTP
#define AF_MCTP 45
#endif

#define MCTP_NET_ANY   0
#define MCTP_ADDR_ANY  0xff
#define MCTP_TAG_OWNER 0x08

struct mctp_addr {
	unsigned char s_addr;
};

struct sockaddr_mctp {
	unsigned short smctp_family;
	unsigned short __smctp_pad0;
	unsigned int   smctp_network;
	struct mctp_addr smctp_addr;
	unsigned char  smctp_type;
	unsigned char  smctp_tag;
	unsigned char  __smctp_pad1;
};

/* MCTP message type byte carried in smctp_type. */
#define MCTP_TYPE_PLDM 1

/* PLDM base commands (DSP0240 §9). */
#define PLDM_GET_TID          0x02
#define PLDM_GET_PLDM_VERSION 0x03
#define PLDM_GET_PLDM_TYPES   0x04

#define PLDM_SUCCESS 0x00

/* Advertised terminus id for this BMC endpoint. */
#define BMC_TID 0x08

int main(int argc, char **argv)
{
	int fd, rc;
	struct sockaddr_mctp addr;
	socklen_t alen;
	unsigned char req[256];
	unsigned char resp[256];
	int n_ans = 0;
	int max_ans = (argc > 1) ? atoi(argv[1]) : 6;

	fd = socket(AF_MCTP, SOCK_DGRAM, 0);
	if (fd < 0) {
		fprintf(stderr, "socket(AF_MCTP) failed: %s\n", strerror(errno));
		return 1;
	}

	memset(&addr, 0, sizeof(addr));
	addr.smctp_family = AF_MCTP;
	addr.smctp_network = MCTP_NET_ANY;
	addr.smctp_addr.s_addr = MCTP_ADDR_ANY;
	addr.smctp_type = MCTP_TYPE_PLDM;
	addr.smctp_tag = MCTP_TAG_OWNER;

	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
		fprintf(stderr, "bind(AF_MCTP,type=1) failed: %s\n",
			strerror(errno));
		close(fd);
		return 1;
	}

	printf("pldm_base_responder: bound PLDM type on local EID, waiting\n");
	fflush(stdout);

	/* Recv with a periodic timeout so we can emit heartbeats and prove the
	 * socket is alive even when no packets arrive. */
	struct timeval tv = { .tv_sec = 2, .tv_usec = 0 };
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

	int hb = 0;
	while (n_ans < max_ans) {
		alen = sizeof(addr);
		rc = recvfrom(fd, req, sizeof(req), 0,
			      (struct sockaddr *)&addr, &alen);
		if (rc < 0) {
			if (errno == EINTR ||
			    errno == EAGAIN || errno == EWOULDBLOCK) {
				if (++hb % 5 == 0) {
					printf("heartbeat: still waiting "
					       "(no PLDM RX yet)\n");
					fflush(stdout);
				}
				continue;
			}
			fprintf(stderr, "recvfrom failed: %s\n",
				strerror(errno));
			break;
		}
		if (rc < 3) {
			printf("short PLDM msg (%d bytes), ignoring\n", rc);
			fflush(stdout);
			continue;
		}

		unsigned char iid = req[0] & 0x1f;
		unsigned char cmd = req[2];

		printf("RX from EID %u tag 0x%02x: iid=%u type=%u cmd=0x%02x\n",
		       addr.smctp_addr.s_addr, addr.smctp_tag, iid,
		       req[1] & 0x3f, cmd);
		fflush(stdout);

		/* Common PLDM response header: request bit cleared. */
		resp[0] = iid;       /* instance id, request=0, datagram=0 */
		resp[1] = req[1];    /* echo type (0) + header version */
		resp[2] = cmd;       /* echo command */
		int rlen = 0;

		switch (cmd) {
		case PLDM_GET_TID:
			resp[3] = PLDM_SUCCESS;
			resp[4] = BMC_TID;
			rlen = 5;
			break;
		case PLDM_GET_PLDM_TYPES:
			resp[3] = PLDM_SUCCESS;
			memset(&resp[4], 0, 8);
			resp[4] = 0x01; /* bit 0 => PLDM base (type 0) */
			rlen = 12;
			break;
		case PLDM_GET_PLDM_VERSION:
			resp[3] = PLDM_SUCCESS;
			resp[4] = 0; resp[5] = 0; resp[6] = 0; resp[7] = 0; /* next handle */
			resp[8] = 0x05; /* transfer flag: Start&End */
			resp[9]  = 0xf1; /* major 1 */
			resp[10] = 0xf1; /* minor 1 */
			resp[11] = 0xf0; /* update 0 */
			resp[12] = 0x00; /* alpha */
			rlen = 13;
			break;
		default:
			/* Unsupported command: CC-only ERROR (0x05). */
			resp[3] = 0x05;
			rlen = 4;
			break;
		}

		/*
		 * Reply to the address recvfrom() filled in. The kernel
		 * AF_MCTP layer clears the tag-owner bit for the response
		 * automatically, so the requester (Zephyr EID 18) matches it.
		 */
		rc = sendto(fd, resp, rlen, 0, (struct sockaddr *)&addr, alen);
		if (rc < 0) {
			fprintf(stderr, "sendto failed: %s\n", strerror(errno));
			continue;
		}
		printf("TX response cmd=0x%02x len=%d to EID %u\n", cmd, rlen,
		       addr.smctp_addr.s_addr);
		fflush(stdout);
		n_ans++;
	}

	printf("pldm_base_responder: answered %d request(s), exiting\n", n_ans);
	fflush(stdout);
	close(fd);
	return 0;
}
