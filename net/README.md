# N3 access point, netem and tunnel (A4, A6, A5)

N3 is the one place where the network is observed and changed: it is the Wi-Fi access
point that N1 (robot side) and N5 (competing load) join, it forwards to N4 (edge) over
Ethernet, it captures headers (A7) and it adds the base RTT with netem (A6). N1 and N4
never know the network condition.

Nothing is cloned onto N3. The scripts in `net/n3/` run from N1 over ssh
(`ssh yubin@<N3> 'sudo bash -s' < net/n3/<script>.sh`), so the same files set up the Pi 5 (N3 from 2026-09-22)
and the spare Pi 4 if it ever has to replace it.

## Layout

| path | contents |
|---|---|
| `n3/packages.sh` | one-time install on N3 while it has internet; prints versions (the freeze) |
| `n3/ap_up.sh` | 5 GHz AP on wlan0, NetworkManager shared mode (DHCP and NAT) |
| `n3/eth_up.sh` | eth0 fixed address towards N4, no gateway |
| `n3/netem.sh` | base RTT: half on wlan0 egress, half on eth0 egress; `off`, `show` |
| `n3/state.sh` | snapshot of board, OS, Wi-Fi, profiles, qdiscs, packages |
| `ping_run.py` | A4, A6 and A8 runs from N1: raw ping log, figures, verdict, meta file; A8 triggers the N5 load mid-run |
| `../analysis/watch_replay.py` | replays the policy 3 watcher over probe runs (A5-3b) |
| `wg/n1.conf.example`, `wg/n4.conf.example` | A5 tunnel templates (real `*.conf` files are git-ignored) |

## Bringing up N3 (done 2026-09-22 on the Pi 5)

1. First boot on N1's Internet Connection Sharing (N1 USB-C LAN to the Pi's eth0). The Pi
   took 192.168.137.224. WSL2 cannot reach the ICS subnet 192.168.137.0/24 (NAT mode), so
   in this step only the Windows ssh client is used from WSL: `ssh.exe yubin@192.168.137.224`.
2. Passwordless sudo for `yubin`, once, from an interactive login, because every script
   is piped over ssh and has no terminal for a password:
   `echo 'yubin ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/010-yubin-nopasswd`,
   `sudo chmod 440` on that file, then `sudo visudo -c` must print `parsed OK`. N3 has no
   internet after setup and serves only this testbed.
3. `packages.sh`, then `ap_up.sh` with `PSK=...` (the passphrase is kept next to the Pi,
   never in the repository).
4. N1 joins `kics-n3`; from here the WSL `ssh` works (`ssh yubin@192.168.60.1`). Then
   `eth_up.sh` over Wi-Fi (changing eth0 while logged in over it would cut the session),
   and the cable moves from N1 to N4.

## Topology (decided 2026-09-22, before the A4 verdict)

Routed with NAT: N3 is a router between its AP subnet and its Ethernet subnet, in
NetworkManager's shared mode (N3 hands out addresses on wlan0 and masquerades what leaves
eth0).

- The detector watches wlan0, where every flow keeps its real source (N1, N5). NAT only
  rewrites what leaves eth0 towards N4, which nothing measures.
- No routes have to be set by hand on the two Windows machines. N1 takes its address and
  gateway from N3; N4 sees a single neighbour, 192.168.50.1.
- Every flow of the testbed starts on the wlan0 side: N1's state datagrams and its
  WireGuard tunnel, N5's iperf3 client, N1's keepalive for the flag path. NAT never has to
  admit a flow started from N4.
- A bridge (br0) was not chosen: bridging an AP interface needs four-address handling in
  the driver, and one broadcast domain across Wi-Fi and Ethernet buys nothing that is
  measured.

| node | interface | address | set by |
|---|---|---|---|
| N3 | wlan0 (AP `kics-n3`) | 192.168.60.1/24 | `ap_up.sh` |
| N1, N5 | Wi-Fi | 192.168.60.x | N3 DHCP (shared mode) |
| N3 | eth0 | 192.168.50.1/24 | `eth_up.sh` |
| N4 | USB-LAN adapter | 192.168.50.4/24, no gateway | by hand (Windows) |
| N1 | USB-C LAN adapter | 192.168.50.11/24 for the direct A10 probe only | by hand, removed after |
| N1 | WireGuard | 10.99.0.1/24 | `wg/n1.conf` |
| N4 | WireGuard | 10.99.0.4/24, UDP 51820 | `wg/n4.conf` |

Ports: edge controller UDP 47000 on N4, flags UDP 47100 on N3, WireGuard UDP 51820 on N4.
N4 keeps the address 192.168.50.4 whether N1 reaches it by the direct cable (A10) or
through N3 (A4 onward), so the only `--edge` change left is the tunnel address at B3.

## Frozen parameters

| parameter | value | basis | status |
|---|---|---|---|
| SSID, security | kics-n3, WPA2-PSK, CCMP only | no TKIP fallback, so every client uses the same cipher | confirmed |
| band, channel | 5 GHz, channel 36 | UNII-1, non-DFS: no radar check can move the AP mid-run; allowed for APs in KR (country set at imaging) | confirmed 9/22 (A4 run 1 passed on channel 36) |
| channel width | 20 MHz (brcmfmac default, `iw dev wlan0 info`) | not settable through NetworkManager; recorded instead. N1 links at 86.6-86.7 Mbit/s (802.11ac, 1 stream); A8 sets the L1 rate against this | recorded 9/22 |
| Wi-Fi power save on N3 | off | power save batches downlink frames, which shows as RTT spikes | confirmed |
| qdisc | default during A4; netem as root qdisc on wlan0 and eth0 for every sweep step | every sweep step then has the same discipline (netem FIFO in front of the driver) instead of fq_codel managing the very queue the detector is meant to see | confirmed |
| netem limit | 10000 packets | at 200 ms each direction holds 100 ms of traffic; L1 at 50 Mbit/s of 1500-byte packets is about 4200 packets/s, so 420 packets in flight | provisional until A8 sets the L1 rate |
| software | `packages.sh` list, versions in `data/a4/n3_state_pi5.txt`: Debian 13 (trixie), kernel 6.18.50+rpt-rpi-2712, Wi-Fi firmware BCM4345/6 7.45.265, chrony 4.6.1-3+deb13u2, dnsmasq-base 2.91-1+deb13u2, ethtool 6.14.2-1, iperf3 3.18-2+deb13u2, iproute2 6.15.0-1, iw 6.9-1+b1, network-manager 1.52.1-1+rpt4, tcpdump 4.99.5-2 | nothing is installed or upgraded on N3 after the A4 verdict (65 pending upgrades left alone) | confirmed 9/22 |
| N1 Windows power | charger connected, Best performance | A3 findings | confirmed |

## Pass criteria

### A4 (access point)

| ID | criterion | basis | status |
|---|---|---|---|
| A4-1 | N1 to N3 round trip median < 5 ms over 12000 pings at 20 Hz | plan 7.3 | pass 9/22, run 1: 4.25 ms |
| A4-2 | 10 minutes with no gap between replies of 1 s or more | plan 7.3 ("10 minutes without a cut"); the 1 s is provisional. Gaps of 150 ms or more (the board watchdog) are counted and reported | pass 9/22, run 1: longest gap 0.17 s, 5 gaps of 150 ms or more |
| A4-3 | N3 state recorded with `state.sh`; N1 reports 5 GHz, channel 36 (`n1_wifi` in the meta file) | the device and settings the verdict belongs to | pass 9/22: `n3_state_pi5.txt`; `n1_wifi` of run 1 shows kics-n3, 5 GHz, channel 36, 802.11ac (its Korean labels are garbled, see Known constraints) |
| A4-4 | N5 associates and pings N1 | plan 8 | pending N5 |

Run 1 (Pi 5, 2026-09-22, Pi at 51.0 C before the run): 12000/12000 replies, median 4.25,
p99 38.1, max 128.0 ms. The tail is not spread over the run: the ten replies over 80 ms
came at 61.2-81.4 s and 379.9-404.7 s of the run, two bursts about 20 s long some five
minutes apart. A5 run 1 shows a third one (below). The cause is not isolated; see Known
constraints.

### A6 (netem)

| ID | criterion | basis | status |
|---|---|---|---|
| A6-1 | Reference run: N1 to N4 through N3, no netem, 1000 pings | the added delay is measured against it | done 9/22, run 1: median 6.965 ms |
| A6-2 | For 10, 30, 60, 100, 200 ms: median minus the reference median within +/-10% of the set value | plan 7.3 | pass 9/22, runs 2-6 (below) |
| A6-3 | Each run's meta file shows netem on both wlan0 and eth0 with half the set value | the delay is split as plan 5.1 says, not put on one side | not recorded 9/22: `tc` was not on the ssh PATH, so the meta files hold station lines only (fixed in `ping_run.py`). To be checked once with `netem.sh 60` and `netem.sh show` saved to `data/a6/netem_show_60.txt` |

| run | set | median / p99 / max (ms) | added | error |
|---|---|---|---|---|
| 1 | 0 (reference) | 6.965 / 16.1 / 24.3 | - | - |
| 2 | 10 ms | 17.1 / 26.0 / 34.8 | 10.135 ms | +1.35% |
| 3 | 30 ms | 37.1 / 45.3 / 50.1 | 30.135 ms | +0.45% |
| 4 | 60 ms | 66.7 / 76.3 / 97.8 | 59.735 ms | -0.44% |
| 5 | 100 ms | 107.0 / 115.0 / 124.0 | 100.035 ms | +0.03% |
| 6 | 200 ms | 206.0 / 210.0 / 213.0 | 199.035 ms | -0.48% |

All six runs: 1000/1000 replies, no gap of 150 ms or more, longest gap 0.063-0.072 s.
After `netem.sh off` both interfaces were back on the default fq_codel.

The 10 ms step is the tightest (+/-1 ms against the Wi-Fi jitter of A4). If it alone fails,
the tolerance is revisited with the A4 spread as evidence, not widened in advance.

### A5 (WireGuard)

| ID | criterion | basis | status |
|---|---|---|---|
| A5-1 | Tunnel up between N1 and N4, AllowedIPs the tunnel subnet only | plan 3.1: the robot flow is encrypted end to end | pass 9/22: WireGuard 1.1.1 on both, `ping 10.99.0.4` 5/5 |
| A5-2 | During a probe through the tunnel, `tcpdump` on N3 wlan0 counts UDP 51820 packets and zero on UDP 47000 | the robot flow is visible to N3 only as WireGuard headers | pass 9/22, run 1: 70 s capture, UDP 51820 2009 packets, UDP 47000 0, 0 dropped by the kernel |
| A5-3a | The probe through the tunnel meets A10-2 (1000/1000 answered, nothing bad) and every reply arrives within the 50 ms control period | a reply later than the period is a held step (B1, last-command hold); 50 ms is the 20 Hz period fixed at B1 | pass 9/22, run 2: 1000/1000, max 16.804 ms |
| A5-3b | Replaying the policy 3 watcher over the probe (`analysis/watch_replay.py`, 15 s baseline, 2 s window) never enters degraded: RTT_win - RTT_min stays under theta_high (20 ms) | with no load the base path alone must not trigger the detector, or policies 3 and 4 are compared on a false alarm; theta_high was set on 9/20 in `rttwatch.py` | pass 9/22, run 2: max excess 4.688 ms at 17.4 s |
| A5-4 | After installing WireGuard on N1, one A3 bench run (busid 2-3) meets A3-1 to A3-7 | a new network driver on N1 must not disturb the USB path | open: USB/IP to WSL dropped on 9/22 (below) |

A5-3 was revised on 2026-09-22 after run 2. As first written it asked the tunnel path to
meet A10-1 (round trip p99 < 10 ms), a bar made for the server alone on a direct cable.
Measured path by path, the tunnel adds little and the Wi-Fi hop most:

| path | median | p99 | run |
|---|---|---|---|
| direct cable | 4.145 ms | 5.031 ms | A10 run 2 |
| + Wi-Fi, N3, NAT | 6.899 ms | 9.478 ms | A10 run 3 |
| + WireGuard | 8.071 ms | 13.292 ms | A5 run 2 |

Both runs failed the old bar and stay FAIL / discarded in the ledger. The revised A5-3a
and A5-3b take their thresholds from values fixed before these data (the control period,
theta_high); no threshold was chosen from the results.

Run 1 (999/1000, p99 46.84 ms, 8 holds) fails A5-3a whatever the bar: all ~110 replies
over 20 ms came between 1.2 and 25.4 s of the run, the Wi-Fi burst seen in A4, and none
after. Its watcher replay peaks at 16.91 ms at 25.5 s, 3 ms under theta_high, with most
of the burst inside the baseline; a whole burst after t0 could cross it. That is input for
A0, which sets theta_high. Run 2 was taken right after, with no burst.

A5-4 on 9/22: the bench could not open the port. `/dev/ttyACM0` appeared after
`usbipd attach` and vanished within a minute; dmesg shows `vhci_hcd: urb->status -104`
every ~5 s, then `device descriptor read/8, error -110` and `USB disconnect`. After
repeated detach/attach, `usbipd attach` hung at "Using IP address 172.23.0.1". Changed
since A3: WireGuard on N1 (active), N1 Wi-Fi on `kics-n3` (no internet), WSL up for 6 h.
No run 7 files were written. Next: fresh WSL, A3 run 7 with the tunnel off, run 8 with it
on.

### A8 (load) and A7 (capture)

A8 runs with `ping_run.py --stage A8` and the N5 agent; criteria and parameters are in
`load/README.md`. A7 is `capture/README.md`.

## Known constraints

- N3 is the Pi 5 (received 2026-09-22); a rented Pi 4 is N5 and the other a spare. A
  verdict belongs to the device that runs the sweep: if the Pi 5 fails before C1, the card
  moves to the spare Pi 4 and A4, A6 and A5 are run again with the same scripts. From C1
  (9/25) N3 is not changed.
- While N1's Wi-Fi is on `kics-n3` it has no internet. Rejoin the school network to push.
- The N1 Wi-Fi link shows bursts of 80-130 ms round trips lasting about 20 s, a few
  minutes apart (A4 run 1 at 61 and 380 s, A5 run 1 at 1-25 s). A 45 s bridge run can
  catch one. B3 and the sweep need a rule to tell such runs apart before they are pooled.
- `n1_wifi` in the 9/22 meta files is garbled: netsh printed UTF-8 and it was read as
  CP949. The ASCII fields (SSID, BSSID, band, channel, rates) are readable; `ping_run.py`
  now tries UTF-8 first.
- WSL interop (running `powershell.exe`, `usbipd.exe` from WSL) was found off on 9/22,
  which left the Windows power facts of A10 run 2 empty. It was re-registered and made
  persistent with `/etc/binfmt.d/WSLInterop.conf`; A10 has no power criterion.
- N3 has no real-time clock and no internet after setup; its wall clock is only used
  through the per-run offset of A9.
