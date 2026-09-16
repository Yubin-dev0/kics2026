# kics2026

Testbed for detecting network degradation from encrypted traffic metadata
and switching robot controllers accordingly. KICS 2026 Fall.

## Layout
| path | contents | owner |
|---|---|---|
| `sim/` | N1 Gazebo simulation, A1 controller, world files | Yubin |
| `fw/` | STM32 safety controller firmware (A2) | Yubin |
| `capture/` | N3 packet capture and feature extraction (A7) | Hyunbin |
| `load/` | N5 competing-traffic generator (A8) | Hyunbin |
| `net/` | netem profiles, WireGuard configs | |
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
A3 complete (runs 3-6, 2026-09-17). See `fw/NOTES.md`; A2 criteria in `fw/README.md`, A1 in `sim/NOTES.md`.
