# Deploying for free

The whole stack runs on one Oracle Cloud **Always Free** ARM server, behind Caddy, which serves
the dashboard, the gateway's WebSocket and API, and Grafana on one HTTPS host and renews its
own certificate. No domain is needed: `<ip-with-dashes>.sslip.io` resolves to the server and
Let's Encrypt issues certificates for it. Pushes to `main` redeploy automatically once the
GitHub secrets below exist.

```
browser ──https/wss──> Caddy :443 ──> dashboard (static)
                                 ├──> gateway :8080   /ws/*, /symbols, /history/*, /health
                                 └──> grafana :3000   /grafana/
         everything else (NATS, TimescaleDB, Prometheus, /metrics) stays on the Compose network
```

## 1. The server (once, in the Oracle Cloud console)

1. Create an Oracle Cloud account. Pick a home region close to you; outside the US keeps
   Binance reachable. Oracle asks for a card to verify identity; Always Free resources are not
   charged.
2. **Compute → Instances → Create instance**:
   - Image: Canonical Ubuntu 24.04 (aarch64).
   - Shape: Ampere `VM.Standard.A1.Flex`, as many OCPUs and GB as the Always Free allowance
     allows (2 OCPU / 12 GB is plenty).
   - Networking: a public subnet with a public IPv4 address.
   - SSH keys: paste your public key.
   If you see "out of host capacity", try another availability domain or retry later.
3. **Networking → Virtual cloud networks → your VCN → Security lists → Default**: add
   ingress rules for TCP 80 and TCP 443 (and UDP 443 for HTTP/3), source `0.0.0.0/0`.

## 2. Set it up (on the server)

```bash
ssh ubuntu@<public-ip>
curl -fsSLO https://raw.githubusercontent.com/adithya916/real-time-trading-dashboard/main/deploy/oracle-setup.sh
less oracle-setup.sh
bash oracle-setup.sh            # or: bash oracle-setup.sh your.domain
```

The script installs Docker, opens 80/443 in the server's own firewall (Oracle's Ubuntu images
reject everything but SSH), raises connection limits, clones the repository to `~/rtd`, writes
`~/rtd/.env` with the domain and random passwords, and starts everything, live Kraken feeds
included. The first build takes a while on ARM (the C++ core compiles); later ones are cached.

It prints the address when done: `https://<ip-with-dashes>.sslip.io`, with Grafana at
`/grafana/`. The Grafana admin password is in `~/rtd/.env`; visitors get read-only access
without one.

## 3. Deploy on every push (GitHub → Settings → Secrets and variables → Actions)

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | the server's public IP |
| `DEPLOY_USER` | `ubuntu` |
| `DEPLOY_SSH_KEY` | a private key for the server (make a dedicated one: `ssh-keygen -t ed25519 -f deploy_key`, and append `deploy_key.pub` to `~/.ssh/authorized_keys` on the server) |
| `DEPLOY_KNOWN_HOSTS` | output of `ssh-keyscan <public-ip>`, so the workflow refuses an impostor host |
| `DEPLOY_DOMAIN` | the `DOMAIN` from `~/rtd/.env` |

`.github/workflows/deploy.yml` then runs on each push to `main`: it SSHes in, runs
`deploy/remote-deploy.sh` (fetch the pushed commit, rebuild what changed, restart, prune old
images) and checks `https://$DOMAIN/health` and `/symbols`. Without the secrets it reports
"not configured" and passes.

## Checked in CI

The integration job starts this production configuration with `DOMAIN=localhost` (Caddy signs
with its own CA) and checks that only ports 80 and 443 are published, that the dashboard,
`/symbols`, the `wss://` stream and `/grafana/` all answer through Caddy, and that `/metrics`
is not exposed.
