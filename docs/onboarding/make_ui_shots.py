#!/usr/bin/env python3
"""Capture focused STDWeb UI shots — one section per step, controls highlighted."""
from __future__ import annotations

import http.server
import json
import re
import shutil
import socketserver
import subprocess
import threading
import time
from pathlib import Path

CHROME = shutil.which("chromium-browser") or shutil.which("chromium")
ROOT = Path("/tmp/onboard_shots_www")
OUT = Path("/home/pyl/repo/stdweb/stdweb/static/onboarding")
HTML_SRC = Path("/tmp")

SHOTS = [
    {
        "name": "login",
        "html": "onboard_login.html",
        "mode": "login",
        "sels": ["#email", "form[action='/login/'] button[type='submit']"],
        "anon_profile": True,
    },
    {
        "name": "upload",
        "html": "onboard_index.html",
        "upload_only": True,
        "sels": ["#div_id_file", "#submit-id-upload"],
    },
    {
        "name": "target",
        "html": "onboard_t3747.html",
        "section": "Initial inspection and masking",
        "target_only": True,
        "minimal_header": True,
        "sels": ["#div_id_target"],
    },
    {
        "name": "inspect",
        "html": "onboard_t4848.html",
        "section": "Initial inspection and masking",
        "trim_logs": True,
        "inspect_only": True,
        "sels": ["#div_id_gain", "button[data-onboard='inspect']"],
    },
    {
        "name": "photometry",
        "html": "onboard_t4848.html",
        "section": "Photometry and astrometry",
        "photometry_only": True,
        "sels": ["#div_id_filter", "button[data-onboard='photometry']"],
    },
    {
        "name": "results",
        "html": "onboard_t4850.html",
        "mode": "results",
        "sels": ["#ob-mag-line", "h3.ob-cutouts", ".ob-cutouts figure"],
    },
    {
        "name": "subtraction",
        "html": "onboard_t4850.html",
        "section": "Template subtraction",
        "subtraction_only": True,
        "sels": ["#div_id_subtraction_method", "button[data-onboard='subtraction']"],
    },
]

INJECT_CSS = """
<style id="ob-shot-css">
#ob-overlay, #ob-pill { display: none !important; }
.ob-hidden { display: none !important; }
.ob-shot-box {
  position: fixed;
  border: 3px solid #e03131;
  border-radius: 6px;
  pointer-events: none;
  z-index: 20000;
}
.ob-shot-pointer {
  position: fixed;
  z-index: 20001;
  width: 28px;
  height: 28px;
  pointer-events: none;
}
main { padding-bottom: 1.5rem !important; }
footer.obsbs-host-band { margin-top: 0 !important; }
</style>
"""

INJECT_JS = r"""
<script id="ob-shot-js">
(function () {
  var cfg = window.OB_SHOT || {};

  function hideChrome() {
    ['ob-overlay', 'ob-pill'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.hidden = true;
    });
  }

  function h3ByText(text) {
    var nodes = document.querySelectorAll('main h3');
    for (var i = 0; i < nodes.length; i++) {
      if ((nodes[i].textContent || '').indexOf(text) >= 0) return nodes[i];
    }
    return null;
  }

  function hideOutsideSection(sectionTitle) {
    var h3 = h3ByText(sectionTitle);
    var main = document.querySelector('main');
    if (!h3 || !main) return;
    var kids = Array.from(main.children);
    var start = -1;
    for (var i = 0; i < kids.length; i++) {
      if (kids[i] === h3 || (kids[i].contains && kids[i].contains(h3))) {
        start = i;
        break;
      }
    }
    if (start < 0) return;
    var end = kids.length;
    for (var j = start + 1; j < kids.length; j++) {
      if (kids[j].tagName === 'H3') {
        end = j;
        break;
      }
    }
    for (var k = 0; k < kids.length; k++) {
      if (k >= start && k < end) continue;
      if (k < start && k <= 2) continue;
      kids[k].classList.add('ob-hidden');
    }
    if (cfg.trim_logs) {
      for (var t = start + 1; t < end; t++) {
        var node = kids[t];
        if (node.querySelector && (node.querySelector('pre') || node.querySelector('.alert'))) {
          node.classList.add('ob-hidden');
        }
      }
    }
  }

  function hideMarketing() {
    document.querySelectorAll('main > p, main > hr').forEach(function (el) {
      el.classList.add('ob-hidden');
    });
  }

  function hideExceptLogin() {
    var main = document.querySelector('main');
    if (!main) return;
    Array.from(main.children).forEach(function (child) {
      if (!child.querySelector('form[action="/login/"], form[action=\'/login/\']')) {
        child.classList.add('ob-hidden');
      }
    });
  }

  function hidePopovers() {
    document.querySelectorAll('.span-help-popover').forEach(function (el) {
      el.classList.add('ob-hidden');
    });
  }

  function hideFooter() {
    document.querySelectorAll('footer.obsbs-host-band').forEach(function (el) {
      el.classList.add('ob-hidden');
    });
  }

  function minimalHeader() {
    var main = document.querySelector('main');
    if (!main) return;
    Array.from(main.children).slice(1, 3).forEach(function (el) {
      el.classList.add('ob-hidden');
    });
  }

  function uploadOnly() {
    var main = document.querySelector('main');
    if (!main) return;
    var h3 = h3ByText('Image upload');
    if (h3) {
      Array.from(main.children).forEach(function (child) {
        if (child === h3 || (child.contains && child.contains(h3))) return;
        child.classList.add('ob-hidden');
      });
    }
    var form = document.querySelector('#submit-id-upload');
    form = form && form.closest('form');
    if (!form) return;
    form.querySelectorAll('.row').forEach(function (row) {
      if (row.querySelector('#div_id_file') || row.querySelector('#submit-id-upload')) {
        row.querySelectorAll('[class*="col-"]').forEach(function (col) {
          if (!col.querySelector('#div_id_file') && !col.querySelector('#submit-id-upload')) {
            col.classList.add('ob-hidden');
          }
        });
        return;
      }
      row.classList.add('ob-hidden');
    });
  }

  function subtractionOnly() {
    var form = document.querySelector('button[data-onboard="subtraction"]');
    form = form && form.closest('form');
    if (!form) return;
    form.querySelectorAll('.row').forEach(function (row) {
      if (row.querySelector('[data-onboard="subtraction"]')) return;
      var cols = row.querySelectorAll('[class*="col-"]');
      if (!cols.length) {
        row.classList.add('ob-hidden');
        return;
      }
      cols.forEach(function (col) {
        if (!col.querySelector('#div_id_subtraction_method')) col.classList.add('ob-hidden');
      });
    });
  }

  function inspectOnly() {
    var form = document.querySelector('button[data-onboard="inspect"]');
    form = form && form.closest('form');
    if (!form) return;
    form.querySelectorAll('.row').forEach(function (row) {
      if (row.querySelector('[data-onboard="inspect"]')) return;
      var cols = row.querySelectorAll('[class*="col-"]');
      if (!cols.length) return;
      cols.forEach(function (col) {
        if (!col.querySelector('#div_id_gain')) col.classList.add('ob-hidden');
      });
      if (row.querySelectorAll('[class*="col-"]:not(.ob-hidden)').length === 0) {
        row.classList.add('ob-hidden');
      }
    });
    document.querySelectorAll('button[name="action"]').forEach(function (btn) {
      if (btn.getAttribute('data-onboard') !== 'inspect') btn.classList.add('ob-hidden');
    });
    document.querySelectorAll('.alert').forEach(function (el) {
      el.classList.add('ob-hidden');
    });
  }

  function photometryOnly() {
    var btn = document.querySelector('button[data-onboard="photometry"]');
    var form = btn && btn.closest('form');
    if (!form) return;
    form.querySelectorAll('.row').forEach(function (row) {
      if (!row.querySelector('#div_id_filter') && !row.querySelector('[data-onboard="photometry"]')) {
        row.classList.add('ob-hidden');
      }
    });
    var filterRow = document.querySelector('#div_id_filter');
    filterRow = filterRow && filterRow.closest('.row');
    if (filterRow) {
      filterRow.querySelectorAll('[class*="col-"]').forEach(function (col) {
        if (!col.querySelector('#div_id_filter')) col.classList.add('ob-hidden');
      });
    }
  }

  function targetOnly() {
    ['#div_id_gain', '#div_id_time', '#div_id_saturation', '#div_id_mask_cosmics'].forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        var col = el.closest('[class*="col-"]') || el.closest('.mb-1') || el;
        col.classList.add('ob-hidden');
      });
    });
    document.querySelectorAll('form button[name="action"]').forEach(function (btn) {
      btn.classList.add('ob-hidden');
    });
  }

  function unhideChain(el) {
    var node = el;
    while (node) {
      node.classList.remove('ob-hidden');
      node = node.parentElement;
    }
  }

  function markResults() {
    var main = document.querySelector('main');
    if (!main) return;
    Array.from(main.children).forEach(function (child) {
      child.classList.add('ob-hidden');
    });
    Array.from(main.children).slice(0, 3).forEach(function (child) {
      child.classList.remove('ob-hidden');
    });
    document.querySelectorAll('pre').forEach(function (pre) {
      if ((pre.textContent || '').indexOf('Primary target magnitude') < 0) return;
      var line = (pre.textContent || '').split('\n').filter(function (l) {
        return l.indexOf('Primary target magnitude') >= 0;
      })[0] || '';
      pre.textContent = line.trim();
      pre.innerHTML = '<span id="ob-mag-line">' + line.trim() + '</span>';
      unhideChain(pre);
      var row = pre.closest('.row');
      if (row) {
        row.querySelectorAll('.col').forEach(function (col) {
          if (!col.contains(pre)) col.classList.add('ob-hidden');
        });
      }
    });
    var cutH3 = h3ByText('Target in direct image');
    if (cutH3) {
      cutH3.classList.add('ob-cutouts');
      unhideChain(cutH3);
      var node = cutH3.nextElementSibling;
      var shown = 0;
      while (node && node.tagName !== 'H3' && shown < 2) {
        unhideChain(node);
        node.classList.add('ob-cutouts');
        if (node.querySelector('figure, img')) shown += 1;
        node = node.nextElementSibling;
      }
    }
    minimalHeader();
    hideFooter();
  }

  function unionRect(els) {
    var u = null;
    els.forEach(function (el) {
      var r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return;
      if (!u) u = { l: r.left, t: r.top, r: r.right, b: r.bottom };
      else {
        u.l = Math.min(u.l, r.left);
        u.t = Math.min(u.t, r.top);
        u.r = Math.max(u.r, r.right);
        u.b = Math.max(u.b, r.bottom);
      }
    });
    return u;
  }

  function boxFor(el) {
    var r = el.getBoundingClientRect();
    var b = document.createElement('div');
    b.className = 'ob-shot-box';
    b.style.left = (r.left - 8) + 'px';
    b.style.top = (r.top - 8) + 'px';
    b.style.width = (r.width + 16) + 'px';
    b.style.height = (r.height + 16) + 'px';
    document.body.appendChild(b);
    return r;
  }

  function pointerAt(r) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'ob-shot-pointer');
    svg.setAttribute('viewBox', '0 0 32 32');
    svg.style.left = (r.left + 16) + 'px';
    svg.style.top = (r.top + r.height * 0.45) + 'px';
    svg.innerHTML = '<path fill="#fff" stroke="#111" stroke-width="1.5" d="M4 3 l16 12 -7 1 4 11 -4 2 -4-11 -5 6 z"/>';
    document.body.appendChild(svg);
  }

  function pinFocus(els) {
    if (!els.length) return;
    els[0].scrollIntoView({ block: 'start', behavior: 'instant' });
    var nav = document.querySelector('nav.navbar');
    var navH = nav ? nav.getBoundingClientRect().height : 56;
    window.scrollBy(0, -(navH + 14));
  }

  function run() {
    hideChrome();
    hidePopovers();
    hideFooter();
    if (cfg.mode === 'login') hideExceptLogin();
    else if (cfg.mode === 'results') markResults();
    else if (cfg.section) hideOutsideSection(cfg.section);
    if (cfg.hide_marketing) hideMarketing();
    if (cfg.minimal_header) minimalHeader();
    if (cfg.target_only) targetOnly();
    if (cfg.inspect_only) inspectOnly();
    if (cfg.photometry_only) photometryOnly();
    if (cfg.upload_only) uploadOnly();
    if (cfg.subtraction_only) subtractionOnly();

    var els = [];
    (cfg.sels || []).forEach(function (sel) {
      document.querySelectorAll(sel).forEach(function (el) {
        if (el && !el.classList.contains('ob-hidden') && !el.closest('.ob-hidden')) els.push(el);
      });
    });
    if (!els.length) {
      document.documentElement.setAttribute('data-ob-shot', 'missing');
      return;
    }
    pinFocus(els);
    els.forEach(boxFor);
    pointerAt(els[0].getBoundingClientRect());
    document.documentElement.setAttribute('data-ob-shot', 'ok');
  }

  function whenReady(cb) {
    function go() { setTimeout(cb, 650); }
    if (document.readyState === 'complete') go();
    else window.addEventListener('load', go);
  }
  whenReady(run);
})();
</script>
"""


def sanitize_login_html(html: str) -> str:
    html = re.sub(
        r'<li class="nav-item dropdown">.*?</li>\s*',
        '<li class="nav-item"><a class="nav-link" href="/signup/">Sign up</a></li>\n'
        '<li class="nav-item"><a class="nav-link" href="/login/">Log in</a></li>\n',
        html,
        count=1,
        flags=re.DOTALL,
    )
    return html


def prepare(src: Path, dest: Path, shot: dict) -> None:
    html = src.read_text(encoding="utf-8", errors="replace")
    if shot["name"] == "login":
        html = sanitize_login_html(html)
    for needle in ('src="/static/onboarding.js"', "src='/static/onboarding.js'"):
        html = html.replace(needle, "")
    cfg = {k: shot[k] for k in (
        "sels", "section", "mode", "trim_logs", "target_only", "hide_marketing",
        "minimal_header", "inspect_only", "photometry_only", "upload_only", "subtraction_only",
    ) if k in shot}
    inject = INJECT_CSS + f"<script>window.OB_SHOT = {json.dumps(cfg)};</script>" + INJECT_JS
    html = html.replace("</body>", inject + "\n</body>", 1) if "</body>" in html else html + inject
    dest.write_text(html, encoding="utf-8")


def start_server(root: Path) -> tuple[socketserver.TCPServer, int]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, *args):
            pass

    httpd = socketserver.TCPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def screenshot(url: str, dest: Path, height: int = 760, anon: bool = False) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    profile = "/tmp/chrome-anon-login" if anon else "/home/pyl/snap/chromium/common/chromium-headless"
    cmd = [
        CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        f"--window-size=1280,{height}", "--force-device-scale-factor=1",
        f"--screenshot={dest.name}", "--virtual-time-budget=6000",
        f"--user-data-dir={profile}", url,
    ]
    subprocess.run(cmd, check=True, cwd=str(dest.parent), stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=45)


def main() -> None:
    if not CHROME:
        raise SystemExit("chromium not found")
    ROOT.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for shot in SHOTS:
        src = HTML_SRC / shot["html"]
        if not src.exists():
            raise SystemExit(f"missing {src}")
        prepare(src, ROOT / f"{shot['name']}.html", shot)

    httpd, port = start_server(ROOT)
    time.sleep(0.2)
    try:
        for shot in SHOTS:
            png = OUT / f"{shot['name']}.png"
            screenshot(
                f"http://127.0.0.1:{port}/{shot['name']}.html",
                png,
                height=760 if shot["name"] != "results" else 900,
                anon=shot.get("anon_profile", False),
            )
            print("shot", shot["name"], png.stat().st_size)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
