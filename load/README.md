# N5 competing traffic (A8)

N5 (the rented Pi 4) joins the same access point as N1 and pulls a bulk flow from an
iperf3 server, so the load crosses N3's wlan0 egress, the queue the robot's replies wait
in. The load is started by a UDP trigger, not by an ssh login, so it begins within a few
ms of the trigger and t0 (the trigger's departure) is a usable zero for A and D
(plan v5 4.1, 4.5).

## Layout

| path | contents |
|---|---|
| `n5_agent.py` | runs on N5, waits on UDP 47200, launches `iperf3 -c <server> -R` at once, reports start and end |
| `loadctl.py` | hand trigger from N1 or N3: ping the agent, start L1 / L2, stop; writes `load_run_N.json` for A8 |
| `n3_server.sh` | iperf3 server on N3 (daemon), start / stop / status over ssh |

In the sweep the N3 detector sends the trigger itself at t0 (`capture/detector.py --load L1
--n5 <N5>`), so t0 sits on N3's clock next to t_det_meta. `loadctl.py` is for A8 and for
repairs.

## Bringing up N5

1. Raspberry Pi OS Lite 64-bit, user `yubin`, ssh keys, then `sudo apt-get install -y iperf3`
   while N5 still has internet (school network or N1's ICS). Record `iperf3 --version`
   and `uname -r` in the A8 note. Nothing else is installed.
2. Join `kics-n3` (5 GHz). N5 takes 192.168.60.x from N3's DHCP; `ping 192.168.60.1` and
   `ping <N1>` is A4-4.
3. Start the agent in its own window and leave it: `ssh yubin@<N5> 'python3 -' < load/n5_agent.py`
   (or copy the file once; it is standard library only). `python3 load/loadctl.py --n5 <N5> ping`
   must answer.
4. Start the server on N3: `ssh yubin@192.168.60.1 'bash -s' < load/n3_server.sh`.

## Loads

| load | what N5 does | seconds | basis | status |
|---|---|---|---|---|
| L1 | `iperf3 -c <server> -R -u -b <rate> -l 1400 -t 45` (server sends UDP at a fixed rate to N5) | 45 | plan v5 5.1: sustained load from t0 to the end of the run | rate provisional (below) |
| L2 | the same for 2.5 s | 2.5 | plan v5 5.1: "a load that ends in 2 to 3 s", inside policy 3's 2 s window, several intervals long for the detector | provisional: 2.5 s is the midpoint; the plan's earlier "short GET repeats" became one short iperf3 burst so that L1 and L2 differ in length only (D8) |

Parameters (`loadctl.py` and `detector.py --load-*` share them):

| parameter | value | basis | status |
|---|---|---|---|
| protocol | UDP, `-l 1400` | a fixed offered rate that does not back off, so every repeat offers the same load (A8-1 asks for repeatability); 1400 B stays under the WireGuard-free MTU | provisional, A8 |
| rate | 60M | above the link's throughput, so the wlan0 queue fills: N1 links at 86.7 Mbit/s (net/README.md, 20 MHz channel), and a single-stream 802.11ac link on 20 MHz carries roughly 50-60 Mbit/s of UDP. Set at A8 from the capacity check: rate = 1.2 x the TCP reverse throughput measured with `loadctl.py start L1 --proto tcp --seconds 10` | provisional, A8 |
| server | N3 (192.168.60.1) | one hop: the load is queued on N3's wlan0 egress exactly where the robot's replies are queued, with no N4 involvement | provisional, A8 decides N3 vs N4 |
| trigger port | UDP 47200 | next to the flag port 47100 and the edge port 47000 | confirmed |

## Pass criteria (A8, plan v5 7.3)

| ID | criterion | basis | status |
|---|---|---|---|
| A8-1 | L1 raises the N1 to N4 round trip reproducibly: three L1 runs with `net/ping_run.py` (A6 harness, no netem) running alongside, the rise of the median over the no-load reference within 20% across the three | plan v5 7.3: "L1 raises the RTT reproducibly (3 repeats, spread < 20%)" | pending |
| A8-2 | after L2 ends, the round trip median of the next 3 s is back within the A4 spread of the reference | plan v3 10.1 A8: "RTT back to baseline within 3 s of L2's end"; the 3 s is provisional | pending |
| A8-3 | the agent acknowledges the trigger within 50 ms (`ack_rtt_ms` in `load_run_N.json`), and iperf3's own JSON shows the offered rate was reached (received Mbit/s within 10% of `-b`, or the link's capacity if lower) | t0 must be within one control period of the load's start | pending |
| A8-4 | server placement: with the server on N3 during L1, tcpdump on N3 reports 0 packets dropped by the kernel (`tcpdump_stderr` in the detector meta) and the detector's steps keep their 100 ms cadence (`t_ns` differences); otherwise the server moves to N4 | the capture must not be the process the load starves | pending |

Record every A8 run in the ledger (`analysis/append_run.py A8 N`, harness `load`, note
with the rate, protocol, received Mbit/s, ping median with and without load).

## Known constraints

- `-R` with a UDP client means the server sends; iperf3 reports the rate the receiver got
  in `sum_received` of its JSON. N5 writes it to `/home/yubin/load/load_<load>_run_<run>.json`
  and sends the received Mbit/s back in its done datagram.
- The trigger is one datagram over the same Wi-Fi; if it is lost the run has no load. The
  agent's ack (A) is recorded by the trigger's sender (detector meta `n5_messages`,
  `load_run_N.json` `ack`); a run without an ack is `discarded`.
- N5's clock is not used for anything; its timestamps in the ack are for sanity only.
