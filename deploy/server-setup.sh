#!/usr/bin/env bash
# One-time hardening + Docker install for a FRESH Ubuntu 24.04 VPS
# (Hetzner or any other). Run ONCE, as root, from the first SSH login:
#
#   curl -fsSL <raw url of this file> | bash        # or scp it up and run it
#   DEPLOY_USER=deploy bash server-setup.sh
#
# What it does, and nothing else:
#   - installs Docker + the compose plugin, fail2ban, ufw, unattended-upgrades
#   - creates a non-root `deploy` user (docker group) with YOUR ssh key
#   - turns OFF password and root SSH login — but ONLY if it found a key
#     to give the deploy user, so it can't lock you out of your own server
#   - firewall: deny everything inbound except SSH, 80, 443
#   - docker log rotation (an unrotated json log is how small VPS disks fill up)
#   - 2 GB swap (pg_dump + API + Postgres on 4 GB is fine until it isn't)
#
# After it finishes: open a SECOND terminal and confirm
#   ssh deploy@<server-ip>
# works BEFORE closing this root session.
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"

if [ "$(id -u)" -ne 0 ]; then echo "Run as root."; exit 1; fi
. /etc/os-release
if [ "${ID}" != "ubuntu" ]; then echo "This script targets Ubuntu (found ${ID})."; exit 1; fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl git ufw fail2ban unattended-upgrades docker.io docker-compose-v2

timedatectl set-timezone UTC

# ---- deploy user + the key that will log in as it -------------------------
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG docker "$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
if [ -s /root/.ssh/authorized_keys ]; then
  cp /root/.ssh/authorized_keys "/home/$DEPLOY_USER/.ssh/authorized_keys"
  chown "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh/authorized_keys"
  chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
fi

# ---- SSH hardening (only if a key is in place) ---------------------------
if [ -s "/home/$DEPLOY_USER/.ssh/authorized_keys" ]; then
  cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
SSHD
  sshd -t
  systemctl reload ssh
  echo "SSH: password + root login DISABLED. Log in as ${DEPLOY_USER} with your key."
else
  echo "WARNING: no ssh key found in /root/.ssh/authorized_keys — left password/root SSH login ENABLED"
  echo "         so you are not locked out. Add a key, then re-run this script."
fi

# ---- firewall ------------------------------------------------------------
# Note: Docker-published ports bypass ufw. That is fine here by design —
# docker-compose.prod.yml publishes only caddy's 80/443 and nothing else.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# ---- fail2ban (default sshd jail is on) + automatic security updates -----
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades

# ---- docker log rotation --------------------------------------------------
install -d /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
systemctl enable --now docker
systemctl restart docker

# ---- swap -----------------------------------------------------------------
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
fi

install -d -o "$DEPLOY_USER" -g "$DEPLOY_USER" /opt/doi
echo
echo "Done. NEXT: from another terminal run  ssh ${DEPLOY_USER}@<server-ip>  and confirm it works"
echo "before you close this session. Then clone the repo into /opt/doi (see deploy/DEPLOY.md)."
