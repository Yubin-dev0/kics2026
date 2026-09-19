"""Switching policies (integrated plan 4.3). flag 1 = local, 0 = edge (fw/PROTOCOL.md).

Policies 1, 2 and 4 are implemented. Policies 2 and 4 need an edge link (--edge,
sim/bridge/edge.py). Policy 3 needs the RTT window watcher, which will read the rtt_us
column this bridge already writes.
"""
LOCAL, EDGE = 1, 0


class AlwaysLocal:
    number, name, initial_flag = 1, 'always_local', LOCAL
    needs_edge = False

    def flag_for_scan(self, current):
        return LOCAL

    def flag_for_event(self, degrade):
        return None  # flags are received and logged but never acted on


class AlwaysEdge:
    """Policy 2: the edge command drives throughout, however late it is. A period without
    a fresh command reuses the previous one (D15); the safety board still applies SLOW and
    STOP, so state in the log shows where a safety controller would have intervened."""
    number, name, initial_flag = 2, 'always_edge', EDGE
    needs_edge = True

    def flag_for_scan(self, current):
        return EDGE

    def flag_for_event(self, degrade):
        return None


class FlagDriven:
    """Policy 4: edge while the link is healthy, local while N3 reports degradation."""
    number, name, initial_flag = 4, 'n3_flag', EDGE
    needs_edge = True

    def flag_for_scan(self, current):
        return current

    def flag_for_event(self, degrade):
        return LOCAL if degrade else EDGE


def make(number):
    if number == 1:
        return AlwaysLocal()
    if number == 2:
        return AlwaysEdge()
    if number == 4:
        return FlagDriven()
    if number == 3:
        raise SystemExit('policy 3 (RTT window) needs the window watcher: not written yet')
    raise SystemExit(f'unknown policy {number}')
