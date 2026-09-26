# C5 notes (2026-09-26): the switching flag in its own queue

C3 left B (flag send on N3 to the S line leaving N1) at 69-367 ms because the flag
crosses the same wlan0 egress as the L1 load (plan D5). C5 measures what that choice
cost: policy 4 only, L1, MET = p90 20/10, RATE 84M, the C3 netem values, and one change
on N3 wlan0 made by `net/n3/prio.sh`:

- V1: root prio (3 bands), every packet in band 1, the flag (UDP sport 47100) in band 0
  by a u32 filter; each band carries the C3 netem (same one-way delay and limit).
  eth0 unchanged.
- V2: V1 plus DSCP CS6 on the flag (nft rule), so the Wi-Fi driver puts it in the
  voice access category instead of best effort with the load.

Runs 1-15 between 11:37 and 12:31, three per condition, by hand with three shell
helpers (d = qdisc + flag capture + detector, r = clock + bridge, f = fetch + register).
`data/sweep_c5.csv` has all 15 rows; `analysis/c5_compare.py` prints everything below.

Per run this stage also keeps `flagcap_run_N.txt` (tcpdump `-tt` of the flag leaving
wlan0, N3 wall clock) and `qdisc_run_N.txt` (`prio.sh show` after the run, so the
band-0 packet count of that run is readable: the qdisc is deleted and rebuilt before
every run, which resets the counters).

## Clock

clock run 0 (before any qdisc, 11:36): delta = -322317300.898 ms, sd 0.191 ms, round
trip 1.1 ms. The Pi was rebooted since 9/23, so delta is a new value. Per-run deltas
drift by about 0.5 ms/min as before.

The per-run offsets are taken with netem on N3's egress only, so the NTP estimate is
biased by about half the one-way delay and B reads low by about h/2 (5 ms at 60 ms).
C3 carries the same bias, so differences C3 - C5 are unaffected. V2 shows it directly:
B 21-23 ms against a flag that provably spent 30.0 ms in the netem of band 0.

## Runs excluded from analysis

`analysis/excluded_runs.csv` (stage column c5) carries the same list.

| run | sweep_c5.csv status | why | re-run |
|---|---|---|---|
| 5 | valid, no t0 | detector timed out before the bridge started (commands typed by hand from the v1 guide); no L1: edge RTT median 67 ms, holds 2/916, flags 0. runs.csv says discarded | 13 |
| 13 | valid, no t0 | same: edge RTT median 67 ms, hold rate 2.6 %, flags 0 | 14 |
| 14 | valid | detector entered 4.1 s before t0 (A = -4100 ms), Wi-Fi burst rule; B 36.2 ms is a no-load reference for the flag path (h 30 + 6 ms) | 15 |

Run 15 (RTT 60, V1, rep 2) is the third valid run of that condition; runs 13-15 all
carry `--rep 2`. Used: 1-4, 6-12, 15.

## Verdicts (0926 guide 2.3, fixed before the data)

- C5-1 band 0 = flags sent, every used run: pass (12/12; 1-5 flags per run).
- C5-2 B median <= one-way delay + 40 ms: V1 fails at every RTT (92 / 267 / 226 ms
  against 45 / 70 / 140); V2 passes at 60 ms (22 ms against 70).
- C5-3 (memo) A within the C3 range of the same RTT in 12/12 runs; D in 9/12 (run 4
  is 70 ms below the C3 minimum at 60 ms; runs 6 and 9 are 13-15 ms above the maximum). The load
  side behaves as in C3: edge RTT median 345-348 ms at 10 ms, 396-398 at 60, 545-548 at
  200 (C3: 345, 397, 544-546).
- C5-4 G > 0: V1 1/3, 1/3, 0/3 (C3 policy 4: 0/3, 0/3, 1/3); V2 2/3 at 60 ms.
- V2 gate: V1 B median at 60 ms 267 - 30 = 237 > 30 -> V2 run (runs 10-12).

## What the 12 used runs show (analysis/c5_compare.py)

B median, C3 policy 4 against C5, ms:

| base RTT | C3 | V1 | V2 | first flag's wait in the qdisc (V1 / V2) |
|---|---|---|---|---|
| 10 | 113 | 92 | - | 5.0 = netem delay |
| 60 | 159 | 267 | 22 | 30.0 = netem delay (V1 and V2) |
| 200 | 155 | 226 | - | 100.0 = netem delay |

- The prio band worked as a qdisc: the first flag of every run left N3's qdisc after
  exactly the netem delay (5.0 / 30.0 / 100.0 ms), and the band-0 counter matched the
  flags sent. Later flags of the same run waited 7-33 ms more than the delay (one
  132.7 ms at 200 ms); the likely cause is the driver holding the whole qdisc stopped
  while its own queue is full, which band 0 cannot bypass either (not checked).
- V1 did not shrink B. Subtracting the qdisc wait and the bias, 72-252 ms of B are
  spent below the qdisc: in the Wi-Fi driver/firmware queue, on the air and in N1's
  bridge. That is the queue the A8 fit called the ~250 ms floor (`load/README.md`),
  and tc cannot bound it. So the flag was never mainly waiting in netem; the C3 B was
  the driver queue, and the 9/26 guide's prediction (B down to h + 10-20 ms with V1)
  was wrong.
- V2 removes almost all of it: B 21-23 ms in all three runs, 6-8 ms below the qdisc
  after the bias correction. Marking the flag CS6 moves it to the voice access
  category of 802.11e, a separate hardware queue with its own contention parameters,
  so it no longer waits behind the best-effort load in the driver.
- G follows B but stays mostly negative because A does not change: V2 at 60 ms gives
  G = -456 / +250 / +48 (2 of 3 positive) where C3 had -948 / -300 / -324. What is left
  of the bound is the detection delay A (740-1420 ms at 60 ms), as C3 found.
- Hold rate 21-28 % at L1 in every used run (C3: 7-28 %), collisions 0, min range
  0.254-0.279 m, C 33-37 us per switch. Pi 5 temperature 54.3-55.4 C.

## Known constraints

- V2 only at 60 ms (3 runs), by the gate rule; 10 and 200 ms have no V2 data.
- Marking DSCP on the flag is a change to the flag path only; the load and the tunnel
  keep best effort. Whether an AP would honour CS6 from an untrusted sender is a
  deployment question, not answered here.
- Flag capture and qdisc counters are missing for run 4 (started before the helpers
  existed); its B (379 ms) is used, its qdisc wait is not.
