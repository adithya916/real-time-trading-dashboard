#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 22.04/24.04 server (made for Oracle Cloud's Always Free ARM
# VM, works on any Ubuntu box with a public IP). Run it on the server as the default user:
#
#   curl -fsSLO https://raw.githubusercontent.com/adithya916/real-time-trading-dashboard/main/deploy/oracle-setup.sh
#   less oracle-setup.sh            # read it first
#   bash oracle-setup.sh [domain]   # default: <public-ip>.sslip.io
#
# It installs Docker from Docker's apt repository, opens ports 80 and 443 in the server's own
# firewall, raises connection limits, clones the repository to ~/rtd, writes ~/rtd/.env with
# random passwords, and starts the stack. Oracle also filters traffic in the VCN: add ingress
# rules for TCP 80 and 443 to the subnet's security list in the console.
set -euo pipefail

REPO="${REPO:-https://github.com/adithya916/real-time-trading-dashboard.git}"
DIR="${DIR:-$HOME/rtd}"

echo "== Docker"
if ! command -v docker >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
fi

echo "== Firewall: 80 and 443 (Oracle's Ubuntu images reject everything but SSH)"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null ||
    sudo iptables -I INPUT 5 -p tcp --dport "$port" -j ACCEPT
done
sudo iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null ||
  sudo iptables -I INPUT 5 -p udp --dport 443 -j ACCEPT
if command -v netfilter-persistent >/dev/null; then sudo netfilter-persistent save; fi

echo "== Limits for many WebSocket viewers"
sudo tee /etc/sysctl.d/90-rtd.conf >/dev/null <<'SYSCTL'
fs.file-max = 1048576
net.core.somaxconn = 8192
net.ipv4.ip_local_port_range = 1024 65535
SYSCTL
sudo sysctl --system >/dev/null
sudo mkdir -p /etc/docker
echo '{"default-ulimits": {"nofile": {"Name": "nofile", "Soft": 65536, "Hard": 65536}}}' |
  sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl restart docker

echo "== Code and secrets"
[ -d "$DIR/.git" ] || git clone "$REPO" "$DIR"
cd "$DIR"
if [ ! -f .env ]; then
  ip="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
  domain="${1:-${ip//./-}.sslip.io}"
  umask 077
  cat >.env <<ENV
DOMAIN=$domain
TIMESCALE_PASSWORD=$(openssl rand -hex 24)
GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)
ENV
  echo "wrote .env for $domain (passwords are random; keep the file private)"
fi

echo "== Start"
sudo docker compose -f compose.yaml -f deploy/compose.prod.yaml --profile feeds up -d --build --wait --wait-timeout 900
. ./.env
echo "Up: https://$DOMAIN  (Grafana: https://$DOMAIN/grafana/)"
