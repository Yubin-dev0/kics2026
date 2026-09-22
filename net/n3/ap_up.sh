#!/bin/bash
# Brings up the 5 GHz access point on N3 wlan0 with NetworkManager (A4). Idempotent:
# an existing n3-ap connection is replaced. The passphrase is passed in, never stored in
# the repository:
#
#   ssh yubin@<N3 address> "sudo PSK='<passphrase>' bash -s" < net/n3/ap_up.sh
#
# Frozen parameters (net/README.md): SSID kics-n3, band a, channel 36 (UNII-1, non-DFS,
# allowed for APs in KR), WPA2-PSK with CCMP only, Wi-Fi power save off, AP subnet
# 192.168.60.0/24 in NetworkManager shared mode (DHCP on N3, NAT towards eth0).
set -euo pipefail
: "${PSK:?give PSK='<passphrase>' (8 to 63 characters)}"
SSID=${SSID:-kics-n3}
CHANNEL=${CHANNEL:-36}
ADDR=${ADDR:-192.168.60.1/24}

nmcli -t -f NAME con show | grep -qx n3-ap && nmcli con delete n3-ap
nmcli con add type wifi ifname wlan0 con-name n3-ap autoconnect yes ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band a 802-11-wireless.channel "$CHANNEL" \
    802-11-wireless.powersave 2 \
    wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn \
    wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.psk "$PSK" \
    ipv4.method shared ipv4.addresses "$ADDR" ipv6.method disabled
nmcli con up n3-ap
sleep 2
iw dev wlan0 info
