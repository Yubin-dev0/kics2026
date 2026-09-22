#!/bin/bash
# Gives N3 eth0 its fixed address towards N4 (A4). No gateway: eth0 leads to N4 only.
# Run it over the Wi-Fi AP, not over the cable, because it changes eth0:
#
#   ssh yubin@192.168.60.1 'sudo bash -s' < net/n3/eth_up.sh
#
# The DHCP profile NetworkManager made at first boot ("Wired connection 1") is kept but
# no longer autoconnects, so the laptop's shared connection can be brought back by hand
# (nmcli con up "Wired connection 1") if N3 ever needs internet again.
set -euo pipefail
ADDR=${ADDR:-192.168.50.1/24}

nmcli -t -f NAME con show | grep -qx n3-eth && nmcli con delete n3-eth
nmcli -t -f NAME,TYPE con show | awk -F: '$2 ~ /ethernet/ && $1 != "n3-eth" {print $1}' |
    while read -r c; do nmcli con modify "$c" connection.autoconnect no; done
nmcli con add type ethernet ifname eth0 con-name n3-eth autoconnect yes \
    ipv4.method manual ipv4.addresses "$ADDR" ipv4.never-default yes ipv6.method disabled
nmcli con up n3-eth
ip -br addr show eth0
