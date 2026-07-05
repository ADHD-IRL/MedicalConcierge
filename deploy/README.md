# Deploying with Docker Compose

This runs the Medical Concierge ingestion MVP behind a Caddy reverse proxy,
matching the hosting recommendation in `docs/ARCHITECTURE.md` section 7:
**self-hosted, LAN + personal VPN only, never public internet.**

## Stack

- `app` — the FastAPI backend (`backend/Dockerfile`), SQLite data on a named
  volume (`medconcierge-data`) so it survives container rebuilds.
- `proxy` — Caddy (`deploy/Caddyfile`), terminates TLS and reverse-proxies to
  `app`. Uses Caddy's internal CA (self-signed) since this isn't meant to
  have a public domain — see "Trusting the internal CA" below.

## Prerequisites

- Docker + Docker Compose plugin on the host (home server/NAS or a personal
  VPS — see `docs/ARCHITECTURE.md` section 7 for the tradeoffs).
- [Tailscale](https://tailscale.com) (or WireGuard) installed on the host and
  on any device you'll use this from, so the app is never exposed to the
  public internet. Do not open ports 80/443 on a public-facing firewall/
  router for this.

## Setup

```bash
echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" > backend/.env
# (all other settings have sensible defaults - see backend/app/config.py)

docker compose build
docker compose up -d
docker compose logs -f app   # confirm it started cleanly
```

The app is now reachable at `https://<host>/` over the Tailscale network (or
your LAN). `docker compose ps` should show both `app` and `proxy` healthy.

## Trusting the internal CA (first run only)

Caddy's `tls internal` directive mints its own self-signed cert, so browsers
will warn about it until you trust the root cert once:

```bash
docker compose exec proxy cat /data/caddy/pki/authorities/local/root.crt
```

Import that file into your OS/browser trust store on each device you'll use.

**Simpler alternative:** skip the `proxy` service entirely and use
`tailscale serve https:443 / http://localhost:8000` pointed at the `app`
container's published port instead — Tailscale issues a real cert for your
tailnet's MagicDNS name and there's no custom CA to distribute. Trade-off:
you lose the security headers and gzip encoding Caddy adds, and you're
depending on Tailscale's proxy instead of one you control. Either is fine for
a single-user deployment; Caddy is the default here because it works the
same way regardless of whether you're using Tailscale or a plain LAN/VPN.

## Backups

Everything that matters lives in the `medconcierge-data` named volume
(the SQLite database of normalized records). Back it up like any other
Docker volume, and encrypt the backup at rest per
`docs/ARCHITECTURE.md` section 5:

```bash
docker run --rm -v medconcierge-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/medconcierge-data-$(date +%F).tar.gz -C /data .
```

## Updating

```bash
git pull
docker compose build
docker compose up -d
```

The named volume is untouched by rebuilds, so your data persists across
updates.
