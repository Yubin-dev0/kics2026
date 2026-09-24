# What the paper's two figures and one table read

Everything on this page is drawn from one file, `data/sweep.csv`, one row per run
(columns in `data/README.md`). The sweep was run on 2026-09-23 (69 rows: 66 runs plus
3 re-runs); `data/synthetic/sweep.csv` has the same header with invented values and is
kept only for script tests.

Check any file before drawing from it:

    python3 analysis/sweep_index.py --check data/sweep.csv

Scripts live in `analysis/` and write into `analysis/out/` (git-ignored). Each script
takes the sweep file as its first argument and defaults to `data/sweep.csv`.
`analysis/c3_summary.py` draws table 1 and figure 2; `analysis/c3_checks.py` runs the
two checks quoted in `data/c3/NOTES.md`; `analysis/a0_select.py` is the A0 selection
(metric and thresholds) over the B3 captures.

## Shared rules

- Rows with `status` other than `valid` are dropped, and so are the runs listed in
  `analysis/excluded_runs.csv` (29, 42, 56; their re-runs 67-69 are in the file).
  Never quote a dropped run. Reasons are in `data/c3/NOTES.md`.
- Each condition has 3 repetitions. Plot the median as the line or bar and show the
  spread; with three points, plot the points themselves rather than a box.
- The x axis of both panels is `rtt_ms`, the base RTT set with netem, with the five steps
  10, 30, 60, 100 and 200 ms. It is the same axis in both panels, so they share it.
- KICS is a two-column format and one column is 8.37 cm wide. Size the figure to that
  width and keep every label readable at that size; 8 pt is the floor.
- Colour, but never colour alone: the proceedings may be printed in black and white, so
  the four policies (and A against D) also differ by marker and line style. The colours
  are the Okabe-Ito set, which colour-blind readers can tell apart.
- Figure text is English, matching the paper.

## Figure 2(a), top panel: detection delay (RQ1)

- y: milliseconds after t0, the moment N3 started the load.
- Two series over all `load = L1` rows: `a_ms` (metadata, the proposal) and `d_ms` (RTT
  window, the baseline). Both are recorded in every run whatever its policy, so all four
  policies' runs contribute points to both series.
- What it was meant to show: A below D across the whole x range. Measured (9/23): A is
  below D in 23 of 60 L1 runs and the median A exceeds the median D at every base RTT
  but 10 ms. H1 fails as stated; the paper reports the measured chain and the reason
  (`data/c3/NOTES.md`, "Why A is mostly later than D"). The figure is kept as it is,
  with the log y axis so the spread of A stays readable.

## Figure 2(b), bottom panel: hold rate (RQ2)

- y: `hold_rate`, the share of control steps in which the bridge held the last command
  because the edge reply was late (percent).
- Four curves, one per `policy`, over `load = L1` rows: 1 always local, 2 always edge,
  3 RTT window, 4 metadata flag.
- Why not collisions: D19 (9/23, after B3) - the course produced no collision in any
  run, policy 2 at 200 ms included (`n_col` = 0 in all 69 rows, min range 0.24-0.37 m),
  so `n_col` has nothing to plot. `edge_stop_steps` was the planned fallback and is also
  0 in every row. Option (ga) chosen by Yubin: hold rate.
- What it shows (9/23): 14-28 % per run at L1 for every policy, no policy apart; the
  four curves lie on top of each other. That is the finding, not a failure of the
  plot: the policies differ in when they switch, not in how often the edge is late.

## Table 1: the timing chain

Three rows, the L1 runs at base RTT 10, 60 and 200 ms; median over the repetitions
(`c3_summary.py` prints all five RTTs and the L2 row; pick the three for the paper).
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
