#!/bin/bash
# Run on gpu-3 with: sudo bash deploy/install-system.sh
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
    nginx \
    redis-server \
    python3-venv \
    build-essential \
    libmagic1t64 \
    git \
    curl \
    rsync

mkdir -p /var/www/stdweb /var/lib/stdweb/{data,tasks,static,db} /var/log/stdweb
chown -R pyl:pyl /var/www/stdweb /var/lib/stdweb /var/log/stdweb

if [[ -d /home/pyl/repo/stdweb ]]; then
    rsync -a --delete \
        --exclude '.venv' --exclude '__pycache__' --exclude '.git/objects' \
        /home/pyl/repo/stdweb/ /var/www/stdweb/
    chown -R pyl:pyl /var/www/stdweb
    for f in .env db.sqlite3; do
        if [[ -f /home/pyl/repo/stdweb/$f ]]; then
            cp -a /home/pyl/repo/stdweb/$f /var/www/stdweb/
            chown pyl:pyl /var/www/stdweb/$f
        fi
    done
fi

if [[ -f /var/www/stdweb/deploy/nginx-stdweb.conf ]]; then
    rm -f /etc/nginx/sites-enabled/default
    cp /var/www/stdweb/deploy/nginx-stdweb.conf /etc/nginx/sites-available/stdweb
    ln -sf /etc/nginx/sites-available/stdweb /etc/nginx/sites-enabled/stdweb
    nginx -t
    systemctl reload nginx || systemctl restart nginx
    systemctl enable redis-server nginx
    # Stop dev Redis on 6379 (e.g. miniconda) so apt redis-server can bind
    if ss -tlnp | grep -q ':6379'; then
        pkill -u pyl redis-server 2>/dev/null || true
        sleep 1
    fi
    systemctl start redis-server || true
fi

if [[ -f /var/www/stdweb/deploy/stdweb-gunicorn.service ]]; then
    cp /var/www/stdweb/deploy/stdweb-gunicorn.service /etc/systemd/system/
    cp /var/www/stdweb/deploy/stdweb-celery.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable stdweb-gunicorn stdweb-celery
fi

echo "System OK. As user pyl:"
echo "  bash /var/www/stdweb/deploy/setup-app.sh"
echo "  sudo systemctl restart stdweb-gunicorn stdweb-celery nginx"
echo "Cloudflare tunnel service URL: http://localhost:80"
