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
| `ping_run.py` | A4 and A6 runs from N1: raw ping log, figures, verdict, meta file |
| `wg/n1.conf.example`, `wg/n4.conf.example` | A5 tunnel templates (real `*.conf` files are git-ignored) |

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
| band, channel | 5 GHz, channel 36 | UNII-1, non-DFS: no radar check can move the AP mid-run; allowed for APs in KR (country set at imaging) | provisional until A4 (a crowded channel shows as an A4 failure; then 40, 44, 48) |
| channel width | as brcmfmac sets it, read with `iw dev wlan0 info` | not settable through NetworkManager; recorded instead | recorded at A4 |
| Wi-Fi power save on N3 | off | power save batches downlink frames, which shows as RTT spikes | confirmed |
| qdisc | default during A4; netem as root qdisc on wlan0 and eth0 for every sweep step | every sweep step then has the same discipline (netem FIFO in front of the driver) instead of fq_codel managing the very queue the detector is meant to see | confirmed |
| netem limit | 10000 packets | at 200 ms each direction holds 100 ms of traffic; L1 at 50 Mbit/s of 1500-byte packets is about 4200 packets/s, so 420 packets in flight | provisional until A8 sets the L1 rate |
| software | `packages.sh` list, versions in `data/a4/n3_state_<device>.txt` | nothing is installed or upgraded on N3 after the A4 verdict | confirmed |
| N1 Windows power | charger connected, Best performance | A3 findings | confirmed |

## Pass criteria

### A4 (access point)

| ID | criterion | basis | status |
|---|---|---|---|
| A4-1 | N1 to N3 round trip median < 5 ms over 12000 pings at 20 Hz | plan 7.3 | provisional |
| A4-2 | 10 minutes with no gap between replies of 1 s or more | plan 7.3 ("10 minutes without a cut"); the 1 s is provisional. Gaps of 150 ms or more (the board watchdog) are counted and reported | provisional |
| A4-3 | N3 state recorded with `state.sh`; N1 reports 5 GHz, channel 36 (`n1_wifi` in the meta file) | the device and settings the verdict belongs to | provisional |
| A4-4 | N5 associates and pings N1 | plan 8 | pending N5 |

### A6 (netem)

| ID | criterion | basis | status |
|---|---|---|---|
| A6-1 | Reference run: N1 to N4 through N3, no netem, 1000 pings | the added delay is measured against it | provisional |
| A6-2 | For 10, 30, 60, 100, 200 ms: median minus the reference median within +/-10% of the set value | plan 7.3 | provisional |
| A6-3 | Each run's meta file shows netem on both wlan0 and eth0 with half the set value | the delay is split as plan 5.1 says, not put on one side | provisional |

The 10 ms step is the tightest (+/-1 ms against the Wi-Fi jitter of A4). If it alone fails,
the tolerance is revisited with the A4 spread as evidence, not widened in advance.

### A5 (WireGuard)

| ID | criterion | basis | status |
|---|---|---|---|
| A5-1 | Tunnel up between N1 and N4, AllowedIPs the tunnel subnet only | plan 3.1: the robot flow is encrypted end to end | provisional |
| A5-2 | During a probe through the tunnel, `tcpdump` on N3 wlan0 counts UDP 51820 packets and zero on UDP 47000 | the robot flow is visible to N3 only as WireGuard headers | provisional |
| A5-3 | The probe through the tunnel (stage A5) meets A10-1 and A10-2 | the tunnel must not cost the edge link its bound | provisional |
| A5-4 | After installing WireGuard on N1, one A3 bench run (busid 2-3) meets A3-1 to A3-7 | a new network driver on N1 must not disturb the USB path | provisional |

## Known constraints

- N3 is the Pi 5 (received 2026-09-22); a rented Pi 4 is N5 and the other a spare. A
  verdict belongs to the device that runs the sweep: if the Pi 5 fails before C1, the card
  moves to the spare Pi 4 and A4, A6 and A5 are run again with the same scripts. From C1
  (9/25) N3 is not changed.
- While N1's Wi-Fi is on `kics-n3` it has no internet. Rejoin the school network to push.
- N3 has no real-time clock and no internet after setup; its wall clock is only used
  through the per-run offset of A9.
