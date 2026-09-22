#!/bin/bash
# Base RTT for the sweep (A6, C3): half of it on each egress of N3, wlan0 (towards N1 and
# N5) and eth0 (towards N4), so both directions of the robot flow carry the same delay.
#
#   ssh yubin@<N3> 'sudo bash -s -- 60'   < net/n3/netem.sh     base RTT 60 ms
#   ssh yubin@<N3> 'sudo bash -s -- off'  < net/n3/netem.sh     back to the default qdisc
#   ssh yubin@<N3> 'sudo bash -s -- show' < net/n3/netem.sh
#
# netem replaces the root qdisc (fq_codel by default) for the whole sweep, so every sweep
# step has the same queueing discipline: a netem FIFO in front of the driver. limit 10000
# packets keeps netem from dropping under L1 at 200 ms (net/README.md).
set -euo pipefail
ARG=${1:?usage: netem.sh <rtt_ms> | off | show}
DEVS=(wlan0 eth0)

case "$ARG" in
    off)
        for d in "${DEVS[@]}"; do tc qdisc del dev "$d" root 2>/dev/null || true; done ;;
    show)
        ;;
    *)
        half=$(awk -v r="$ARG" 'BEGIN { if (r !~ /^[0-9.]+$/) exit 1; printf "%g", r / 2 }') ||
            { echo "not a number: $ARG" >&2; exit 2; }
        for d in "${DEVS[@]}"; do
            tc qdisc replace dev "$d" root netem delay "${half}ms" limit 10000
        done ;;
esac
for d in "${DEVS[@]}"; do tc -s qdisc show dev "$d"; done
