# Deploying on a VPS

Target: an Ubuntu 24.04 VPS (OVHcloud), with Docker Compose. About 20 minutes.

## 1. Connect with a key

On your PC, if you have no key yet:

```bash
ssh-keygen -t ed25519
ssh-copy-id ubuntu@<vps-ip>     # once, with the password OVH sent you
ssh ubuntu@<vps-ip>             # must work without a password now
```

## 2. Harden the server

Keep this first session open for the whole step.

```bash
git clone https://github.com/HakeemMHB/incident-desk.git
cd incident-desk
sudo bash infra/vps/harden.sh "$USER"
```

From your PC, in a **new** terminal, check before closing anything:

```bash
ssh ubuntu@<vps-ip>                                   # works
ssh -o PubkeyAuthentication=no ubuntu@<vps-ip>        # Permission denied
ssh root@<vps-ip>                                     # Permission denied
```

Then log out and back in, so your user can run `docker`.

## 3. Configure and start

```bash
cd incident-desk
cp .env.example .env
chmod 600 .env
```

Edit `.env`:

- `POSTGRES_PASSWORD` and `REDIS_PASSWORD`: generate each with
  `openssl rand -hex 24`;
- `APP_BIND=0.0.0.0` and `APP_PORT=80`.

```bash
docker compose up -d --build
docker compose ps                   # three services, healthy
curl -s localhost/readyz            # {"postgres":"up","redis":"up"}
```

Open `http://<vps-ip>` in a browser.

## 4. Verify from outside

From your PC:

```bash
nmap -Pn <vps-ip>                   # only 22, 80 (and 443) open
```

PostgreSQL (5432) and Redis (6379) must not appear: they are not published.

On the VPS, failed SSH attempts blocked by fail2ban:

```bash
sudo fail2ban-client status sshd
```

## 5. Update

```bash
git pull
docker compose up -d --build
```

## Good to know

- **Docker bypasses ufw for published ports.** A port published with
  `ports:` is reachable even if ufw does not allow it, because Docker writes
  its own firewall rules. That is why the databases have no `ports:` at all,
  and why the app is bound to `127.0.0.1` by default.
- **No HTTPS yet.** With a domain name, a reverse proxy such as Caddy would
  add a Let's Encrypt certificate automatically. Without HTTPS, do not enter
  anything sensitive in the form.
