# Deploying to a Hetzner VPS (private pilot)

Target: one Hetzner Cloud server, Docker Compose, Caddy for HTTPS, Cloudflare
in front. Invite-only sign-up. Everything below was exercised end to end
against a local copy of this exact stack (build → migrate → serve →
restore a dump → verify); the only parts that cannot be rehearsed
locally are the real-internet ones (DNS, Let's Encrypt, SMTP delivery,
Cloudflare), which have their own checks in step 9.

## 0. What you need before starting

| Item | Notes |
|---|---|
| Hetzner Cloud account | Add a card; new accounts may need ID verification. |
| A domain name | ~₹700–900/year. Cloudflare Registrar, Namecheap, GoDaddy — any. |
| Cloudflare account (free) | Domain's DNS must be on Cloudflare to use its proxy. |
| An SMTP provider | Brevo / Resend / Amazon SES / Zoho. Free tiers are enough. Without it, sign-up verification and password-reset emails are NOT delivered. |
| A **new** Cloudflare R2 bucket + a token scoped to it | For prod backups only. Never reuse the dev bucket. |
| This repo reachable from the server | Private repo: add a read-only **deploy key** (step 4). |

## 1. Create the server

Hetzner Cloud console → New project → Add server:

- **Location:** Singapore (closest to India with a Hetzner region).
- **Image:** Ubuntu 24.04.
- **Type:** a **x86** shared-vCPU plan with **4 GB RAM** (CX/CPX line). **Not** the
  ARM "CAX" line: the database image bundles an x86-64 wal-g build.
- **SSH key:** add your public key here (`ssh-keygen -t ed25519` if you have none).
  This is what lets the hardening script safely turn password login off.
- **Backups:** enable Hetzner backups (+20%). It is a second, independent safety net
  on top of the R2 backups.
- **Firewall:** create a Hetzner Cloud Firewall allowing inbound TCP 22, 80, 443 only,
  and attach it. (Defence in depth; the server also runs its own firewall.)

## 2. DNS + Cloudflare

Put the domain on Cloudflare (change nameservers at your registrar). Add two **A** records
pointing at the server's public IPv4: `app` and `api`.

Leave both **DNS only (grey cloud)** for now. Caddy needs to answer Let's Encrypt's challenge
directly the first time; you turn the proxy on in step 8.

## 3. Harden the server (once)

```bash
ssh root@<server-ip>
curl -fsSL https://raw.githubusercontent.com/toc2santy/Defence--Opportunity-Intelligence/master/deploy/server-setup.sh -o setup.sh
# private repo? then instead:  scp deploy/server-setup.sh root@<server-ip>:setup.sh
bash setup.sh
```

It installs Docker, creates the `deploy` user with your key, **disables password and root SSH
login**, enables the firewall/fail2ban/automatic security updates, rotates Docker logs and adds
swap. **Before closing the root session**, open a second terminal and confirm
`ssh deploy@<server-ip>` works.

## 4. Get the code onto the server

```bash
ssh deploy@<server-ip>
ssh-keygen -t ed25519 -f ~/.ssh/doi_deploy -N ""          # server-side key
cat ~/.ssh/doi_deploy.pub                                  # add as a READ-ONLY Deploy key
                                                           # in GitHub → repo → Settings → Deploy keys
printf 'Host github.com\n  IdentityFile ~/.ssh/doi_deploy\n' >> ~/.ssh/config
git clone git@github.com:toc2santy/Defence--Opportunity-Intelligence.git /opt/doi
cd /opt/doi
```

## 5. Configure

```bash
deploy/gen-secrets.sh            # creates .env.prod, fills every random secret
nano .env.prod                   # fill DOMAIN, ACME_EMAIL, SIGNUP_ALLOWLIST, R2_*, SMTP_*, ALERT_EMAIL
```

**Copy the whole `.env.prod` into your password manager now.** `BACKUP_ENCRYPTION_KEY` exists
nowhere else: lose it and every backup is permanently unreadable.

If you will load the existing dev database (step 7), set `MFA_ENCRYPTION_KEY` and
`PII_ENCRYPTION_KEY` to the **dev** values first (from the dev `.env`).

## 6. Deploy

```bash
deploy/deploy.sh
```

Builds the images, starts Postgres, applies every migration, then starts the API and Caddy.
Re-run it for every update (`git pull && deploy/deploy.sh`).

## 7. (Optional) load the existing database

The shared tender data (≈11k programmes, 6k awards, 6k organisations) took weeks to ingest;
restoring it avoids starting empty. On your laptop:

```bash
docker exec defence-oi-db-1 pg_dump -U postgres -Fc doi > doi.dump
scp doi.dump deploy@<server-ip>:/opt/doi/
```

On the server:

```bash
deploy/restore-dump.sh doi.dump        # asks you to type "replace"
shred -u doi.dump                      # and delete the laptop copy
```

It brings the schema up to date afterwards. The dump contains the dev test tenants as well;
remove any you don't want.

## 8. Turn Cloudflare on

Cloudflare → SSL/TLS → set mode **Full (strict)**. Then switch the `app` and `api` records to
**Proxied (orange cloud)**. Now set `USE_CLOUDFLARE=yes` in `.env.prod` (it starts as `no` so the first
certificate can be issued) and run `deploy/deploy.sh` again so the origin starts refusing anything that isn't Cloudflare (and
reads real visitor IPs from `CF-Connecting-IP`; otherwise every user would share one rate-limit
bucket).

Then update the SSO consoles — add these as **authorised redirect URIs** (keep the localhost
ones for dev):

- Google: `https://api.<DOMAIN>/auth/oidc/google/callback`
- Microsoft: `https://api.<DOMAIN>/auth/oidc/microsoft/callback`

## 9. Verify

```bash
deploy/verify.sh
```

Checks health, the frontend, `/config.js`, that `/docs` is off, that protected routes need auth,
that a stranger cannot sign up, and that HSTS is on. Then by hand, once:

1. Sign up with an allow-listed email → the verification email **arrives** (not in spam).
2. "Forgot password" → the reset email arrives.
3. Log in with Google and with Microsoft.
4. Log in as a platform admin (set `is_platform_admin = true` on your own user row in the
   database) and call `POST /admin/backup/run`, then `POST /admin/backup/verify-restore`; both
   must succeed against the NEW R2 bucket. `GET /admin/backup/status` shows backup,
   restore-drill and retention health together.

## Day-2 operations

| Task | Command (from `/opt/doi`) |
|---|---|
| Update | `git pull && deploy/deploy.sh` |
| Logs | `docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f api` |
| Status | `docker compose -f docker-compose.prod.yml --env-file .env.prod ps` |
| Allow a new user to sign up | add the email/@domain to `SIGNUP_ALLOWLIST` in `.env.prod`, `deploy/deploy.sh` |
| Restore from a backup | see `db/pitr_restore.sh` (point-in-time) and `CLAUDE.md` (Backup & DR) |

Shortcut: `alias dc='docker compose -f docker-compose.prod.yml --env-file .env.prod'`.

## Things this does NOT do yet

- No monitoring/uptime check: add a free external monitor (UptimeRobot, Better Stack) on
  `https://api.<DOMAIN>/healthz`.
- No Terms of Service / Privacy Policy, billing, or user data export/delete — fine for an
  invite-only pilot, required before taking paying customers.
- Single server: a Hetzner outage means downtime until you restore (backups make it recoverable,
  not highly available).
