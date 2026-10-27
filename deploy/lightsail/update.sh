#!/usr/bin/env bash
# Get the latest code and restart Portolan on the server. Keeps the data, .env, portolan.toml
# and the login.   bash deploy/lightsail/update.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
git pull --ff-only
sudo docker compose -f deploy/lightsail/compose.yml up -d --build
sudo docker image prune -f >/dev/null   # drop the old image so the disk doesn't fill up
echo "Updated. Check it with: sudo docker compose -f deploy/lightsail/compose.yml ps"
