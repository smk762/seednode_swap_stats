Production Setup Guide

Goal
- Deploy Seednode Swap Stats on a fresh Linux server using Docker and docker compose.

Assumptions
- Ubuntu 22.04+ (adjust package commands for your distro)
- You will run the FastAPI service behind a reverse proxy (recommended)

1) Create a deployment user (optional but recommended)

```bash
sudo adduser deploy
sudo usermod -aG sudo,docker deploy
newgrp docker
```

2) Install Docker Engine and Compose plugin

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin git
```

3) Clone the repository

```bash
git clone https://github.com/your-org-or-user/seednode_swap_stats.git
cd seednode_swap_stats
```

4) Prepare KDF configuration
- Copy the template and set a strong rpc_password:

```bash
cp MM2.json.template MM2.json
sed -i 's/"rpc_password": ""/"rpc_password": "CHANGE_ME_STRONG_SECRET"/' MM2.json
```

- The compose file mounts the repository root into the KDF container’s working directory (/home/komodian/kdf). KDF will persist data under ./.kdf (bind-mounted into the container at /home/komodian/.kdf).

5) Create .env with production overrides (recommended)
- Place this in the repository root (same dir as docker-compose.yml). Example:

```bash
cat > .env <<'EOF'
# Critical: replace this with a strong secret in production
PUBKEY_HASH_KEY=replace_with_strong_secret

# Optional overrides
#KDF_DB_PATH=/home/komodian/.kdf/DB/<your-hash-here>/MM2.db
#EVENTS_JSON_PATH=/app/events.json
#RETENTION_HOURS=1
#BACKFILL_SINCE=

# Registration settings
#REGISTRATION_DOC_ADDRESS=RGzkzaZcRySBYq4jStV6iVtccztLh51WRt
#REGISTRATION_POLL_SECONDS=180
#REGISTRATION_EXPIRY_HOURS=24
#REGISTRATION_AMOUNT_MIN=0.001
#REGISTRATION_AMOUNT_MAX=3.33
EOF
```

Notes
- KDF_DB_PATH default points to a specific path under /home/komodian/.kdf/DB/... in the container. The host-side ./.kdf directory is mounted there by compose. You typically do not need to change this unless your DB path differs.
- REGISTRATION_DB_PATH defaults to /app/app/DEX_COMP.db inside the api container. Because api/app is bind-mounted, the DB file persists on the host at api/app/DEX_COMP.db.

6) Build and start the stack

```bash
docker compose up -d --build
```

7) Validate the deployment

```bash
# API health
curl http://localhost:8000/healthz

# Tail logs
docker compose logs -f api
docker compose logs -f kdf
```

8) Firewall and exposure
- Expose only what you need:
  - API: 8000/tcp (often fronted by a reverse proxy on 80/443)
  - KDF: 8870 (RPC), 42845/42855 (p2p); keep private unless your topology requires exposure
- Example (UFW):

```bash
sudo ufw allow 80,443/tcp
# If absolutely required (not recommended by default):
# sudo ufw allow 8000/tcp
# sudo ufw allow 8870,42845,42855/tcp
```

9) Reverse proxy (Nginx example)

```nginx
server {
    listen 80;
    server_name your.domain.example;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }
}
```

Harden with HTTPS (e.g., certbot) and enable system services for nginx.

10) Backups
- Persisted state to back up:
  - ./.kdf (entire directory; contains KDF DBs)
  - api/app/DEX_COMP.db (registration DB)

11) Upgrades

```bash
git pull
docker compose pull  # if images are published remotely; skip if building locally
docker compose up -d --build
```

12) Troubleshooting
- API fails to start:
  - Check docker compose logs -f api
  - Ensure KDF_DB_PATH points to a valid SQLite DB created by KDF
  - Ensure events.json is present or EVENTS_JSON_PATH points to a valid file
- KDF not producing DB:
  - Verify MM2.json rpc_password is set
  - Tail docker compose logs -f kdf and inspect ~/kdf/kdf.log inside the container if needed
- Registration flow not updating:
  - Ensure REGISTRATION_DOC_ADDRESS is set and reachable via the configured insight service

13) Service management (systemd optional)
- If you want compose to start on boot, create a systemd unit that runs docker compose up -d in this directory or use a process supervisor of your choice.




