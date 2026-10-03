#!/usr/bin/env bash
# EMAS backend setup — run on the VPS (Ubuntu). Safe alongside other Django
# services: containers are isolated, only 127.0.0.1:8010 is bound, nginx site
# is additive.
set -euo pipefail

REPO="https://github.com/raydanielg/jointexamsback.git"
APP_DIR=/opt/jointexams

echo "== Detecting public IP"
IP=$(curl -4 -s https://api.ipify.org || curl -4 -s https://ipv4.icanhazip.com)
DOMAIN="joint.${IP}.sslip.io"
echo "   Domain: $DOMAIN"

echo "== Installing docker + compose plugin"
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null

echo "== Cloning repo -> $APP_DIR"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi
cd "$APP_DIR"

echo "== Writing .env"
SECRET=$(openssl rand -hex 32)
DBPASS=$(openssl rand -hex 16)
if [ -f deploy/.env ]; then
  echo "   .env already exists — keeping it (delete to regenerate)"
else
  sed -e "s/CHANGE_ME_GENERATE_A_LONG_RANDOM_STRING/$SECRET/" \
      -e "s/CHANGE_ME_STRONG_DB_PASSWORD/$DBPASS/" \
      -e "s/DOMAIN_HERE/$DOMAIN/g" \
      deploy/env.production.template > deploy/.env
fi

echo "== Building + starting containers"
mkdir -p staticfiles media
# container runs as uid 1000 (emas) — it must own the mounted dirs
chown -R 1000:1000 staticfiles media
docker compose -f deploy/docker-compose.prod.yml up -d --build

echo "== Nginx site"
apt-get update -qq && apt-get install -y -qq nginx certbot python3-certbot-nginx
sed "s/DOMAIN_HERE/$DOMAIN/g" deploy/nginx.emas.conf > /etc/nginx/sites-available/jointexams
ln -sf /etc/nginx/sites-available/jointexams /etc/nginx/sites-enabled/jointexams
mkdir -p $APP_DIR/staticfiles $APP_DIR/media
nginx -t && systemctl reload nginx

echo "== HTTPS via certbot"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@$DOMAIN --redirect || \
  echo "   (certbot skipped — run manually later)"

echo ""
echo "DONE → https://$DOMAIN/api/v1/"
echo "Admin: create a superuser with"
echo "  docker compose -f $APP_DIR/deploy/docker-compose.prod.yml exec web python manage.py createsuperuser"
