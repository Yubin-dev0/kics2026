# fw notes - N1 to N2 serial link

## Scope

A3 checks that a process inside WSL2 can talk to the N2 board with the same line
integrity as the Windows host in A2, and measures what the WSL2 path costs. The B1 UART
bridge runs as a ROS 2 node inside WSL2, so this is the path every B and C run uses.
Firmware, bench and board are the same as in A2; only the host path changes.

## Test conditions (A3, 2026-09-17)

| Item | Value |
|---|---|
| Host OS | Windows 11 10.0.26200.9457 |
| WSL | 2.7.14.0, kernel 6.18.33.2-microsoft-standard-WSL2, Ubuntu 22.04.5, init systemd, networking mode nat |
| USB passthrough | usbipd-win 5.3.0, busid 1-3, full-speed device on vhci_hcd |
| Kernel drivers | CONFIG_USBIP_VHCI_HCD=m, CONFIG_USB_ACM=m (both load on attach) |
| Debug probe | ST-LINK/V2-1, firmware V2J46M33 (build May 26 2025) |
| Board | NUCLEO-F446RE, serial 0669FF485775495067204114, firmware proto v2, build_id "Sep 16 2026 18:11:48 dbg" (source 35d6b8b) |
| Port in WSL2 | /dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_0669FF485775495067204114-if02 -> /dev/ttyACM0, root:dialout 660 (udev) |
| Bench | fw/tools/a2_bench.py at 5d90341, 921600 8N1 |
| Other tools | usbutils 1:014-1build1, Git for Windows 2.55.0.windows.3 |

## Setup (once per machine)

- Windows, admin shell: `winget install usbipd`, then `usbipd bind --busid 1-3`.
- WSL2: `sudo usermod -aG dialout $USER`, then `wsl --shutdown` from Windows.
- Every session, normal Windows shell with a WSL2 window open: `usbipd attach --wsl --busid 1-3`.
- Before flashing from CubeIDE: `usbipd detach --busid 1-3`.

## A3 pass criteria

| ID | Criterion | Basis | Status |
|---|---|---|---|
| A3-1 | Bench reports proto 2 and build_id "Sep 16 2026 18:11:48 dbg" | Removes every variable except the host path | confirmed |
| A3-2 | Per run: lost 0/2009, bad_lines_delta 0 in every phase, sweep mismatches 0 | Same as A2 V5 | confirmed |
| A3-3 | switches 39 = n_sw delta 39 | Same as A2 | confirmed |
| A3-4 | A3-1 to A3-3 and A3-5 to A3-7 hold for 3 consecutive runs | A2 V5 streak rule | confirmed |
| A3-5 | switch_us median within 31-35 us | A2 range over 117 switches; C is timed on the board, so the host path must not move it | confirmed |
| A3-6 | Watchdog gap 145-152 ms | Board counts 150 ms; the bench times it after the USB path. A2 gave 147.9-148.1 | provisional, too narrow (see Findings) |
| A3-7 | Paced 1000 lines: host round trip p99 < 5 ms | The link sits inside the B1 loop, whose pass criterion is loop jitter < 5 ms; 10% of the 50 ms control period | provisional, tied to B1 |
| A3-8 | One run with headless Gazebo running: A3-2 and A3-7 hold | B1 runs Gazebo in the same VM | provisional |

The team plan asked for "the same round trip as A2". Two distributions are never equal,
and the A2 figure of 2 ms had no basis, so A3-7 replaces it with a bound derived from B1.

## Validation

Round trip in ms (paced phase, 1000 lines). C in us. All eight runs: lost 0/2009,
bad +0, sweep 0/609, switches 39/39, git 5d90341 clean.

| Run | Path | Power mode | Median | p99 | Max | Min | C median (min-max) | Wdog ms | A3 verdict |
|---|---|---|---|---|---|---|---|---|---|
| 1 | WSL2 usbipd | balanced | 3.206 | 8.904 | 15.603 | 1.612 | 34 (32-35) | 147.9 | fails A3-7 |
| 2 | WSL2 usbipd | balanced | 2.738 | 8.047 | 11.888 | 1.574 | 34 (32-35) | 147.1 | fails A3-7 |
| 101 | Windows COM3 | balanced | 2.474 | 4.676 | 15.086 | 1.156 | 34 (32-35) | 149.1 | control |
| 102 | Windows COM3 | best performance | 1.548 | 2.744 | 3.147 | 1.064 | 34 (31-35) | 147.7 | control |
| 3 | WSL2 usbipd | best performance | 2.290 | 3.510 | 5.497 | 1.493 | 34 (33-35) | 148.3 | pass, streak 1/3 |
| 4 | WSL2 usbipd | best performance | 2.306 | 3.623 | 4.980 | 1.400 | 34 (32-35) | 145.6 | pass, streak 2/3 |
| 5 | WSL2 usbipd | best performance | 2.279 | 3.559 | 4.577 | 1.380 | 34 (32-35) | 149.0 | pass, streak 3/3 |
| 6 | WSL2 usbipd + Gazebo | best performance | 2.055 | 2.836 | 3.288 | 1.362 | 34 (33-37) | 152.1 | pass (A3-8) |

Reference, A2 Windows COM3 on 2026-09-16 (runs 3-5): median 1.81-1.92, p99 2.3-2.8,
max about 3.0, C 34 (31-35), wdog 147.9-148.1.

Result: A3 passes on runs 3-5 (A3-1 to A3-7) and run 6 (A3-8), with the power mode set
to Best performance.

## Findings

- The Windows power mode sets the tail, not the USB path. In Balanced mode both paths
  reached a 15 ms maximum and the Windows control itself had p99 4.68 ms. In Best
  performance the Windows path returned to its A2 level. Deep CPU idle states are the
  likely cause; this was inferred from the mode switch, not measured directly.
- Cost of the WSL2 path, same day, Best performance, runs 3-5 against run 102: median
  +0.73 to +0.76 ms, p99 +0.77 to +0.88 ms, min +0.32 to +0.43 ms, max +1.4 to +2.4 ms.
  usbipd adds a steady sub-millisecond delay and occasional spikes of a few ms.
- Gazebo load shortened the tail (run 6, p99 2.84 ms). Busy cores probably stay out of
  deep idle, so the idle-host figures are the conservative ones for B1.
- C is unchanged by the path: median 34 us in all eight runs. Run 6 reached 37 us, the
  first value above the A2 range; host load may change how bytes arrive in the DMA
  buffer. This is three orders of magnitude below A, B and D.
- The watchdog gap spreads with the path: 145.6-152.1 ms on WSL2 against 147.7-149.1 ms
  on Windows. The gap is the board's 150 ms plus or minus the host path delay, so A3-6
  should read 150 ms +/- the path maximum (about 5.5 ms on WSL2) in later stages. Run 6
  (152.1 ms) is outside the provisional range; the verdict was not changed after the fact.

## Use in the timing chain

B ends at t_flag_rx, just before the UART write on N1, and C starts when the board
receives the line. The time in between, called U here, is in neither term. U is
one-way and cannot be timed directly, but it cannot exceed the round trip of the same
line. Proposal (to confirm with the RQ1 owner): report G = D - (A + B + U + C) next to
the original G, with U bounded per flag line by that line's round trip, logged by the
B1 bridge as t_c_rx - t_uart_tx. With the p99 above (2.8-3.6 ms), U can only change a
conclusion when D - A is a few ms.

## Pre-run checklist (every N1 run from B1 on)

- Charger connected; Windows power mode Best performance for both plugged in and on battery.
- `usbipd list` shows 1-3 Attached; the by-id path exists in WSL2.
- Bench or bridge reports the expected build_id.
- No other process holds the port (`sudo fuser -v /dev/ttyACM0`).

## Known constraints

- Attach is lost on `wsl --shutdown`, sleep, USB re-plug or any USB re-enumeration.
  Re-attach before a run. `usbipd attach --wsl --busid 1-3 --auto-attach` keeps it
  attached for long unattended runs.
- While attached, Windows (CubeIDE, COM3, the NOD_F446RE drive) cannot see the board.
- WSL2 runs in NAT mode, so N3 cannot open UDP towards WSL2. The C1 flag path must be
  opened from N1 (keepalive to N3, flags returned on that socket). Decided on 2026-09-17,
  implemented in B1.
- `usbipd` is only on PATH in shells opened after installation.
- socat 1.7.4 on this host rejects `-d0`; use `socat pty,raw,echo=0,link=/tmp/vboard pty,raw,echo=0,link=/tmp/vhost`.
- The board's mass-storage interface appears in WSL2 as a SCSI disk (/dev/sdX); it is
  not mounted and has no effect. "vhci_device speed not set" in dmesg is harmless.
- ModemManager is inactive on this host. If it runs, it writes AT commands to new
  ttyACM ports and raises bad_lines.
