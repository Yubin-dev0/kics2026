#!/bin/bash
# iperf3 server on N3 for the A8 load (N5 runs the client with -R, so N3 sends and the load
# crosses N3's wlan0 egress queue). From the repository root on N1:
#
#   ssh yubin@192.168.60.1 'bash -s' < load/n3_server.sh            start (daemon, port 5201)
#   ssh yubin@192.168.60.1 'bash -s -- stop' < load/n3_server.sh
#   ssh yubin@192.168.60.1 'bash -s -- status' < load/n3_server.sh
#
# Whether the server stays on N3 or moves to N4 is decided at A8 (load/README.md): if
# serving the load costs N3 capture completeness (tcpdump "dropped by kernel" > 0) or CPU,
# the server goes to N4 and N5 reaches it through N3's NAT.
set -euo pipefail
ARG=${1:-start}
LOG=/home/yubin/load/iperf3_server.log
mkdir -p /home/yubin/load
case "$ARG" in
    start)
        pkill -x iperf3 2>/dev/null || true
        iperf3 -s -p 5201 -D --logfile "$LOG"
        sleep 0.5
        pgrep -a -x iperf3 || { echo "iperf3 server did not start" >&2; exit 1; }
        echo "iperf3 server on port 5201, log $LOG" ;;
    stop)
        pkill -x iperf3 && echo stopped || echo "not running" ;;
    status)
        pgrep -a -x iperf3 || echo "not running"
        tail -n 5 "$LOG" 2>/dev/null || true ;;
    *)
        echo "usage: n3_server.sh [start|stop|status]" >&2; exit 2 ;;
esac
