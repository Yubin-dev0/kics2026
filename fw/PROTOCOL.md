# N1 <-> N2 line protocol (version 2)

N1 (laptop, UART bridge) and N2 (NUCLEO-F446RE, safety layer) exchange one ASCII line per
control step over the ST-LINK virtual COM port. N1 sends an S line for every `/scan`
message (20 Hz); N2 answers each valid S line with exactly one C line. N2 also sends one
unsolicited C line when S lines stop arriving (watchdog).

ASCII was chosen over a binary frame on purpose: lines are readable with any serial
terminal, the fake board is trivial, and logs paste straight into CSV. Parsing-time jitter
is a few microseconds, three orders of magnitude below the detection delays (A, D) that the
switching delay C is compared against.

## Physical layer

| item | value |
|---|---|
| link | USB cable, ST-LINK V2-1 virtual COM port -> USART2 (PA2/PA3) |
| format | 921600 bps, 8N1, no flow control |
| line end | `\n` (a preceding `\r` is ignored) |
| checksum | `*XX` before `\n`: XOR of every byte from the first character up to, not including, `*`, as two hex digits |
| max line | 96 bytes without `\n` |

## S line (N1 -> N2)

```
S,<seq>,<min_mm>,<local_v>,<local_w>,<edge_v>,<edge_w>,<flag>*XX
```

| field | type | unit | meaning |
|---|---|---|---|
| seq | u32 | - | control step number, shared with the UDP state and the logs |
| min_mm | u16 | mm | minimum over the 16 front sectors (+/-48 deg), sensor-referenced. 0 = every ray invalid (fail-safe STOP), 65535 = no return (far) |
| local_v | i16 | mm/s | speed cap from the N1 waypoint follower: 220, or 0 while it turns in place (heading error > 0.4 rad). Negative values are treated as 0 |
| local_w | i16 | mrad/s | angular rate from the N1 waypoint follower, used in local mode in every state |
| edge_v | i16 | mm/s | linear speed from the edge controller (N4) |
| edge_w | i16 | mrad/s | angular rate from the edge controller |
| flag | 0/1 | - | requested mode: 0 = edge, 1 = local |

`flag` is a level, not an event. Every line carries the mode N1 wants; N2 switches only when
it differs from the current mode. A corrupted line therefore delays a switch by one line
instead of losing it. When the requested mode changes, N1 sends an extra S line immediately
(reusing the latest sensor values) instead of waiting for the next `/scan`, so the up to
50 ms wait does not leak into the flag delivery time B.

N1 converts ranges with `floor(r * 1000)`. Truncation only ever shortens a distance, so every
conversion error is on the safe side.

## C line (N2 -> N1)

```
C,<seq>,<v_out>,<w_out>,<mode>,<state>,<switch_us>,<n_sw>,<bad_lines>*XX
```

| field | type | unit | meaning |
|---|---|---|---|
| seq | u32 | - | echo of the S line (last received seq on a watchdog line) |
| v_out | i16 | mm/s | final linear command applied to the robot |
| w_out | i16 | mrad/s | final angular command |
| mode | 0/1 | - | mode after handling this line |
| state | 0..3 | - | safety rule result: 0 RUN, 1 SLOW, 2 STOP, 3 WDOG. Reported in both modes, applied only in local mode |
| switch_us | u32 | us | switching delay C if this line changed the mode, otherwise 0 |
| n_sw | u16 | - | mode changes since reset |
| bad_lines | u16 | - | lines rejected since reset (format, checksum, range, overlong, queue full, UART error) |

Command selection: edge mode outputs `edge_v, edge_w`; local mode outputs
`min(safety speed, max(local_v, 0))` and `local_w`. This is exactly what the A1 controller
does: it takes the safety speed, sets v to 0 while turning in place, and keeps commanding w
even in STOP (replay of run_7: both STOP rows carry a nonzero w). Version 1 lacked `local_v`,
so a board in local mode would have driven forward through waypoint turns.

## V line (version query)

N1 sends `V*56`; N2 answers `V,<proto_version>,<build_date_time> <dbg|rel>,<sysclk_hz>*XX`. The bench
records the answer in each run's meta file.

## Safety rule (local mode)

Integer port of the rule validated in A1 (run_10 to run_12). `r` is `min_mm`. The table
gives the safety speed; `v_out` is this value capped by `local_v`.

| condition | state | v_out (mm/s) |
|---|---|---|
| r >= 400 | RUN | 220 |
| 200 <= r < 400 | SLOW | max(220 * (r - 200) / 200, 100), integer division |
| r < 200 | STOP | 0 |

Against the float rule used in simulation, the board is never faster and at most 2.1 mm/s
slower: range truncation costs up to 1 mm, which is 1.1 mm/s on the SLOW slope, and integer
division costs up to 1 mm/s. The state never differs, because the thresholds are whole
millimetres.

## Switching delay C

`switch_us` runs from the moment the UART receive event that completes the line fires
(DWT CYCCNT read at the top of `HAL_UARTEx_RxEventCallback`) to the moment the new mode is
written. It includes queueing to the main loop and parsing. It excludes one idle-line
detection time (one character, 10.9 us at 921600), which is constant.
Resolution is 1 us (180 cycles at 180 MHz). A switching line never reports 0; the smallest
value is 1.

## Watchdog

If no valid S line arrives for more than 150 ms, N2 sends one C line with `state = 3`,
`v_out = 0`, `w_out = 0` and the last seq, and N1 stops the robot. The next valid S line
clears the condition.

## Numbers and their basis

| value | number | basis | status |
|---|---|---|---|
| d_stop | 200 mm | d_col 150 mm + delay distance 33 mm, rounded up (A1) | confirmed |
| d_slow | 400 mm | run_6 to run_12: SLOW reached every run, min_range 262 to 277 mm, no collision | confirmed |
| v_max | 220 mm/s | TurtleBot3 Burger maximum linear speed | confirmed |
| SLOW floor | 100 mm/s | A1 run_4/run_5: without a floor the robot crawls near d_stop and times out | confirmed |
| baud | 921600 | worst-case 52 byte S + 57 byte C line (with `\n`) = 1.2 ms on the wire per step; ST-LINK V2-1 stability checked in A2 (V5) | provisional until a2 passes |
| watchdog | 150 ms | 3 missed 20 Hz lines; 0.22 m/s * 150 ms = 33 mm < d_stop - d_col = 50 mm | provisional |
| switch_us pass bar | < 1000 us | keeps C at least two orders below B (a few ms) and far below A, D (tens to hundreds of ms) | provisional |
| max line | 96 bytes | longest legal S line is 51 bytes, C line 56; margin for later fields | design |

## Known constraints

- There is no separate 100 Hz safety loop. N2 decides once per S line, so its decision rate
  equals the sensor rate (20 Hz). This settles D12: the "100 Hz UART round trip" check is
  replaced by "every 20 Hz line answered, plus immediate lines on flag changes".
- The host-side round trip measured by the bench includes USB and OS scheduling; it is
  recorded but carries no pass bar. C is measured on the board only.
- Watchdog stops the robot only through N1. In this HIL setup that covers a stalled
  simulator or bridge thread, not a dead N1.
