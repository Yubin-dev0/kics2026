# What the paper's two figures and one table read

Everything on this page is drawn from one file, `data/sweep.csv`, one row per run
(columns in `data/README.md`). Until the sweep of 2026-09-26 exists, write the scripts
against `data/synthetic/sweep.csv`, which has the same header and the same 66 rows with
invented values. On 9/26 the only change is the path.

Check any file before drawing from it:

    python3 analysis/sweep_index.py --check data/synthetic/sweep.csv

Scripts live in `analysis/` and write into `analysis/out/` (git-ignored). Each script
takes the sweep file as its first argument and defaults to the synthetic one.

## Shared rules

- Rows with `status` other than `valid` are dropped. Never quote a dropped run.
- Each condition has 3 repetitions. Plot the median as the line or bar and show the
  spread; with three points, plot the points themselves rather than a box.
- The x axis of both panels is `rtt_ms`, the base RTT set with netem, with the five steps
  10, 30, 60, 100 and 200 ms. It is the same axis in both panels, so they share it.
- KICS is a two-column format and one column is 8.37 cm wide. Size the figure to that
  width and keep every label readable at that size; 8 pt is the floor.
- Greyscale-safe: the proceedings may be printed in black and white, so separate the four
  policies by marker and line style, not by colour alone.
- Figure text is English, matching the paper.

## Figure 2(a), top panel: detection delay (RQ1)

- y: milliseconds after t0, the moment N3 started the load.
- Two series over all `load = L1` rows: `a_ms` (metadata, the proposal) and `d_ms` (RTT
  window, the baseline). Both are recorded in every run whatever its policy, so all four
  policies' runs contribute points to both series.
- What it has to show: A below D across the whole x range. If that fails, H1 fails and
  section 1.3 of the plan says what happens next.

## Figure 2(b), bottom panel: collisions (RQ2)

- y: `n_col`, collisions per run (entries into min_range < 0.15 m, a consecutive stretch
  counting once).
- Four curves, one per `policy`, over `load = L1` rows: 1 always local, 2 always edge,
  3 RTT window, 4 metadata flag.
- What it has to show: policy 4 below policy 3, and near policy 1.
- If policy 2 turns out not to collide even at 200 ms, the y axis becomes
  `edge_stop_steps` (steps in edge mode where the safety rule would have stopped the
  robot). That column is in the file, so the swap is one line. The decision is D19, taken
  after B3 on 9/23; the loopback runs of 9/20 already showed no collisions at a steady
  200 ms, so treat the swap as likely and keep it a one-line change.

## Table 1: the timing chain

Three rows, the L1 runs at base RTT 10, 60 and 200 ms; median over the repetitions.
Columns A, B, U, C, D, G from `a_ms`, `b_ms`, `u_ms`, `c_ms`, `d_ms`, `g_ms`, then `n_sw`
for policies 3 and 4 side by side, then N_false.

- `b_ms` and `g_ms` are only filled for policy 4 runs: B is the flag's trip from N3 to N1,
  and only policy 4 sends flags. Take A, D, U, C from the same policy 4 runs so the row is
  one condition, not a mixture.
- N_false is not a column. It is `n_sw` of the `load = L2` rows, per policy: the switches a
  two-second load caused, which is the false-switch count of plan 5.3.
- U carries a footnote: it is the A3 constant (USB path p99, 3.5 ms idle), not measured per
  run.
- G = D - (A + B + U + C). Do not recompute it from the parts, print the column; the
  validator already checks the two agree.

## What is not a figure

Deadline miss rate (`m_rate`), hold rate (`hold_rate`) and the switch counts outside
table 1 go in one sentence of section III, per plan 2.1. Figure 3 was dropped for space.
