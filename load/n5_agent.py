#!/usr/bin/env python3
"""N5 load agent (A8): waits on UDP for a start datagram and launches iperf3 at once, so the
load begins within a few ms of the trigger instead of the 0.5-1 s of an ssh login. The
trigger is sent by the N3 detector at t0 (capture/detector.py --load) or by hand with
load/loadctl.py. Standard library only; piped to N5 over ssh like the N3 scripts, or copied
once:

  ssh yubin@<N5> 'python3 -' < load/n5_agent.py               (keep this window open)
  ssh yubin@<N5> 'python3 - --port 47200 --out /home/yubin/load' < load/n5_agent.py

Datagrams (ASCII, XOR checksum as everywhere else in the testbed):
  L,<load>,<seconds>,<proto>,<rate>,<server>,<run>*XX   start: iperf3 -c <server> -R -t <seconds>
                                                        (-u -b <rate> for udp); L1 / L2 only name
                                                        the file and the row in the reply
  S*XX                                                  stop whatever runs
  P*XX                                                  ping
replies
  A,<load>,<run>,<t_start_ns>,<pid>*XX                  iperf3 started (N5 wall clock)
  E,<text>*XX                                           error
  D,<load>,<run>,<t_end_ns>,<returncode>,<mbps>*XX      sent to the last trigger source when
                                                        iperf3 ends (mbps from its JSON)
  S,<killed>*XX / P,<t_ns>*XX

Every run writes <out>/load_<load>_run_<run>.json, the iperf3 --json output, which holds the
achieved rate, the loss and the jitter of the load itself. -R makes the server (N3) send and
N5 receive, so the load crosses N3's wlan0 egress queue, the one the robot's replies share.
"""
import argparse
import json
import os
import socket
import subprocess
import time
from functools import reduce


def checksum(body):
    return reduce(lambda x, c: x ^ c, body.encode('ascii'), 0)


def with_checksum(body):
    return f'{body}*{checksum(body):02X}\n'.encode('ascii')


def parse(data):
    text = data.decode('ascii', 'replace').strip()
    body, sep, cs = text.rpartition('*')
    if not sep or len(cs) != 2 or int(cs, 16) != checksum(body):
        raise ValueError(text)
    return body.split(',')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=47200)
    ap.add_argument('--out', default=os.path.expanduser('~/load'))
    ap.add_argument('--iperf3', default='iperf3')
    ap.add_argument('--iperf-port', type=int, default=5201)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', args.port))
    s.settimeout(0.1)
    proc, cur, log = None, None, None
    print(f'listening on 0.0.0.0:{args.port}, output {args.out}', flush=True)
    try:
        while True:
            if proc is not None and proc.poll() is not None:
                t_end = time.time_ns()
                log.close()
                mbps = ''
                try:
                    with open(cur['path']) as f:
                        j = json.load(f)
                    end = j.get('end', {})
                    summ = end.get('sum_received') or end.get('sum') or {}
                    mbps = f"{summ.get('bits_per_second', 0) / 1e6:.1f}"
                    lost = summ.get('lost_percent')
                    print(f"iperf3 done rc {proc.returncode}: {mbps} Mbit/s received"
                          f"{'' if lost is None else f', lost {lost:.2f}%'}", flush=True)
                except (OSError, ValueError) as e:
                    print(f'iperf3 done rc {proc.returncode}, no json: {e}', flush=True)
                s.sendto(with_checksum(f"D,{cur['load']},{cur['run']},{t_end},{proc.returncode},{mbps}"), cur['src'])
                proc = None
            try:
                data, src = s.recvfrom(256)
            except socket.timeout:
                continue
            try:
                f = parse(data)
            except ValueError as e:
                print(f'bad datagram from {src}: {e}', flush=True)
                continue
            if f[0] == 'P':
                s.sendto(with_checksum(f'P,{time.time_ns()}'), src)
            elif f[0] == 'S':
                killed = 0
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    killed = 1
                s.sendto(with_checksum(f'S,{killed}'), src)
                print(f'stop from {src[0]}: killed {killed}', flush=True)
            elif f[0] == 'L' and len(f) == 7:
                _, load, seconds, proto, rate, server, run = f
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=2)
                path = os.path.join(args.out, f'load_{load}_run_{run}.json')
                cmd = [args.iperf3, '-c', server, '-p', str(args.iperf_port), '-R', '-t', seconds,
                       '--json', '--logfile', path]
                if proto == 'udp':
                    cmd += ['-u', '-b', rate, '-l', '1400']
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    log = open(path + '.err', 'w')
                    t_start = time.time_ns()
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log)
                except OSError as e:
                    s.sendto(with_checksum(f'E,{e}'), src)
                    print(f'cannot start iperf3: {e}', flush=True)
                    continue
                cur = {'load': load, 'run': run, 'path': path, 'src': src}
                s.sendto(with_checksum(f'A,{load},{run},{t_start},{proc.pid}'), src)
                print(f'{load} run {run} from {src[0]}: {" ".join(cmd)} (pid {proc.pid})', flush=True)
            else:
                s.sendto(with_checksum('E,unknown'), src)
    except KeyboardInterrupt:
        pass
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()


if __name__ == '__main__':
    main()
