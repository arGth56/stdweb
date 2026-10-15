#!/usr/bin/env python3
"""Dump authenticated STDWeb pages to /tmp for onboarding screenshots."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stdweb.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client

HOST = {"HTTP_HOST": "stdweb.org.uk"}
OUT = Path("/tmp")
User = get_user_model()


def dump(path: str, dest: Path, client: Client) -> None:
    r = client.get(path, **HOST)
    if r.status_code != 200:
        raise SystemExit(f"{path} -> {r.status_code}")
    dest.write_text(r.content.decode("utf-8", errors="replace"), encoding="utf-8")
    print(dest.name, len(r.content))


def main() -> None:
    anon = Client()
    dump("/login/", OUT / "onboard_login.html", anon)

    user = User.objects.get(username="pyl")
    auth = Client()
    auth.force_login(user)

    dump("/", OUT / "onboard_index.html", auth)
    dump("/tasks/3747/", OUT / "onboard_t3747.html", auth)
    dump("/tasks/4848/", OUT / "onboard_t4848.html", auth)
    dump("/tasks/4850/", OUT / "onboard_t4850.html", auth)


if __name__ == "__main__":
    main()
