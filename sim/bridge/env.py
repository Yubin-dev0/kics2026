"""Records the conditions a bridge run was executed under.

Windows-side facts are read through WSL interop. Every probe has a timeout and records
its error instead of stopping the run; the verdict decides what a missing fact means.

Pre-run check, from sim/:  python3 -m bridge.env
"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from .paths import REPO

WIN = {
    'reg': ['reg.exe', '/mnt/c/Windows/System32/reg.exe'],
    'powershell': ['powershell.exe',
                   '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'],
    'wsl': ['wsl.exe', '/mnt/c/Windows/System32/wsl.exe'],
    'usbipd': ['usbipd.exe', '/mnt/c/Program Files/usbipd-win/usbipd.exe'],
}

# Windows 11 power mode (Settings > System > Power) is stored as an overlay scheme.
# The mapping is checked on the first B1 run by switching the mode (see the B1 guide).
OVERLAYS = {
    '00000000-0000-0000-0000-000000000000': 'balanced',
    'ded574b5-45a0-4f42-8737-46345c09c238': 'best_performance',
    '961cc777-2547-4f9d-8174-7d86181b8a7a': 'best_power_efficiency',
}

PS_SCRIPT = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$k='HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Power\\User\\PowerSchemes';"
    "$o=Get-ItemProperty $k -ErrorAction SilentlyContinue;"
    "$b=Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus "
    "-ErrorAction SilentlyContinue | Select-Object -First 1;"
    "$a=Get-NetAdapter -ErrorAction SilentlyContinue | "
    "Select-Object Name,Status,InterfaceDescription;"
    "[pscustomobject]@{overlay_ac=$o.ActiveOverlayAcPowerScheme;"
    "overlay_dc=$o.ActiveOverlayDcPowerScheme;power_online=$b.PowerOnline;"
    "adapters=@($a)} | ConvertTo-Json -Depth 3 -Compress"
)

BRIDGE_SOURCES = ['sim/bridge/core.py', 'sim/bridge/policy.py', 'sim/bridge/summary.py',
                  'sim/bridge/node.py', 'sim/bridge/run.py', 'sim/bridge/env.py',
                  'sim/bridge/paths.py', 'sim/nav.py', 'fw/tools/proto.py']
NET_SOURCES = ['sim/bridge/netio.py']
WORLD = os.path.expanduser('~/tb3_ws/install/turtlebot3_gazebo/share/'
                           'turtlebot3_gazebo/worlds/a1_course.world')


def _exe(key):
    for c in WIN[key]:
        if shutil.which(c) or os.path.exists(c):
            return c
    return None


def _run(cmd, timeout=8.0, encoding='utf-8'):
    # Windows programs started from a Linux working directory warn about UNC paths
    cwd = '/mnt/c' if cmd[0].endswith('.exe') and os.path.isdir('/mnt/c') else None
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f'{type(e).__name__}: {e}'
    text = out.stdout.decode(encoding, 'replace')
    if out.returncode != 0:
        return text, f'exit {out.returncode}: {out.stderr.decode("utf-8", "replace")[:200]}'
    return text, None


def windows_facts(busid):
    facts, errors = {}, {}
    ps = _exe('powershell')
    if ps:
        text, err = _run([ps, '-NoProfile', '-NonInteractive', '-Command', PS_SCRIPT],
                         timeout=20.0)
        if err:
            errors['powershell'] = err
        else:
            try:
                j = json.loads(text.strip().lstrip('\ufeff'))
                ac, dc = (j.get('overlay_ac') or '').lower(), (j.get('overlay_dc') or '').lower()
                facts['power_overlay_ac'] = ac or None
                facts['power_overlay_dc'] = dc or None
                facts['power_mode_ac'] = OVERLAYS.get(ac, 'unknown') if ac else None
                facts['power_mode_dc'] = OVERLAYS.get(dc, 'unknown') if dc else None
                facts['on_ac_power'] = j.get('power_online')
                ad = j.get('adapters') or []
                facts['windows_adapters'] = [
                    {'name': a.get('Name'), 'status': a.get('Status'),
                     'desc': a.get('InterfaceDescription')} for a in ad]
            except (ValueError, AttributeError) as e:
                errors['powershell'] = f'parse: {e}: {text[:200]!r}'
    else:
        errors['powershell'] = 'not found'

    wsl = _exe('wsl')
    if wsl:
        text, err = _run([wsl, '--version'], encoding='utf-16-le')
        if err:
            errors['wsl'] = err
        else:
            facts['wsl_version'] = [ln.strip() for ln in text.splitlines() if ln.strip()]
    else:
        errors['wsl'] = 'not found'

    usbipd = _exe('usbipd')
    if usbipd:
        text, err = _run([usbipd, '--version'])
        facts['usbipd_version'] = text.strip() if text and not err else None
        if err:
            errors['usbipd_version'] = err
        text, err = _run([usbipd, 'list'])
        if err:
            errors['usbipd_list'] = err
        else:
            line = next((ln for ln in text.splitlines() if ln.strip().startswith(busid + ' ')),
                        None)
            facts['usbipd_busid_line'] = re.sub(r'\s+', ' ', line).strip() if line else None
    else:
        errors['usbipd'] = 'not found'
    return facts, errors


def linux_facts(port):
    facts = {'kernel': platform.release(), 'python': platform.python_version()}
    try:
        for ln in Path('/etc/os-release').read_text().splitlines():
            if ln.startswith('PRETTY_NAME='):
                facts['os'] = ln.split('=', 1)[1].strip('"')
    except OSError:
        pass
    text, err = _run(['ip', '-4', 'route'], timeout=3.0)
    facts['ip_route'] = text.strip().splitlines() if text and not err else err
    real = os.path.realpath(port)
    facts['port_realpath'] = real
    facts['port_holders'] = port_holders(real)
    return facts


def port_holders(real):
    """Other processes with the port open (readable /proc entries only)."""
    me, found = os.getpid(), []
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            for fd in os.listdir(f'/proc/{pid}/fd'):
                if os.path.realpath(f'/proc/{pid}/fd/{fd}') == real:
                    cmd = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ')
                    found.append(f'{pid} {cmd.decode(errors="replace").strip()[:80]}')
                    break
        except OSError:
            continue
    return found


def sha1_files(rel_paths):
    h = hashlib.sha1()
    for rel in sorted(rel_paths):
        p = REPO / rel
        h.update(rel.encode())
        h.update(p.read_bytes().replace(b'\r\n', b'\n') if p.exists() else b'missing')
    return h.hexdigest()[:12]


def sha1_file(path):
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return 'missing'


def git_info():
    try:
        commit = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', '--short', 'HEAD'],
                                         text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(
            ['git', '-C', str(REPO), 'status', '--porcelain', '--',
             'sim', 'fw', 'capture', 'load', 'net'],
            text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return commit, dirty


def code_info():
    return {'code_sha1': sha1_files(BRIDGE_SOURCES), 'net_sha1': sha1_files(NET_SOURCES),
            'world_sha1': sha1_file(WORLD)}


def main():
    import argparse
    from .run import BOARD_PORT
    ap = argparse.ArgumentParser(description='Print the facts a board run would record.')
    ap.add_argument('--port', default=BOARD_PORT)
    ap.add_argument('--busid', default='1-3')
    args = ap.parse_args()
    win, err = windows_facts(args.busid)
    print(json.dumps({'windows': win, 'errors': err, 'linux': linux_facts(args.port),
                      'git': git_info(), **code_info()}, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
