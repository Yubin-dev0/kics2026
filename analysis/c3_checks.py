#!/usr/bin/env python3
"""Two checks behind data/c3/NOTES.md.

    python3 analysis/c3_checks.py

1. Why A is late: for every policy-4 L1 run in the sweep, the detector metric
   `q_hat_ms` (p90 inter-arrival minus baseline) over the first 3 s after t0,
   next to the slope of the edge RTT (from the bridge log) over the first 1.5 s of
   the rise. The threshold is 20 ms.
2. C1-1: C1 run 2 flag send times (N3 wall clock) converted to the bridge's
   monotonic clock with the C1 clock offset and the run's clock pairs, against the
   bridge end time. Flags sent after the bridge ended cannot be received.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
EXCL = os.path.join(HERE, "excluded_runs.csv")
C1_DELTA_MS = -54174235.547  # C1 clock run 0: N3 wall - N1 wall


def qhat_check():
    sw = pd.read_csv(os.path.join(DATA, "sweep.csv")).set_index("run_id")
    ex = pd.read_csv(EXCL).query("stage == 'c3'").run_id
    runs = sw[(sw.policy == 4) & (sw.load == "L1") & (sw.status == "valid") & ~sw.index.isin(ex)].index
    rows = []
    for r in runs:
        m = json.load(open(os.path.join(DATA, "c3", f"n3_run_{r}_meta.json")))
        w = pd.read_csv(os.path.join(DATA, "c3", f"n3_run_{r}_windows.csv"))
        w["t"] = (w.t_ns - m["t0_ns"]) / 1e9
        q = w[(w.t >= 0) & (w.t <= 3)].q_hat_ms
        b = pd.read_csv(os.path.join(DATA, "c3", f"run_{r}.csv"))
        b = b[(b.kind != "settle") & b.rtt_us.notna()]
        rtt = b.rtt_us / 1000
        base = rtt.iloc[:200].median()
        t = b.t_send_ns / 1e9
        t_on = t[rtt > base + 50].iloc[0]
        seg = b[(t >= t_on) & (t <= t_on + 1.5)]
        slope = np.polyfit(seg.t_send_ns / 1e9 - t_on, seg.rtt_us / 1000, 1)[0]
        rows.append({"run": r, "rtt_ms": int(sw.loc[r, "rtt_ms"]),
                     "qhat_med_0-3s": round(q.median()), "qhat_max_0-3s": round(q.max()),
                     "steps_qhat>20": int((q > 20).sum()), "of": len(q),
                     "edge_rtt_slope_ms/s": round(slope),
                     "A_ms": round(sw.loc[r, "a_ms"]), "D_ms": round(sw.loc[r, "d_ms"]),
                     "G_ms": round(sw.loc[r, "g_ms"])})
    df = pd.DataFrame(rows)
    print("check 1: q_hat (p90 IAT - baseline) in the 3 s after t0, threshold 20 ms")
    print(df.to_string(index=False))
    print(f"q_hat median across runs: {df['qhat_med_0-3s'].min()}-{df['qhat_med_0-3s'].max()} ms; "
          f"edge RTT rise: {df['edge_rtt_slope_ms/s'].min()}-{df['edge_rtt_slope_ms/s'].max()} ms/s "
          "(run 40's negative slope is an onset-detection artefact; RTT there already sat at 700 ms)")


def fseq_check():
    m = json.load(open(os.path.join(DATA, "c1", "run_2_meta.json")))
    flags = pd.read_csv(os.path.join(DATA, "c1", "n3_run_2_flags.csv"))
    cp = m["clock_pairs"]["end"]
    wall_minus_mono = cp["wall_ns"] - cp["mono_ns"]
    end_mono = cp["mono_ns"]
    b = pd.read_csv(os.path.join(DATA, "c1", "run_2.csv"))
    rx = b[b.flag_seq.notna()].set_index(b[b.flag_seq.notna()].flag_seq.astype(int)).t_flag_rx_ns
    print("\ncheck 2: C1 run 2 flags, N3 send time in the bridge clock vs bridge end")
    for _, f in flags.iterrows():
        n1_mono = f.t_send_ns - C1_DELTA_MS * 1e6 - wall_minus_mono
        margin = (end_mono - n1_mono) / 1e9
        got = int(f.fseq) in rx.index
        trip = f" received +{(rx[int(f.fseq)] - n1_mono) / 1e6:.0f} ms" if got else " not received"
        print(f"fseq {int(f.fseq):2d} degrade {int(f.degrade)} sent {margin:+.2f} s before bridge end;{trip}")
    print(f"bridge logged {m['flags']['flags']} flags of {len(flags)} sent")


def main():
    qhat_check()
    fseq_check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
