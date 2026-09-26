#!/usr/bin/env python3
"""C5 (flag in its own queue) against the C3 policy-4 runs of the same base RTT.

    python3 analysis/c5_compare.py [data/sweep_c5.csv [data/sweep.csv]]

Drops the runs listed in analysis/excluded_runs.csv for each stage. Prints, per base RTT
and variant (V1 = prio band only, V2 = prio band + DSCP CS6):

  1. B, A, D, G medians and G > 0 counts for C5 and for the C3 policy-4 runs, and
     dB = B(C3) - B(C5) (positive: C5 is faster);
  2. per run: the first flag's wait in the N3 qdisc (flagcap time - t_send_ns, both on
     the N3 wall clock) and what is left of B below the qdisc;
  3. the pre-registered C5 verdicts (0926 guide, 2.3).

B carries the clock-offset bias of the sweep: the offset is taken with netem already on
N3's egress only, so the NTP estimate is low by about half the one-way delay h and B
reads low by about h/2 (C3 has the same bias, so dB is unaffected). V2 at 60 ms shows it:
B 21-23 ms against a flag that provably spent 30 ms in the qdisc. The below-qdisc column
therefore uses B + h/2 - q1. Writes analysis/out/c5_compare.csv.
"""
import argparse
import csv
import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
OUT = os.path.join(HERE, "out")
EXCL = os.path.join(HERE, "excluded_runs.csv")
ONE_WAY = {10: 5, 60: 30, 200: 100}          # netem one-way delay h per base RTT (ms)
PASS_B = {10: 45, 60: 70, 200: 140}          # C5-2: B median <= h + 40 ms


def used(path, stage):
    df = pd.read_csv(path)
    ex = pd.read_csv(EXCL)
    ex = ex[ex.stage == stage].run_id
    return df[(df.status == "valid") & ~df.run_id.isin(ex) & df.a_ms.notna()].copy()


def variant(run):
    q = os.path.join(DATA, "c5", f"qdisc_run_{run}.txt")
    return "V2" if os.path.exists(q) and "dscp set cs6" in open(q).read() else "V1"


def flag_waits(run):
    """qdisc wait of every flag (ms) and the band-0 packet count."""
    d = os.path.join(DATA, "c5")
    fl = list(csv.DictReader(open(os.path.join(d, f"n3_run_{run}_flags.csv"))))
    cap_p = os.path.join(d, f"flagcap_run_{run}.txt")
    cap = [float(l.split()[0]) for l in open(cap_p) if "UDP" in l] if os.path.exists(cap_p) else []
    waits = [round((c - int(f["t_send_ns"]) / 1e9) * 1000, 1) for f, c in zip(fl, cap)]
    band0 = None
    q_p = os.path.join(d, f"qdisc_run_{run}.txt")
    if os.path.exists(q_p):
        part = open(q_p).read().split("netem 10:")
        if len(part) > 1:
            band0 = int(re.search(r"Sent \d+ bytes (\d+) pkt", part[1]).group(1))
    return waits, band0, len(fl)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("c5", nargs="?", default=os.path.join(DATA, "sweep_c5.csv"))
    ap.add_argument("c3", nargs="?", default=os.path.join(DATA, "sweep.csv"))
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)

    c5 = used(a.c5, "c5")
    c5["rtt"] = c5.rtt_ms.astype(int)
    c5["variant"] = c5.run_id.map(variant)
    c3 = used(a.c3, "c3")
    c3 = c3[(c3.load == "L1")]
    c3["rtt"] = c3.rtt_ms.astype(int)
    c3p4 = c3[c3.policy == 4]

    print(f"C5 runs used: {sorted(c5.run_id.tolist())}")
    rows = []
    print("\n1. medians (ms), C3 policy 4 against C5")
    print(f"{'RTT':>4} {'var':>3} {'n':>2} | {'B C3':>6} {'B C5':>6} {'dB':>6} | {'A C3':>6} {'A C5':>6} "
          f"| {'D C3':>6} {'D C5':>6} | {'G C3':>7} {'G C5':>7} | G>0 C3  C5")
    for (rtt, var), g in c5.groupby(["rtt", "variant"]):
        p = c3p4[c3p4.rtt == rtt]
        r = {"rtt_ms": rtt, "variant": var, "n": len(g),
             "b_c3": p.b_ms.median(), "b_c5": g.b_ms.median(),
             "a_c3": p.a_ms.median(), "a_c5": g.a_ms.median(),
             "d_c3": p.d_ms.median(), "d_c5": g.d_ms.median(),
             "g_c3": p.g_ms.median(), "g_c5": g.g_ms.median(),
             "gpos_c3": f"{int((p.g_ms > 0).sum())}/{len(p)}", "gpos_c5": f"{int((g.g_ms > 0).sum())}/{len(g)}"}
        r["db"] = r["b_c3"] - r["b_c5"]
        rows.append(r)
        print(f"{rtt:>4} {var:>3} {len(g):>2} | {r['b_c3']:6.0f} {r['b_c5']:6.0f} {r['db']:6.0f} | "
              f"{r['a_c3']:6.0f} {r['a_c5']:6.0f} | {r['d_c3']:6.0f} {r['d_c5']:6.0f} | "
              f"{r['g_c3']:7.0f} {r['g_c5']:7.0f} | {r['gpos_c3']:>5} {r['gpos_c5']:>4}")
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "c5_compare.csv"), index=False)

    print("\n2. per run: first flag's qdisc wait q1 and B left below the qdisc (B + h/2 - q1)")
    print(f"{'run':>4} {'RTT':>4} {'var':>3} {'B':>7} {'q1':>6} {'below':>6}  flags band0  all qdisc waits")
    c1_ok = True
    for _, r in c5.sort_values(["variant", "rtt", "run_id"]).iterrows():
        waits, band0, nflags = flag_waits(r.run_id)
        h = ONE_WAY[r.rtt]
        q1 = waits[0] if waits else None
        below = r.b_ms + h / 2 - q1 if q1 is not None else None
        c1_ok &= band0 == nflags
        print(f"{r.run_id:>4} {r.rtt:>4} {r.variant:>3} {r.b_ms:7.1f} "
              f"{'-' if q1 is None else f'{q1:6.1f}':>6} {'-' if below is None else f'{below:6.1f}':>6}"
              f"  {nflags:>5} {band0!s:>5}  {waits}")

    print("\n3. verdicts (0926 guide 2.3, set before the data)")
    print(f"C5-1 band 0 = flags in every used run: {'pass' if c1_ok else 'FAIL'}")
    for r in rows:
        lim = ONE_WAY[r['rtt_ms']] + 40
        verdict = "pass" if r["b_c5"] <= lim else "fail"
        print(f"C5-2 RTT {r['rtt_ms']} {r['variant']}: B median {r['b_c5']:.0f} ms against <= {lim} -> {verdict}; "
              f"dB {r['db']:+.0f} ms")
    for r in rows:
        p = c3p4.rtt == r["rtt_ms"]
        allc3 = c3[c3.rtt == r["rtt_ms"]]
        g = c5[(c5.rtt == r["rtt_ms"]) & (c5.variant == r["variant"])]
        a_in = g.a_ms.between(allc3.a_ms.min(), allc3.a_ms.max())
        d_in = g.d_ms.between(allc3.d_ms.min(), allc3.d_ms.max())
        print(f"C5-3 RTT {r['rtt_ms']} {r['variant']}: A in C3 range {int(a_in.sum())}/{len(g)} "
              f"[{allc3.a_ms.min():.0f}-{allc3.a_ms.max():.0f}], D in C3 range {int(d_in.sum())}/{len(g)} "
              f"[{allc3.d_ms.min():.0f}-{allc3.d_ms.max():.0f}] (memo, not a verdict)")
    for r in rows:
        print(f"C5-4 RTT {r['rtt_ms']} {r['variant']}: G > 0 in {r['gpos_c5']} (C3 policy 4: {r['gpos_c3']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
