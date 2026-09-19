"""Switching policies (integrated plan 6.1). flag 1 = local, 0 = edge (fw/PROTOCOL.md).

Only policy 1 is complete in B1. Policy 4 is wired to the flag listener so the flag path
can be tested; its edge commands come from the stub until B2. Policies 2 and 3 need the
edge controller (B2) and the RTT window (B4).
"""
LOCAL, EDGE = 1, 0


class AlwaysLocal:
    number, name, initial_flag = 1, 'always_local', LOCAL

    def flag_for_scan(self, current):
        return LOCAL

    def flag_for_event(self, degrade):
        return None  # flags are received and logged but never acted on


class FlagDriven:
    """Policy 4: edge while the link is healthy, local while N3 reports degradation."""
    number, name, initial_flag = 4, 'n3_flag', EDGE

    def flag_for_scan(self, current):
        return current

    def flag_for_event(self, degrade):
        return LOCAL if degrade else EDGE


def make(number):
    if number == 1:
        return AlwaysLocal()
    if number == 4:
        return FlagDriven()
    if number == 2:
        raise SystemExit('policy 2 (always edge) needs the edge controller: stage B2')
    if number == 3:
        raise SystemExit('policy 3 (RTT threshold) needs the edge link and window: stage B4')
    raise SystemExit(f'unknown policy {number}')
