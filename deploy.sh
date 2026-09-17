#!/usr/bin/env bash
# Build the image off-server, ship it to the Docker host, bring the stack up.
# The Pi never builds (920 MiB RAM); it only loads the prebuilt image.
#
# Usage:
#   ./deploy.sh                                   # host=pi, path=~/invoices
#   DEPLOY_HOST=pi DEPLOY_PATH=~/invoices ./deploy.sh
#
# First deploy: the server directory needs .env and config/sender.toml.
# See README.md, "Deployment".
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-pi}"
DEPLOY_PATH="${DEPLOY_PATH:-~/invoices}"
APP_IMAGE="${APP_IMAGE:-invoice-of-reason:latest}"

# GoBD Rz. 154 (Programmidentität): the running version must match a commit that stays in git
# history. The app records every version change in its database. See scripts/deploy-check.sh.
APP_VERSION="$("$(dirname "$0")/scripts/deploy-check.sh")"
echo ">> Version $APP_VERSION"

map_arch() { sed 's/x86_64/amd64/;s/aarch64/arm64/;s/armv7l/arm\/v7/;s/armv6l/arm\/v6/'; }

REMOTE_ARCH="$(ssh "$DEPLOY_HOST" 'uname -m')"
PLATFORM="linux/$(echo "$REMOTE_ARCH" | map_arch)"
LOCAL_PLATFORM="linux/$(uname -m | map_arch)"
echo ">> $DEPLOY_HOST is $REMOTE_ARCH -> $PLATFORM"

if [ "$PLATFORM" != "$LOCAL_PLATFORM" ]; then
  echo ">> Registering qemu binfmt for cross-build"
  docker run --privileged --rm tonistiigi/binfmt --install arm,arm64 >/dev/null
fi

echo ">> Running tests inside a $PLATFORM image"
docker build --platform "$PLATFORM" --target test -t "${APP_IMAGE%:*}:test" .

echo ">> Building $APP_IMAGE for $PLATFORM"
docker build --platform "$PLATFORM" --target runtime --build-arg APP_VERSION="$APP_VERSION" -t "$APP_IMAGE" .

echo ">> Checking server prerequisites"
ssh "$DEPLOY_HOST" "mkdir -p $DEPLOY_PATH/data $DEPLOY_PATH/backups $DEPLOY_PATH/config \
  && test -f $DEPLOY_PATH/.env || { echo 'missing $DEPLOY_PATH/.env (see .env.example)'; exit 1; } \
  && test -f $DEPLOY_PATH/config/sender.toml || { echo 'missing $DEPLOY_PATH/config/sender.toml'; exit 1; }"

echo ">> Shipping image and compose file to $DEPLOY_HOST"
docker save "$APP_IMAGE" | gzip | ssh "$DEPLOY_HOST" 'gunzip | docker load'
scp docker-compose.yml "$DEPLOY_HOST:$DEPLOY_PATH/docker-compose.yml"

echo ">> Starting the stack on $DEPLOY_HOST:$DEPLOY_PATH"
ssh "$DEPLOY_HOST" "cd $DEPLOY_PATH && docker compose up -d && docker image prune -f >/dev/null"
ssh "$DEPLOY_HOST" "for i in \$(seq 1 30); do curl -fsS http://127.0.0.1:8082/healthz >/dev/null && echo '>> Healthy' && exit 0; sleep 2; done; echo 'health check failed'; exit 1"
