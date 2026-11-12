#!/usr/bin/env bash
# Harden a fresh Ubuntu 24.04 VPS, then install Docker.
#
# Usage, as your sudo user (e.g. "ubuntu" on OVH), from a session you keep open:
#     sudo bash infra/vps/harden.sh "$USER"
#
# Safety: the script refuses to disable password login if the user has no SSH
# key, and it validates the SSH configuration before reloading it. Keep your
# current session open and test a NEW connection before closing it.
set -euo pipefail

ADMIN_USER="${1:?usage: sudo bash harden.sh <admin-user>}"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

KEYS="$(getent passwd "$ADMIN_USER" | cut -d: -f6)/.ssh/authorized_keys"
if [[ ! -s "$KEYS" ]]; then
  echo "No SSH key in $KEYS: add yours first, or you would lock yourself out." >&2
  exit 1
fi

echo "==> Updating packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ufw fail2ban python3-systemd unattended-upgrades docker.io docker-compose-v2 git

echo "==> SSH: key only, no root login"
# Files in sshd_config.d are read in order and the FIRST value wins:
# "00-" beats the cloud image's "50-cloud-init.conf", which enables passwords.
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
EOF
sshd -t
systemctl reload ssh

echo "==> Firewall: deny everything inbound except SSH, HTTP, HTTPS"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> fail2ban: ban an IP after 5 failed SSH logins"
cat > /etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
backend = systemd
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

echo "==> Automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "==> Docker"
systemctl enable --now docker
usermod -aG docker "$ADMIN_USER"

echo
echo "Done. Before closing this session, from your PC, in a NEW terminal:"
echo "  ssh $ADMIN_USER@<ip>                                  # must work (key)"
echo "  ssh -o PubkeyAuthentication=no $ADMIN_USER@<ip>       # must be refused"
echo "  ssh root@<ip>                                         # must be refused"
echo "Then log out and back in, so the docker group applies."
