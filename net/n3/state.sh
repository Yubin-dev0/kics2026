#!/bin/bash
# Snapshot of everything A4 freezes on N3: board, OS, kernel, Wi-Fi firmware and
# regulatory domain, the AP and eth0 profiles, forwarding, NAT, qdiscs, package versions.
# Saved next to the A4 runs, so a later swap (Pi 4 -> Pi 5) can be diffed:
#
#   ssh yubin@192.168.60.1 'sudo bash -s' < net/n3/state.sh > data/a4/n3_state_<device>.txt
set -uo pipefail
sec() { echo; echo "## $1"; }

sec board;      tr -d '\0' < /proc/device-tree/model; echo; hostname
sec os;         . /etc/os-release; echo "$PRETTY_NAME"; uname -r
sec regdomain;  iw reg get | sed -n '1,4p'
sec wlan0;      iw dev wlan0 info
sec firmware;   dmesg | grep -i 'brcmf.*firmware' | tail -2
sec ap;         nmcli -f 802-11-wireless.ssid,802-11-wireless.mode,802-11-wireless.band,802-11-wireless.channel,802-11-wireless.powersave,802-11-wireless-security.key-mgmt,802-11-wireless-security.pairwise,ipv4.method,ipv4.addresses con show n3-ap 2>&1
sec eth0;       nmcli -f ipv4.method,ipv4.addresses,ipv4.never-default con show n3-eth 2>&1; ethtool eth0 2>/dev/null | grep -E 'Speed|Duplex|Link detected'
sec addresses;  ip -br addr
sec clients;    iw dev wlan0 station dump | grep -E '^Station|signal:|tx bitrate|rx bitrate'
sec forwarding; sysctl net.ipv4.ip_forward
sec nat;        nft list ruleset 2>/dev/null | grep -iE 'masquerade|nm-shared' || iptables -t nat -S 2>/dev/null
sec qdisc;      tc qdisc show dev wlan0; tc qdisc show dev eth0
sec packages;   dpkg-query -W -f='${Package} ${Version}\n' network-manager dnsmasq-base iproute2 tcpdump iperf3 chrony iw ethtool 2>&1
