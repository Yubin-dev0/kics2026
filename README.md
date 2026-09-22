# kics2026

Testbed for detecting network degradation from encrypted traffic metadata
and switching robot controllers accordingly. KICS 2026 Fall.

## Layout
| path | contents | owner |
|---|---|---|
| `sim/` | N1 Gazebo simulation, A1 controller, world files, UART bridge (`sim/bridge/`) | Yubin |
| `fw/` | STM32 safety controller firmware (A2) | Yubin |
| `edge/` | N4 edge controller UDP server (A10), runs on the lab PC | Yubin |
| `capture/` | N3 packet capture and feature extraction (A7) | Yubin |
| `load/` | N5 competing-traffic generator (A8) | Yubin |
| `net/` | N3 access point, netem and WireGuard: scripts run on N3 over ssh, A4/A6 ping runs (start at `net/README.md`) | Yubin |
| `analysis/` | aggregation scripts, figures | |
| `data/` | run ledger and per-run logs -- start at `data/README.md` | |

## Ground rules
- Numbers live in exactly one place: `data/runs.csv` and the per-run logs.
  Do not copy figures into other documents; link to the row instead.
- Every run records its own configuration to `run_N_meta.json`. If
  `git_dirty` is true, that run cannot be reproduced from a commit.
- pcap files never enter this repository. See `.gitignore`.
- Filenames, paths, commit messages and docs are in English.

## Status
B1 complete (board runs 2-4, 2026-09-22). N4 edge link, edge controller and policies
1-4 checked on the fake board over loopback (2026-09-20); the A10 pass on the lab PC is
next. See `sim/bridge/README.md`, `edge/README.md`, `fw/NOTES.md`; A2 criteria in
`fw/README.md`, A1 in `sim/NOTES.md`.
