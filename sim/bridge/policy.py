"""Switching policies (integrated plan 4.3). flag 1 = local, 0 = edge (fw/PROTOCOL.md).

Policies 2, 3 and 4 need an edge link (--edge, sim/bridge/edge.py). Policy 3 acts on the
RTT window watcher (sim/bridge/rttwatch.py), which every run with an edge link carries;
policy 4 acts on the N3 flags (sim/bridge/netio.py).

flag_for_scan(current, degraded) is called once per scan after the edge step, with the
watcher's verdict for this step (None when the run has no watcher). flag_for_event is
called from the flag listener thread with the N3 verdict.
"""
LOCAL, EDGE = 1, 0


class AlwaysLocal:
    number, name, initial_flag = 1, 'always_local', LOCAL
    needs_edge = False

    def flag_for_scan(self, current, degraded):
        return LOCAL

    def flag_for_event(self, degrade):
        return None  # flags are received and logged but never acted on


class AlwaysEdge:
    """Policy 2: the edge command drives throughout, however late it is. A period without
    a fresh command reuses the previous one (D15); the safety board still applies SLOW and
    STOP, so state in the log shows where a safety controller would have intervened."""
    number, name, initial_flag = 2, 'always_edge', EDGE
    needs_edge = True

    def flag_for_scan(self, current, degraded):
        return EDGE

    def flag_for_event(self, degrade):
        return None


class RttWindow:
    """Policy 3 (baseline): local while the 2 s RTT window sits more than theta_high above
    the no-load minimum, edge again once it is back under theta_low. The flag rides the
    next S line, so B is zero for this policy by construction."""
    number, name, initial_flag = 3, 'rtt_window', EDGE
    needs_edge = True

    def flag_for_scan(self, current, degraded):
        if degraded is None:
            return current
        return LOCAL if degraded else EDGE

    def flag_for_event(self, degrade):
        return None  # N3 flags are logged for B, never acted on


class FlagDriven:
    """Policy 4: edge while the link is healthy, local while N3 reports degradation."""
    number, name, initial_flag = 4, 'n3_flag', EDGE
    needs_edge = True

    def flag_for_scan(self, current, degraded):
        return current

    def flag_for_event(self, degrade):
        return LOCAL if degrade else EDGE


def make(number):
    if number == 1:
        return AlwaysLocal()
    if number == 2:
        return AlwaysEdge()
    if number == 3:
        return RttWindow()
    if number == 4:
        return FlagDriven()
    raise SystemExit(f'unknown policy {number}')
