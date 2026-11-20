#!/usr/bin/env bash
# Pattie VPS setup (ARCHITECTURE.md, "VPS security (Tailscale)"). Run on the server as root.
#
#   scp infra/setup-vps.sh root@<ip>:/root/ && ssh root@<ip>
#   bash setup-vps.sh base        # updates, automatic security updates, Docker, basics
#   bash setup-vps.sh tailscale   # install Tailscale and join the tailnet (prints a login link)
#   -- from your laptop, check:  ssh root@<vps-name>   (over Tailscale, not the public IP)
#   bash setup-vps.sh lockdown    # firewall: no inbound from the internet; password SSH off
#
# Each step is safe to run again. "lockdown" refuses to run unless Tailscale SSH is up,
# so you can't lock yourself out by running it too early.
set -euo pipefail

need_root() { [ "$(id -u)" -eq 0 ] || { echo "Run as root."; exit 1; }; }

base() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get upgrade -yq
  apt-get install -yq unattended-upgrades ufw curl ca-certificates jq
  # Automatic security updates (tenet 2)
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  systemctl enable --now unattended-upgrades
  # Docker from Ubuntu's own packages (get.docker.com lags behind new Ubuntu releases)
  if ! command -v docker >/dev/null; then
    apt-get install -yq docker.io docker-compose-v2 || apt-get install -yq docker.io docker-compose
    systemctl enable --now docker
  fi
  # Containers publish on localhost by default, so `ports:` can't bypass ufw by accident.
  mkdir -p /etc/docker
  if [ ! -s /etc/docker/daemon.json ]; then
    echo '{ "ip": "127.0.0.1" }' >/etc/docker/daemon.json
    systemctl restart docker
  fi
  timedatectl set-timezone UTC
  echo "base: done. Docker $(docker --version | cut -d' ' -f3 | tr -d ,)"
}

tailscale_step() {
  if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  # --ssh: Tailscale SSH (logins checked against the tailnet policy, not keys on this box).
  # Without TS_AUTHKEY this prints a login URL; open it and approve the machine.
  if [ -n "${TS_AUTHKEY:-}" ]; then
    tailscale up --ssh --hostname "${TS_HOSTNAME:-pattie-vps}" --advertise-tags=tag:pattie-vps --authkey "$TS_AUTHKEY"
  else
    tailscale up --ssh --hostname "${TS_HOSTNAME:-pattie-vps}"
  fi
  tailscale set --auto-update
  echo
  echo "tailscale: up as $(tailscale ip -4 | head -1)."
  echo "In the Tailscale admin console: disable key expiry for this machine."
  echo "From your laptop (with Tailscale running), check: ssh root@${TS_HOSTNAME:-pattie-vps}"
}

lockdown() {
  # Refuse unless Tailscale is connected and Tailscale SSH is on.
  tailscale status --json | jq -e '.BackendState == "Running"' >/dev/null \
    || { echo "Tailscale isn't running. Run: bash setup-vps.sh tailscale"; exit 1; }
  tailscale debug prefs 2>/dev/null | jq -e '.RunSSH == true' >/dev/null \
    || { echo "Tailscale SSH is off. Run: tailscale set --ssh"; exit 1; }

  ufw --force reset
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow in on tailscale0 comment 'tailnet'
  ufw allow 41641/udp comment 'tailscale direct connections'
  # Airbyte Cloud -> Postgres exception goes here later (ARCHITECTURE.md FAQ 22), e.g.:
  # ufw allow from <airbyte-ip> to any port 5432 proto tcp comment 'airbyte cloud'
  ufw --force enable

  # Public SSH is now blocked by the firewall; also turn off passwords and root password login.
  cat >/etc/ssh/sshd_config.d/10-pattie.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
  sshd -t && systemctl reload ssh 2>/dev/null || systemctl reload sshd
  ufw status verbose
  echo "lockdown: done. The public IP accepts nothing inbound. Use Tailscale to connect."
  echo "Also set Hostinger's panel firewall to drop all inbound except UDP 41641."
}

need_root
case "${1:-}" in
  base) base ;;
  tailscale) tailscale_step ;;
  lockdown) lockdown ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
