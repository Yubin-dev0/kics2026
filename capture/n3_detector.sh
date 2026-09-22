#!/bin/bash
# Starts the A7 detector on N3 for one run, from the repository root on N1. Nothing is
# cloned onto N3: the script is piped over ssh, its files land in /home/yubin/n3runs/<stage>
# on N3 and come back with capture/fetch.sh. The commit of detector.py is passed along
# because N3 has no clone. Keep the window open for the run (--seconds, default 90 s);
# extra arguments go to detector.py:
#
#   capture/n3_detector.sh 1 b3                                       observe only
#   capture/n3_detector.sh 2 c1 --load L1 --n5 192.168.60.20         load at t0
#   capture/n3_detector.sh 3 c3 --load L2 --n5 192.168.60.20 --metric pair
set -euo pipefail
RUN=${1:?usage: n3_detector.sh <run> <stage> [detector args]}
STAGE=${2:?usage: n3_detector.sh <run> <stage> [detector args]}
shift 2
N3=${N3:-yubin@192.168.60.1}
GIT=$(git rev-parse --short HEAD 2>/dev/null || echo none)
if [ -n "$(git status --porcelain capture/detector.py 2>/dev/null)" ]; then GIT="$GIT-DIRTY"; fi
exec ssh "$N3" "sudo python3 - --run $RUN --stage $STAGE --git $GIT $*" < "$(dirname "$0")/detector.py"
