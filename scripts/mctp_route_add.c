/*
 * mctp_route_add <eid> <ifindex> [mtu]
 *
 * Minimal AF_NETLINK helper: installs an MCTP route (RTM_NEWROUTE, family
 * AF_MCTP) to <eid> out of interface <ifindex>. Written to bypass a busy-loop
 * bug in this OpenBMC image's `mctp route add` CLI. Statically linked, dropped
 * onto the BMC (which has no python/perl/compiler, only busybox).
 *
 * Route attribute layout mirrors the kernel's net/mctp/route.c handling of
 * RTM_NEWROUTE: rtmsg{ rtm_family = AF_MCTP, rtm_dst_len = 0, rtm_type =
 * RTN_UNICAST } + RTA_DST(u8 eid) + RTA_OIF(u32 ifindex)
 * [+ nested RTA_METRICS -> RTAX_MTU(u32)].
 *
 * NETLINK_EXT_ACK + NETLINK_CAP_ACK are enabled so the kernel returns its
 * diagnostic string on failure (and the ACK is capped, so extack TLVs sit
 * immediately after struct nlmsgerr -- trivial to parse).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>

#ifndef AF_MCTP
#define AF_MCTP 45
#endif
#ifndef NETLINK_EXT_ACK
#define NETLINK_EXT_ACK 11
#endif
#ifndef NETLINK_CAP_ACK
#define NETLINK_CAP_ACK 10
#endif
#ifndef NLM_F_ACK_TLVS
#define NLM_F_ACK_TLVS 0x200
#endif
#ifndef NLMSGERR_ATTR_MSG
#define NLMSGERR_ATTR_MSG 1
#endif

struct req {
    struct nlmsghdr nlh;
    struct rtmsg rtm;
    char attrs[128];
};

static int add_attr(struct nlmsghdr *nlh, int maxlen, int type,
                    const void *data, int alen)
{
    int len = RTA_LENGTH(alen);
    struct rtattr *rta;
    if (NLMSG_ALIGN(nlh->nlmsg_len) + RTA_ALIGN(len) > (unsigned)maxlen) {
        fprintf(stderr, "attr overflow\n");
        return -1;
    }
    rta = (struct rtattr *)((char *)nlh + NLMSG_ALIGN(nlh->nlmsg_len));
    rta->rta_type = type;
    rta->rta_len = len;
    memcpy(RTA_DATA(rta), data, alen);
    nlh->nlmsg_len = NLMSG_ALIGN(nlh->nlmsg_len) + RTA_ALIGN(len);
    return 0;
}

static void dump_extack(struct nlmsghdr *rh)
{
    struct nlmsgerr *e = (struct nlmsgerr *)NLMSG_DATA(rh);
    if (!(rh->nlmsg_flags & NLM_F_ACK_TLVS))
        return;
    /* CAP_ACK is on, so the original request is not echoed: TLVs start right
     * after struct nlmsgerr. */
    struct rtattr *rta = (struct rtattr *)((char *)e + sizeof(*e));
    int rtalen = rh->nlmsg_len - NLMSG_LENGTH(sizeof(*e));
    for (; RTA_OK(rta, rtalen); rta = RTA_NEXT(rta, rtalen)) {
        if (rta->rta_type == NLMSGERR_ATTR_MSG)
            fprintf(stderr, "extack: %s\n", (char *)RTA_DATA(rta));
    }
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s <eid> <ifindex> [mtu]\n", argv[0]);
        return 2;
    }
    unsigned char eid = (unsigned char)atoi(argv[1]);
    unsigned int ifindex = (unsigned int)atoi(argv[2]);
    unsigned int mtu = argc > 3 ? (unsigned int)atoi(argv[3]) : 0;

    int fd = socket(AF_NETLINK, SOCK_RAW, NETLINK_ROUTE);
    if (fd < 0) { perror("socket"); return 1; }

    int one = 1;
    setsockopt(fd, SOL_NETLINK, NETLINK_EXT_ACK, &one, sizeof(one));
    setsockopt(fd, SOL_NETLINK, NETLINK_CAP_ACK, &one, sizeof(one));

    struct req r;
    memset(&r, 0, sizeof(r));
    r.nlh.nlmsg_len = NLMSG_LENGTH(sizeof(struct rtmsg));
    r.nlh.nlmsg_type = RTM_NEWROUTE;
    r.nlh.nlmsg_flags = NLM_F_REQUEST | NLM_F_CREATE | NLM_F_REPLACE | NLM_F_ACK;
    r.nlh.nlmsg_seq = 1;
    r.rtm.rtm_family = AF_MCTP;
    r.rtm.rtm_dst_len = 0;         /* single-EID route (extent 0) */
    r.rtm.rtm_type = RTN_UNICAST;

    add_attr(&r.nlh, sizeof(r), RTA_DST, &eid, sizeof(eid));
    add_attr(&r.nlh, sizeof(r), RTA_OIF, &ifindex, sizeof(ifindex));
    if (mtu) {
        char mbuf[RTA_LENGTH(sizeof(unsigned int))];
        struct rtattr *m = (struct rtattr *)mbuf;
        m->rta_type = RTAX_MTU;
        m->rta_len = RTA_LENGTH(sizeof(unsigned int));
        memcpy(RTA_DATA(m), &mtu, sizeof(mtu));
        add_attr(&r.nlh, sizeof(r), RTA_METRICS, mbuf, sizeof(unsigned int));
    }

    struct sockaddr_nl sa;
    memset(&sa, 0, sizeof(sa));
    sa.nl_family = AF_NETLINK;

    if (sendto(fd, &r, r.nlh.nlmsg_len, 0,
               (struct sockaddr *)&sa, sizeof(sa)) < 0) {
        perror("sendto");
        return 1;
    }

    char buf[1024];
    int n = recv(fd, buf, sizeof(buf), 0);
    if (n < 0) { perror("recv"); return 1; }

    struct nlmsghdr *rh = (struct nlmsghdr *)buf;
    if (rh->nlmsg_type == NLMSG_ERROR) {
        struct nlmsgerr *e = (struct nlmsgerr *)NLMSG_DATA(rh);
        if (e->error == 0) {
            printf("route add OK: eid=%u oif=%u mtu=%u\n", eid, ifindex, mtu);
            return 0;
        }
        fprintf(stderr, "netlink error: %s (%d)\n",
                strerror(-e->error), e->error);
        dump_extack(rh);
        return 1;
    }
    printf("unexpected nlmsg_type=%u\n", rh->nlmsg_type);
    return 1;
}
