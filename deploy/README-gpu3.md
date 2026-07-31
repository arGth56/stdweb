# gpu-3 deployment (stdweb.org.uk)

## Done without sudo

- Code: `~/repo/stdweb`, stdpipe: `~/repo/stdpipe`
- venv, Redis (miniconda), gunicorn on **127.0.0.1:8000**, Celery worker
- DB migrated (copied from dev machine), static files in `~/var/lib/stdweb/static`
- Cloudflare tunnel connector: `cloudflared` (system service)

## Required: match tunnel to gunicorn

The Cloudflare route may still point to `http://localhost:80`. Gunicorn listens on **8000**.

**Cloudflare → Tunnels → your tunnel → Routes → edit `stdweb.org.uk`:**

- Service URL: `http://localhost:8000`

Or install nginx on port 80 (needs sudo):

```bash
sudo bash ~/repo/stdweb/deploy/install-system.sh
bash /var/www/stdweb/deploy/setup-app.sh
sudo systemctl enable --now stdweb-gunicorn stdweb-celery
sudo systemctl reload nginx
```

## Production layout (after `install-system.sh`)

| Path | Purpose |
|------|---------|
| `/var/www/stdweb` | application |
| `/var/lib/stdweb/{data,tasks,static}` | data |
| `/var/log/stdweb/` | logs |

## Logs

```bash
tail -f ~/var/log/stdweb/gunicorn.log ~/var/log/stdweb/celery.log
```

## Astro binaries

Pipeline needs `solve-field`, `sex`, `hotpants`, etc. Install on gpu-3 separately (not automated yet).
