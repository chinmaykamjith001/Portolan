#!/usr/bin/env bash
# Set up Portolan on a fresh Ubuntu 24.04 server (AWS Lightsail) and start it.
# Run from anywhere inside the clone:   bash deploy/lightsail/setup.sh
#
# Safe to run again: it keeps an existing .env, portolan.toml and login, and only rebuilds.
# Walkthrough: docs/deploy-lightsail.md
set -euo pipefail

cd "$(dirname "$0")/../.."
HERE=deploy/lightsail
COMPOSE=(sudo docker compose -f "$HERE/compose.yml")

say() { printf '\n== %s\n' "$*"; }

# 1. Docker, from Docker's own apt repository (Ubuntu's compose can be too old for compose.yml).
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker"
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# 2. Swap: building the frontend can briefly need more memory than a 1-2 GB server has.
if [ -z "$(swapon --show)" ]; then
  say "Adding 2 GB of swap"
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# 3. .env: you paste the key yourself; this script never asks for it or prints it.
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i 's/^PORTOLAN_LLM_PROVIDER=.*/PORTOLAN_LLM_PROVIDER=gemini/' .env
fi
chmod 600 .env
if ! grep -Eq '^GEMINI_API_KEY=[^[:space:]]+' .env; then
  say "Add your Gemini key, then run this script again"
  echo "  nano .env"
  echo "  Paste your key right after GEMINI_API_KEY= , save with Ctrl+O then Enter, exit with Ctrl+X."
  exit 1
fi

# 4. portolan.toml with the free-tier models (edit it later to change them).
if [ ! -f portolan.toml ]; then
  sed -e 's#gemini/<flash-lite-model-id>#gemini/gemini-3.5-flash-lite#' \
      -e 's#gemini/<flash-model-id>#gemini/gemini-3.8-flash#g' \
      portolan.toml.example > portolan.toml
fi

# 5. Caddyfile: the site's address and its login. Without a domain of your own, the address is
#    <your-ip-with-dashes>.sslip.io, a free name that points at your IP, so HTTPS works.
if [ ! -f "$HERE/Caddyfile" ]; then
  say "Choosing the site address and login"
  if [ -n "${PORTOLAN_DOMAIN:-}" ]; then
    domain=$PORTOLAN_DOMAIN
  else
    ip=$(curl -fsS https://checkip.amazonaws.com)
    domain="$(echo "$ip" | tr . -).sslip.io"
  fi
  while :; do
    read -rp "Login name for the site [portolan]: " user
    user=${user:-portolan}
    [[ $user =~ ^[A-Za-z0-9_.-]+$ ]] && break
    echo "Use letters, digits, '.', '_' or '-' only."
  done
  while :; do
    read -rsp "Password for the site (12 or more characters): " pw; echo
    read -rsp "Same password again: " pw2; echo
    [ "$pw" = "$pw2" ] && [ "${#pw}" -ge 12 ] && break
    echo "The passwords differ or are shorter than 12 characters. Try again."
  done
  # Only the bcrypt hash is stored. (--plaintext is briefly visible to other users of this
  # server in the process list; a Lightsail instance has no other users.)
  hash=$(sudo docker run --rm caddy:2 caddy hash-password --plaintext "$pw")
  unset pw pw2
  cat > "$HERE/Caddyfile" <<EOF
$domain {
	basic_auth {
		$user $hash
	}
	reverse_proxy portolan:8000
}
EOF
  chmod 600 "$HERE/Caddyfile"
fi
domain=$(head -n 1 "$HERE/Caddyfile" | cut -d ' ' -f 1)

# 6. Build and start. The first build takes several minutes.
say "Building and starting Portolan"
"${COMPOSE[@]}" up -d --build

say "Waiting for the app to come up"
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T portolan python -c \
      "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" \
      >/dev/null 2>&1; then
    "${COMPOSE[@]}" exec -T portolan portolan doctor || true
    say "Portolan is running at https://$domain"
    echo "  Log in with the name and password you chose. The first visit can take a minute"
    echo "  while Caddy gets the HTTPS certificate."
    exit 0
  fi
  sleep 5
done
echo "The app did not answer within 5 minutes. Its log:"
"${COMPOSE[@]}" logs --tail 50 portolan
exit 1
