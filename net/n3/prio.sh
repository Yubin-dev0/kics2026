#!/bin/bash
# C5 variant of the sweep qdisc: the switching flag (UDP from N3 port 47100, N3 -> N1)
# gets its own band in front of everything else that leaves wlan0, so it no longer
# waits behind the L1 load (plan D5 kept them in one queue; this measures what that
# choice cost). eth0 is unchanged: plain netem as in nq.
#
#   ssh yubin@<N3> 'sudo bash -s -- 30 830'       < net/n3/prio.sh   one-way 30 ms, limit 830
#   ssh yubin@<N3> 'sudo bash -s -- 30 830 dscp'  < net/n3/prio.sh   plus DSCP CS6 on the flag
#   ssh yubin@<N3> 'sudo bash -s -- show'         < net/n3/prio.sh   per-band counters
#   ssh yubin@<N3> 'sudo bash -s -- off'          < net/n3/prio.sh   back to fq_codel, rule gone
#
# wlan0 root = prio with 3 bands, every packet in band 1 (priomap all 1) except the flag,
# which a u32 filter sends to band 0. Each band carries its own netem with the same delay
# and limit as nq, so the flag still pays the base one-way delay but not the queue.
# `show` prints the per-band packet counts: after a policy 4 run band 0 (handle 10:) must
# have sent exactly the flags of that run (C5-1).
# The optional `dscp` marks the flag CS6 (nft, table c5) so the Wi-Fi driver puts it in
# the voice access category (802.11e), which is the queue below tc that nq cannot bound.
set -euo pipefail
ARG=${1:?usage: prio.sh <one-way ms> <limit> [dscp] | show | off}
TC=/usr/sbin/tc
NFT=/usr/sbin/nft

case "$ARG" in
    off)
        $TC qdisc del dev wlan0 root 2>/dev/null || true
        $TC qdisc del dev eth0 root 2>/dev/null || true
        $NFT delete table ip c5 2>/dev/null || true ;;
    show)
        ;;
    *)
        HALF=$ARG
        LIMIT=${2:?usage: prio.sh <one-way ms> <limit> [dscp]}
        DSCP=${3:-}
        $TC qdisc replace dev wlan0 root handle 1: prio bands 3 priomap 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1
        for b in 1 2 3; do
            $TC qdisc replace dev wlan0 parent 1:$b handle ${b}0: netem delay "${HALF}ms" limit "$LIMIT"
        done
        $TC filter replace dev wlan0 parent 1: protocol ip prio 1 u32 \
            match ip protocol 17 0xff match ip sport 47100 0xffff flowid 1:1
        $TC qdisc replace dev eth0 root netem delay "${HALF}ms" limit "$LIMIT"
        $NFT delete table ip c5 2>/dev/null || true
        if [ "$DSCP" = "dscp" ]; then
            $NFT add table ip c5
            $NFT add chain ip c5 out '{ type filter hook output priority mangle; }'
            $NFT add rule ip c5 out oifname wlan0 udp sport 47100 ip dscp set cs6
        fi ;;
esac
echo "## wlan0 bands (10: = flag band)"
$TC -s qdisc show dev wlan0
echo "## wlan0 filter"
$TC filter show dev wlan0 parent 1:
echo "## eth0"
$TC -s qdisc show dev eth0
echo "## dscp rule"
$NFT list table ip c5 2>/dev/null || echo none
