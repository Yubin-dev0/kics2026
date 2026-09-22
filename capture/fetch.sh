#!/bin/bash
# Copies the N3 files of one run into data/<stage>/ (windows, flags, meta; never the pcap,
# which stays on N3 and is backed up by hand). From the repository root on N1:
#
#   capture/fetch.sh 1 b3
#   N3=yubin@192.168.60.1 capture/fetch.sh 2 c1
set -euo pipefail
RUN=${1:?usage: fetch.sh <run> <stage>}
STAGE=$(echo "${2:?usage: fetch.sh <run> <stage>}" | tr 'A-Z' 'a-z')
N3=${N3:-yubin@192.168.60.1}
DEST="$(dirname "$0")/../data/$STAGE"
mkdir -p "$DEST"
scp -q "$N3:/home/yubin/n3runs/$STAGE/n3_run_${RUN}_windows.csv" \
       "$N3:/home/yubin/n3runs/$STAGE/n3_run_${RUN}_flags.csv" \
       "$N3:/home/yubin/n3runs/$STAGE/n3_run_${RUN}_meta.json" "$DEST/"
ls -l "$DEST"/n3_run_${RUN}_*
python3 - "$DEST/n3_run_${RUN}_meta.json" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
d = m['detector']
print(f"run {m['run_id']} {m['stage']}: steps {d['steps']}, packets {d['packets']}, baseline {d['baseline_ms']}, "
      f"enters {d['enters']}, leaves {d['leaves']}, t0 {m['t0_ns']}, t_det_meta {m['t_det_meta_ns']}, A {m['a_ms']} ms")
EOF
