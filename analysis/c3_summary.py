#!/usr/bin/env python3
"""Table 1 and figure 2 from the C3 sweep index.

    python3 analysis/c3_summary.py [data/sweep.csv]

Drops rows whose status is not `valid` and the runs listed in
analysis/excluded_runs.csv (their re-runs are in the file already).
Writes analysis/out/table1.csv, analysis/out/holds.csv and analysis/out/fig2.pdf
(and .png), and prints both tables.

Figure 2(a): A (metadata detector) and D (RTT watcher) against base RTT, L1 rows of
every policy, three points per condition and the median as the line.
Figure 2(b): hold rate against base RTT, one curve per policy (decision D19, option
(ga): the course produced no collisions, so n_col is not a usable y axis).
"""
import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
EXCL = os.path.join(HERE, "excluded_runs.csv")
RTTS = [10, 30, 60, 100, 200]
POLICY = {1: "always local", 2: "always edge", 3: "RTT window", 4: "metadata flag"}
# colour + marker + line style: the Okabe-Ito palette is colour-blind safe, and the marker
# and line style keep the four policies apart in a greyscale print (FIGURES.md)
STYLE = {1: ("o", "-", "#000000"), 2: ("s", "--", "#E69F00"), 3: ("^", "-.", "#0072B2"), 4: ("D", ":", "#D55E00")}
COL_A, COL_D = "#D55E00", "#0072B2"


def load(path):
    df = pd.read_csv(path)
    ex = pd.read_csv(EXCL)
    used = df[(df.status == "valid") & ~df.run_id.isin(ex.run_id)].copy()
    used["cond"] = used.rtt_ms.astype(int).astype(str) + "/" + used.load
    return used


def table1(d):
    rows = []
    for rtt in RTTS:
        g = d[(d.load == "L1") & (d.rtt_ms == rtt)]
        p4 = g[g.policy == 4]
        rows.append({
            "rtt_ms": rtt, "n": len(g),
            "A_med_ms": round(g.a_ms.median()), "D_med_ms": round(g.d_ms.median()),
            "A_lt_D": int((g.a_ms < g.d_ms).sum()),
            "B_med_ms": round(p4.b_ms.median()), "U_ms": p4.u_ms.median(),
            "C_us": round(p4.c_ms.median() * 1000), "G_med_ms": round(p4.g_ms.median()),
            "G_pos": int((p4.g_ms > 0).sum()),
            "n_sw_p3": int(g[g.policy == 3].n_sw.sum()), "n_sw_p4": int(p4.n_sw.sum()),
        })
    l2 = d[d.load == "L2"]
    rows.append({
        "rtt_ms": "60 (L2)", "n": len(l2),
        "A_med_ms": round(l2.a_ms.median()), "D_med_ms": round(l2.d_ms.median()),
        "A_lt_D": int((l2.a_ms < l2.d_ms).sum()),
        "B_med_ms": round(l2[l2.policy == 4].b_ms.median()), "U_ms": l2.u_ms.median(),
        "C_us": round(l2.c_ms.median() * 1000), "G_med_ms": round(l2[l2.policy == 4].g_ms.median()),
        "G_pos": int((l2.g_ms > 0).sum()),
        "n_sw_p3": int(l2[l2.policy == 3].n_sw.sum()), "n_sw_p4": int(l2[l2.policy == 4].n_sw.sum()),
    })
    return pd.DataFrame(rows)


def holds(d):
    t = d.pivot_table(index="cond", columns="policy", values="hold_rate", aggfunc="median", sort=False)
    return (t * 100).round(1)


def fig2(d, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 8, "axes.linewidth": 0.6, "lines.linewidth": 1.0,
                         "xtick.major.width": 0.6, "ytick.major.width": 0.6})
    w = 8.37 / 2.54
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(w, 3.4), sharex=True, dpi=200)
    l1 = d[d.load == "L1"]

    # (a) A and D, every policy's L1 runs
    for col, lab, mk, ls, c in (("a_ms", "A: metadata flag", "o", "-", COL_A),
                                ("d_ms", "D: RTT window", "x", "--", COL_D)):
        med = [l1[l1.rtt_ms == r][col].median() for r in RTTS]
        for r in RTTS:
            ax1.plot([r] * len(l1[l1.rtt_ms == r]), l1[l1.rtt_ms == r][col], mk, ms=3, mfc="none",
                     color=c, alpha=0.4, mew=0.6, zorder=2)
        ax1.plot(RTTS, med, ls, marker=mk, ms=4, color=c, label=lab, zorder=3)
    ax1.set_ylabel("delay after t0 (ms)")
    ax1.set_yscale("log")
    ax1.set_yticks([500, 1000, 2000, 4000])
    ax1.set_yticklabels(["500", "1000", "2000", "4000"])
    ax1.set_ylim(400, 8000)
    ax1.legend(frameon=False, loc="upper left", fontsize=7)
    ax1.grid(True, axis="y", color="0.85", linewidth=0.5)
    ax1.text(0.98, 0.92, "(a)", transform=ax1.transAxes, ha="right")

    # (b) hold rate per policy
    for p in (1, 2, 3, 4):
        g = l1[l1.policy == p]
        med = [g[g.rtt_ms == r].hold_rate.median() * 100 for r in RTTS]
        mk, ls, c = STYLE[p]
        ax2.plot(RTTS, med, ls, marker=mk, ms=4, color=c, mfc="white" if p in (2, 4) else c,
                 label=POLICY[p])
    ax2.set_ylabel("hold rate (%)")
    ax2.set_xlabel("base RTT (ms, netem)")
    ax2.set_xscale("log")
    ax2.set_xticks(RTTS)
    ax2.set_xticklabels([str(r) for r in RTTS])
    ax2.set_ylim(0, 32)
    ax2.legend(frameon=False, ncol=2, loc="lower center", fontsize=7)
    ax2.grid(True, axis="y", color="0.85", linewidth=0.5)
    ax2.text(0.98, 0.92, "(b)", transform=ax2.transAxes, ha="right")

    fig.tight_layout(h_pad=0.4)
    fig.savefig(path + ".pdf")
    fig.savefig(path + ".png")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sweep", nargs="?", default=os.path.join(HERE, "..", "data", "sweep.csv"))
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    d = load(a.sweep)
    print(f"used runs: {len(d)} (excluded: {pd.read_csv(EXCL).run_id.tolist()})")
    t1 = table1(d)
    h = holds(d)
    t1.to_csv(os.path.join(OUT, "table1.csv"), index=False)
    h.to_csv(os.path.join(OUT, "holds.csv"))
    print(t1.to_string(index=False))
    print("\nhold rate, median % (rows: rtt/load, cols: policy)")
    print(h.to_string())
    print(f"\ncollisions: {int(d.n_col.sum())}, min range {d.min_range_m.min():.3f}-{d.min_range_m.max():.3f} m")
    print(f"C per switch: {d.c_ms.min()*1000:.0f}-{d.c_ms.max()*1000:.0f} us")
    fig2(d, os.path.join(OUT, "fig2"))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
