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

### Large data disk (12.7 TB Toshiba on gpu-3)

Production task images belong on **`TASKS_PATH`** (one directory per task ID). The NVMe root disk is only ~465 GB; mount the Toshiba **`/dev/sda2`** at `/var/lib/stdweb` and keep `tasks/` + `data/` there.

**Recommended:** reformat **ext4** (not exFAT) — pipeline output is many small files; exFAT is a poor fit on Linux.

On gpu-3 (requires sudo password):

```bash
# 1) Format (only if the disk has no data you need — destroys sda2)
sudo FORMAT=1 bash ~/repo/stdweb/deploy/gpu3-mount-data-disk.sh

# OR mount existing exFAT for a test (not recommended long-term):
sudo bash ~/repo/stdweb/deploy/gpu3-mount-data-disk.sh

# 2) Copy existing gpu-3 tasks + later the STD archive
sudo MIGRATE_FROM=/var/lib/stdweb/tasks bash ~/repo/stdweb/deploy/gpu3-mount-data-disk.sh

# From STD machine (~2 TiB, run when ready):
rsync -aH --info=progress2 /media/pyl/Expansion/tasks/ pyl@gpu-3:/var/lib/stdweb/tasks/
```

Updates `/var/www/stdweb/.env` (`TASKS_PATH`, `DATA_PATH`) and `/etc/fstab`. Then restart gunicorn + Celery.

Current gpu-3 state (2026-08): **`TASKS_PATH=/var/lib/stdweb/tasks`** (~94 MB); main archive still on STD **`/media/pyl/Expansion/tasks`**. Moving tasks to gpu-3 makes gpu-3 the processing + storage node; keep DB paths consistent (task IDs must match directory names).

## Logs

```bash
tail -f ~/var/log/stdweb/gunicorn.log ~/var/log/stdweb/celery.log
```

## Astro binaries

Pipeline needs `solve-field`, `sex`, `hotpants`, etc. Install on gpu-3 separately (not automated yet).
