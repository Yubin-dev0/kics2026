# fw - N2 safety layer (STM32)

| path | contents |
|---|---|
| `PROTOCOL.md` | N1 <-> N2 line protocol, safety rule, switching delay definition |
| `core/` | protocol, safety rule, mode switch, application glue. Plain C11, no HAL |
| `host/` | host build of `core`: unit tests and `fake_stm32` (the core behind a tty) |
| `stm32/port/` | HAL glue for NUCLEO-F446RE (UART DMA, DWT) |
| `stm32/n2_safety/` | STM32CubeMX/CubeIDE project; `Core/Src/fw_sources.c` compiles `core` and `port` (see `stm32/README.md`) |
| `tools/` | `proto.py` (shared with the N1 bridge), `a2_bench.py`, `replay_check.py` |

## Host checks (Linux or WSL)

```
make -C fw/host test
```

Bench and replay without a board:

```
make -C fw/host fake_stm32
socat -d0 pty,raw,echo=0,link=/tmp/vboard pty,raw,echo=0,link=/tmp/vhost &
fw/host/fake_stm32 /tmp/vboard &
python3 fw/tools/a2_bench.py --port /tmp/vhost --run 1 --target fake --out /tmp/a2
```

Fake runs go to `/tmp`, not `data/`, and are never registered in `data/runs.csv`.

## Where each part runs

| work | machine | location |
|---|---|---|
| edit `fw/`, build, flash, A2 bench and replay | Windows | `C:\dev\kics2026` clone |
| host unit tests, fake board | WSL (Ubuntu-22.04) | `~/kics2026` clone |
| register runs in `data/runs.csv` | WSL | `python3 analysis/append_run.py A2 N` |

`data/runs.csv` is edited only from WSL, so the two clones never conflict on it.

## A2 pass criteria

A run passes when every phase of `a2_bench.py` passes and `replay_check.py` reports zero
mismatches on run_10 to run_12 (state against the rule and the logged mode, v against the
rule and the logged v, w against the logged w):

- paced: 1000 lines at 20 Hz, 0 lost, edge command passed through unchanged
- sweep: min_mm 0 to 600 and 65535 plus 7 local_v cap cases (609 lines), board output equals
  the reference bit for bit
- switch: 39 switches, `n_sw` advances by exactly 39, every `switch_us` below 1000
- wdog: exactly one WDOG line, 140 to 300 ms after the last reply (host-side)
- `bad_lines` does not move during the run
