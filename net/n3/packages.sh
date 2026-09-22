#!/bin/bash
# Installs everything N3 needs from A4 to C3, once, while it still has internet (first
# boot, through the laptop's shared connection), and prints the installed versions.
# The version list is the software freeze of A4: nothing is installed or upgraded on N3
# after the A4 verdict. Run from the repository root on N1:
#
#   ssh yubin@<N3 address> 'sudo bash -s' < net/n3/packages.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
# tcpdump: A7 capture. iperf3: A8 load (also usable from N3). chrony: A9 clock reference.
# dnsmasq-base: DHCP for the AP (NetworkManager shared mode). iw, ethtool: state records.
apt-get install -y --no-install-recommends tcpdump iperf3 chrony dnsmasq-base iw ethtool

echo "## installed"
dpkg-query -W -f='${Package} ${Version}\n' \
    network-manager dnsmasq-base iproute2 tcpdump iperf3 chrony iw ethtool
echo "## kernel"
uname -r
