# N3 capture and metadata detector (A7)

The detector is the proposed method (policy 4): it watches the robot's encrypted flow on
N3's wlan0 and decides, from packet headers alone, that the access point is congesting.
It runs on N3 in every run of the sweep, whatever the policy (plan v5 5.3: A comes from
every run), and only policy 4 acts on its flag. The same process marks the run start,
starts the N5 load at t0 and stamps the flag, so t0, t_det_meta and the flag share N3's
clock and A = t_det_meta - t0 needs no offset (plan v5 4.5).

## Layout

| path | contents |
|---|---|
| `detector.py` | the detector: tcpdump front end, window features, decision rule, flag sender, t0 trigger; offline replay of a pcap, a tcpdump text log or a windows file |
| `n3_detector.sh` | runs it on N3 over ssh for one run (nothing is cloned onto N3) |
| `fetch.sh` | copies a run's windows, flags and meta files from N3 into `data/<stage>/` |
| `test_detector.py` | self-check with a synthetic 20 Hz flow: no load, L1, L2, pcap round trip, replay |

Files of run N (on N3 in `/home/yubin/n3runs/<stage>/`, then `data/<stage>/`):

| file | contents |
|---|---|
| `n3_run_N.pcap` | `tcpdump -i wlan0 -s 96 udp port 51820`: WireGuard headers only, stays on N3 (git-ignored) |
| `n3_run_N_windows.csv` | one row per 100 ms step, columns below |
| `n3_run_N_flags.csv` | one row per flag sent: `fseq, degrade, t_det_ns, t_send_ns, peer, step` |
| `n3_run_N_meta.json` | parameters, baseline, packet counts, `t_run_start_ns`, `t0_ns`, `t_det_meta_ns`, `a_ms`, the N5 agent's replies, tcpdump's own statistics (`packets dropped by kernel` is the A8 server-placement check) |

## What one run looks like

```
N1  python3 -m bridge.node --run 3 --stage c1 --policy 4 --edge 10.99.0.4 --n3 192.168.60.1 ...
N1  capture/n3_detector.sh 3 c1 --load L1 --n5 192.168.60.20      (started before the bridge)
N5  ssh yubin@<N5> 'python3 -' < load/n5_agent.py                  (once, stays up)
N1  capture/fetch.sh 3 c1                                          (after the run)
```

1. `tcpdump` starts on wlan0 with `--print -w`: the pcap is written and each packet line is
   parsed live (epoch time, addresses, ports, UDP length).
2. The bridge's first keepalive (`K,n` to UDP 47100, `sim/bridge/netio.py`; sent by every
   run given `--n3`, whatever the policy) marks the run start on N3's clock. With `--load`,
   the trigger to the N5 agent leaves `--t0-s` (17 s) later and that instant is `t0_ns`.
3. Every `step_s` the features of the last `window_s` are computed (next section), the
   rule decides, and on an entry the flag `F,fseq,1,t_det_ns` goes to the keepalive's
   source address (WSL2 NAT: N3 cannot open a flow to N1). `t_det_ns` is taken right
   before `sendto`; B on N1 starts from it.
4. After `--seconds` (default 90) tcpdump is stopped, the N5 agent gets a stop, and the
   three files are written. SIGTERM and SIGHUP (the ssh session dropping) also finish the
   files.

## Window columns (`n3_run_N_windows.csv`)

A window holds the packets of the last `window_s` seconds at the end of a step; `up` is
the wlan0 side to N4 (source in `--ap-net`), `down` the replies.

| column | unit | meaning |
|---|---|---|
| step, t_ns, elapsed_s | -, ns, s | step number, its end on N3's wall clock, seconds since the first robot packet |
| n_up, n_down, bytes_up, bytes_down | -, B | packets and UDP payload bytes per direction in the window |
| iat_med_*_ms, iat_p90_*_ms, iat_max_*_ms | ms | median, 90th percentile (nearest rank) and maximum of the intervals between consecutive packets of that direction (plan: IAT) |
| iat_mad_*_ms | ms | mean absolute deviation of those intervals from their median (jitter) |
| burst_* | - | longest run of consecutive intervals under `burst_eps_ms` (plan: burst_len) |
| updown_ratio | - | bytes_up / bytes_down (plan: updown_ratio) |
| pair_n, pair_med_ms, pair_min_ms, pair_max_ms | -, ms | request/reply pairs: a reply's time on wlan0 egress minus its request's time on wlan0 ingress, i.e. the Ethernet round trip to N4 plus the wait in N3's own wlan0 queue (the queue the load fills). Pairs are made in order; WireGuard control packets (32, 92, 148 bytes) are skipped, and a request unanswered for `PAIR_MAX_S` is dropped |
| q_up_ms, q_down_ms, q_pair_ms | ms | the chosen `--metric` of that direction (or of the pairs) minus its no-load baseline |
| q_hat_ms | ms | the value the rule looks at: q_pair with `--metric pair`, otherwise q_up / q_down / the larger of the two (`--dir`) |
| q_min_ms | ms | smallest q_hat over the last `interval_s` (RFC 8289 interval minimum) |
| baseline_done | - | 1 once the first `baseline_s` of the flow are over; decisions start here |
| degraded | - | the rule's state after this step |
| t_det_meta_ns | ns | the flag's `t_det_ns`, on the row of the step that entered |

Empty cells: a direction with fewer than two packets in the window has no interval; no
pair in the window; before the baseline. Empty means not computable, not zero.

## Rule and parameters

| parameter | value | basis | status |
|---|---|---|---|
| window_s | 1 s | plan v3 6.2 step 1 (W_f) | confirmed (plan) |
| step_s | 0.1 s | plan v3 6.2, sliding 100 ms | confirmed (plan) |
| interval_s | 0.1 s | RFC 8289 default interval (plan v3 6.2 step 2); `test_detector.py` shows 3 s rejects a 2.5 s L2 | provisional, A0 |
| baseline_s | 5 s | plan v3 8.3: no-load reference is the first 5 s of the flow | provisional, A0 |
| theta_high / theta_low | 20 / 10 ms | the same provisional pair as `sim/bridge/rttwatch.py` on N1 (plan: one pair for policies 3 and 4) | provisional, A0 |
| metric | med | plan v3 6.2: median packet interval of the window minus the baseline. See the limit below | provisional, A0 (D4) |
| dir | both | the larger excess of the two directions; A0 may fix one | provisional, A0 |
| burst_eps_ms | 5 ms | intervals under this are one burst; recorded only | provisional |
| PAIR_MAX_S | 1.5 s | an unanswered request older than this is dropped from pairing; 30 periods of 50 ms | provisional |
| t0_s | 17 s | plan v5 4.1 puts t0 at 15 s of the run. The keepalive that marks the run start on N3 begins before the bridge's settle line and first scan, 1-3 s ahead of N1's t = 0, so 15 s after the keepalive could fall inside N1's 15 s RTT baseline, during which the watcher cannot enter and D would be inflated. 17 s keeps t0 past it; `sweep_index.py` prints t0 on N1's clock for every run as the check | provisional: t0 is also a design choice against the corner arrival times (decision list of 9/22) |
| lag_ms | 30 ms | live only: a step is evaluated this long after its end so that tcpdump lines still in the pipe land in it | provisional |

Decision, after the baseline: `degraded` enters when q_min > theta_high and leaves when
q_min < theta_low (double threshold, TCP Vegas); the first entry stamps `t_det_meta_ns`.
A window without packets decides nothing: on N3 an empty window almost always means the
run is over, unlike the RTT watcher on N1 where it means the edge stopped answering.

Limit of the interval median, to be weighed at A0 with real captures: a packet interval
changes only while the queue delay is changing. A queue that grows by 150 ms over 3 s
shifts the intervals of the 20 Hz flow by 150/3000 x 50 = 2.5 ms and then, once the delay
holds, the intervals are 50 ms again. `test_detector.py` case 2 shows the median never
entering on such a ramp while `pair` (the reply's wait in N3's queue, measured directly)
enters 1.0 s after t0. p90 and mad respond to the jitter that comes with a full queue.
Every window records all of them, so A0 replays the first L1 captures with
`--replay-windows ... --metric {med,p90,mad,pair}` and picks the metric and thresholds
before C1; the sweep then runs one fixed choice. Whatever is chosen is written down here.

## Pass criteria (A7, plan v5 7.3)

| ID | criterion | basis | status |
|---|---|---|---|
| A7-1 | a 60 s pcap is replayed in under 5 s (`--replay`) | plan: "60 s pcap processed within 5 s" | synthetic 2400 packets in 0.04 s on the sandbox; to be timed once on N3 |
| A7-2 | no-load run: after the baseline, q_hat stays within 10% of the 50 ms period (`abs(q_hat) < 5 ms` for med) and the detector never enters | plan: "no-load figures stable, window to window < 10%"; A5-3b is the same check for the RTT watcher | pending: first B3 run with the detector observing |
| A7-3 | tcpdump reports 0 packets dropped by the kernel over the run | the capture must be complete for pairing and intervals | pending |
| A7-4 | the N1 keepalive is seen, the flag reaches N1 (`flags` in the N1 meta) and `t_det_meta_ns` in N1's flag line equals `t_det_ns` in `n3_run_N_flags.csv` | C1 flag path; the same number on both sides is what B is computed from | pending C1 |

## Validation

- `test_detector.py` (sandbox, 2026-09-22): synthetic flow, all cases pass. No load: baseline
  49.99 / 49.99 ms, max abs q_hat 0.41 ms, no entry, 600 steps. L1 (150 ms ramp over 3 s
  then +/-20 ms): pair enters once, A = 1000 ms; med never enters; p90 enters 6 times.
  L2 (2.5 s bump): pair enters and leaves once with the 0.1 s interval, never with 3 s.
- Live path on loopback (sandbox, real tcpdump 4.99.4 with `--print -w`, real
  `n5_agent.py` with a stand-in iperf3): keepalive marked the run start, t0 fired 4.003 s
  after it as asked, the agent acknowledged in 0.4 ms, a 40 ms echo delay entered the
  pair rule 1.7 s after t0 and the flag reached the fake N1; the leave flag followed when
  the delay was removed. 0 packets dropped by the kernel.

## Known constraints

- Nothing is installed on N3 beyond the A4 freeze; the detector is standard library only
  and is piped over ssh (`n3_detector.sh`). It needs `sudo` for tcpdump (NOPASSWD is set).
- tcpdump 4.99.5 on N3 supports `--print` with `-w`; if it ever does not, `--no-pcap`
  keeps the live decision and drops the pcap.
- The detector pairs requests and replies by order. It relies on the WireGuard payload
  sizes of the state datagram and the reply being fixed and different from the WireGuard
  control sizes (32, 92, 148 bytes); check `packets.control` and `packets.unpaired_*` in
  the meta file of the first real run.
- On the wlan0 egress the capture point is after the qdisc: what a reply waited in netem
  or in the driver's back-pressured queue is visible in `pair_*`, what it waits inside the
  Wi-Fi firmware is not. Which of the two the Pi 5 shows under L1 is an A8/A0 finding.
- N1's keepalive starts with the bridge, 1-3 s before the run's first scan; the run start
  N3 records is that keepalive. The C3 merge reports t0 on N1's clock too
  (`analysis/sweep_index.py`, `t0_n1_s`), which must be over 15 s for the RTT baseline.
