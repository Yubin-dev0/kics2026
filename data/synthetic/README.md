# Synthetic sweep data

`sweep.csv` here is **not a measurement**. It is 66 rows of invented numbers in the exact
schema of the real `data/sweep.csv`, so that the figure and table scripts can be written
and finished before the sweep of 2026-09-26 runs. Regenerate it with

    python3 analysis/make_synthetic.py

It is deterministic (one seed), so everyone sees the same numbers and a change in a figure
is a change in the script, never in the data.

Rules:

- No value from this file goes into the paper, the slides, or any message where it could
  be read as a result. Every row carries `git = synthetic` and a note saying so.
- The shape is real: column names, units, the 66 rows of the design (5 base RTT x 4
  policies x 3 repetitions of L1, plus 3 repetitions of L2 for policies 3 and 4), and
  which cells are empty for which policy (`b_ms` and `g_ms` only for policy 4).
- The values follow the mechanism the plan expects, so that a swapped series or a wrong
  axis shows up as an obviously wrong picture. They are not a prediction: see the header
  of `analysis/make_synthetic.py` for what the sweep is actually free to contradict.

On 9/26 the scripts point at `data/sweep.csv` instead, and nothing else changes.
