"""Automated TNS lookup (cone search + name) for telegram / lightcurve pages."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import timedelta

from django.utils.timezone import now

from . import models

log = logging.getLogger(__name__)

TNS_NAME = re.compile(r'^(?:SN|AT)[\s_]*(\d{4}[a-z]{1,10})$', re.I)
DISCOVERY_ISO = re.compile(
    r'Discovery date(?:\s*\(UT\))?\s*:\s*'
    r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)',
    re.I,
)
DISCOVERY_JD = re.compile(r'JD\s*=\s*(24\d{5}\.\d+)', re.I)
CACHE_HOURS = 24
TNS_HEADERS = {
    'User-Agent': 'stdweb.org.uk photometry (ObsBS RAPAS; https://stdweb.org.uk)',
}
# Public TNS HTML search defaults to a short discovery window and can omit
# unclassified ATs unless these flags are set. EP follow-up is often >30 days old.
TNS_SEARCH_DEFAULTS = {
    'unclassified_at': '1',
    'classified_sne': '1',
    'include_frb': '1',
    'discovered_period_value': '24',
    'discovered_period_units': 'months',
    'num_page': '50',
}
DEFAULT_CONE_ARCSEC = 60


def tns_bare_name(name):
    text = str(name or '').strip()
    if not text:
        return None
    compact = re.sub(r'[\s_]+', '', text)
    match = TNS_NAME.match(compact)
    if match:
        return match.group(1).lower()
    match = TNS_NAME.match(text)
    if match:
        return match.group(1).lower()
    return None


def _iso_to_mjd(iso):
    if not iso:
        return None
    try:
        from astropy.time import Time
        return float(Time(iso.replace('T', ' ')).mjd)
    except Exception:
        return None


def _sexagesimal_to_deg(ra_s, dec_s):
    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        c = SkyCoord(f'{ra_s} {dec_s}', unit=(u.hourangle, u.deg))
        return float(c.ra.deg), float(c.dec.deg)
    except Exception:
        try:
            return float(ra_s), float(dec_s)
        except (TypeError, ValueError):
            return None, None


def _parse_csv_row(row):
    name = (row.get('Name') or '').strip()
    iso = (row.get('Discovery Date (UT)') or '').strip()
    ra_s, dec_s = row.get('RA'), row.get('DEC')
    ra, dec = _sexagesimal_to_deg(ra_s, dec_s)
    bare = tns_bare_name(name)
    mag = (row.get('Discovery Mag/Flux') or '').strip()
    try:
        mag_f = float(mag) if mag else None
    except ValueError:
        mag_f = None
    return {
        'tns_name': name[:80],
        'display_name': name[:80],
        'obj_type': (row.get('Obj. Type') or '').strip()[:50],
        'host_name': (row.get('Host Name') or '').strip()[:120],
        'redshift': (row.get('Redshift') or '').strip()[:20],
        'disc_mag': mag_f,
        'disc_filter': (row.get('Discovery Filter') or '').strip()[:40],
        'reporting_group': (row.get('Reporting Group/s') or '').strip()[:120],
        'discovery_iso': iso[:50],
        'discovery_mjd': _iso_to_mjd(iso),
        'tns_ra': ra,
        'tns_dec': dec,
        'tns_url': f'https://www.wis-tns.org/object/{bare}' if bare else '',
        'source': 'tns',
    }


def _tns_csv(params, near=None):
    """Query the public TNS CSV search.

    Returns
    -------
    ('ok', info_dict or None)
        Search completed; info is the closest (or first) row, or None if empty.
    ('error', message)
        Network / rate-limit / HTML block — not a real empty cone.
    """
    import urllib.parse
    import requests

    url = 'https://www.wis-tns.org/search?' + urllib.parse.urlencode(
        {**TNS_SEARCH_DEFAULTS, **params, 'format': 'csv'}
    )
    try:
        r = requests.get(url, headers=TNS_HEADERS, timeout=20, allow_redirects=True)
    except Exception as exc:
        log.warning('TNS CSV failed %s: %s', url, exc)
        return 'error', str(exc)
    if r.status_code == 429:
        log.warning('TNS rate limited: %s', url)
        return 'error', 'TNS HTTP 429 (rate limited)'
    if r.status_code != 200 or len(r.content) < 20:
        return 'error', f'TNS HTTP {r.status_code}'
    ctype = (r.headers.get('content-type') or '').lower()
    text = r.content.lstrip(b'\xef\xbb\xbf').decode('utf-8', errors='replace')
    if 'too many requests' in text.lower():
        return 'error', 'TNS HTTP 429 (rate limited)'
    if '"Name"' not in text and 'Name' not in text.split('\n', 1)[0]:
        return 'error', f'TNS returned {ctype or "non-CSV"}'
    rows = [_parse_csv_row(row) for row in csv.DictReader(io.StringIO(text)) if row.get('Name')]
    if not rows:
        return 'ok', None
    if near and len(rows) > 1:
        ra0, dec0 = near
        def _key(info):
            if info.get('tns_ra') is None or info.get('tns_dec') is None:
                return 1e9
            return _sep_arcsec(ra0, dec0, info['tns_ra'], info['tns_dec'])
        rows.sort(key=_key)
    return 'ok', rows[0]


def _sep_arcsec(ra1, dec1, ra2, dec2):
    import math
    dra = (float(ra1) - float(ra2)) * 3600.0 * math.cos(
        math.radians(0.5 * (float(dec1) + float(dec2)))
    )
    ddec = (float(dec1) - float(dec2)) * 3600.0
    return math.hypot(dra, ddec)


def format_offset(arcsec):
    if arcsec is None:
        return ''
    x = float(arcsec)
    if abs(x) >= 60:
        return f'{x / 60:.1f}′'
    return f'{x:.2f}″'


def _info_to_match(info, ra, dec, radius_arcsec, via):
    offset = None
    if info.get('tns_ra') is not None and info.get('tns_dec') is not None:
        offset = _sep_arcsec(ra, dec, info['tns_ra'], info['tns_dec'])
    on_target = offset is None or offset <= float(radius_arcsec)
    return {
        'matched': True,
        'on_target': bool(on_target),
        'via': via,
        'radius_arcsec': float(radius_arcsec),
        'target_ra': float(ra),
        'target_dec': float(dec),
        'tns_name': info.get('tns_name') or '',
        'obj_type': info.get('obj_type') or '',
        'host_name': info.get('host_name') or '',
        'redshift': info.get('redshift') or '',
        'disc_mag': info.get('disc_mag'),
        'disc_filter': info.get('disc_filter') or '',
        'reporting_group': info.get('reporting_group') or '',
        'discovery_iso': (info.get('discovery_iso') or '')[:19],
        'tns_url': info.get('tns_url') or '',
        'tns_ra': info.get('tns_ra'),
        'tns_dec': info.get('tns_dec'),
        'offset_arcsec': None if offset is None else round(float(offset), 2),
        'offset_s': format_offset(offset),
    }


def match_target(ra, dec, name='', radius_arcsec=DEFAULT_CONE_ARCSEC):
    """Does a TNS alert coincide with this photometry position?"""
    ra, dec = float(ra), float(dec)
    radius_arcsec = float(radius_arcsec)
    empty = {
        'matched': False,
        'on_target': False,
        'via': '',
        'query_ok': True,
        'query_error': '',
        'radius_arcsec': radius_arcsec,
        'target_ra': ra,
        'target_dec': dec,
        'tns_name': '',
        'offset_arcsec': None,
        'offset_s': '',
        'tns_url': (
            'https://www.wis-tns.org/search?'
            f'ra={ra}&decl={dec}&radius={radius_arcsec:.0f}&coords_unit=arcsec'
        ),
    }
    try:
        meta = ensure_alert_meta(name, ra=ra, dec=dec, radius_arcsec=radius_arcsec)
        if meta is None:
            return empty
        if getattr(meta, 'source', '') == 'tns_error':
            empty['query_ok'] = False
            empty['query_error'] = (meta.reporting_group or 'TNS query failed')[:120]
            return empty
        payload = tns_payload(meta)
        if payload and payload.get('tns_name'):
            return _info_to_match(payload, ra, dec, radius_arcsec, 'tns')
    except Exception as exc:
        log.warning('TNS match failed: %s', exc)
        empty['query_ok'] = False
        empty['query_error'] = str(exc)[:120]
    return empty


def attach_tns_to_config(config, log=None, image_path=None):
    return attach_alerts_to_config(config, log=log, image_path=image_path)


def attach_alerts_to_config(config, log=None, image_path=None):
    """After inspect: TNS cone + Einstein Probe / GCN alert if this is an EP field."""
    attach_tns_only(config, log=log)
    attach_ep_to_config(config, log=log, image_path=image_path)
    return config.get('tns_alert'), config.get('ep_alert')


def attach_tns_only(config, log=None):
    """After inspect: record whether the target matches a TNS alert."""
    say = log if callable(log) else (lambda *a, **k: None)
    ra, dec = config.get('target_ra'), config.get('target_dec')
    if ra is None or dec is None:
        config.pop('tns_alert', None)
        return None
    name = str(config.get('target') or '').splitlines()[0].strip()
    result = match_target(ra, dec, name=name)
    config['tns_alert'] = result
    if result.get('matched') and result.get('on_target'):
        extra = result.get('obj_type') or ''
        disc = result.get('discovery_iso') or ''
        off = result.get('offset_s') or ''
        say(
            f"TNS alert matches the target: {result['tns_name']}"
            + (f" ({extra})" if extra else '')
            + (f", offset {off}" if off else '')
            + (f", discovered {disc}" if disc else '')
        )
        if result.get('tns_url'):
            say(f"TNS: {result['tns_url']}")
    elif result.get('matched'):
        say(
            f"TNS knows {result['tns_name']}"
            + (f" ({result.get('obj_type')})" if result.get('obj_type') else '')
            + f" but it is {result.get('offset_s') or '?'} from these coordinates "
            f"— the photometry position may not be the alert."
        )
    elif not result.get('query_ok'):
        say(
            f"TNS query failed ({result.get('query_error') or 'unknown'}) "
            f"at RA={float(ra):.5f} Dec={float(dec):.5f} — not a confirmed empty field"
        )
    else:
        say(
            f"TNS: no alert within {result['radius_arcsec']:.0f}\" of "
            f"RA={float(ra):.5f} Dec={float(dec):.5f}"
        )
    return result


EP_TRIGGER = re.compile(
    r'(?:ToO[-_])?EP[-_]?(?:WXT[-_]?)?(017\d{8})',
    re.I,
)
EP_TRIGGER_BARE = re.compile(r'\b(017\d{8})\b')
GCN_HEADERS = {
    'User-Agent': 'stdweb.org.uk photometry (ObsBS RAPAS; https://stdweb.org.uk)',
}
CANDIDATE_RES = [
    re.compile(r'\bPM J\d{4,5}[+\-]\d{4}[A-Za-z]?\b'),
    re.compile(r'\bStKM\s+\d+-\d+\b', re.I),
    re.compile(r'\b2MASS J\d{8}[+\-]\d{7}\b'),
    re.compile(r'\bLSPM J\d{4}[+\-]\d{4}\b', re.I),
    re.compile(r'\bGaia(?:\s*DR3)?\s+\d{5,}\b', re.I),
]
EP_STAR_MATCH_ARCSEC = 5.0


def extract_ep_trigger(*texts):
    blob = ' '.join(str(t or '') for t in texts)
    match = EP_TRIGGER.search(blob)
    if match:
        return match.group(1)
    match = EP_TRIGGER_BARE.search(blob)
    return match.group(1) if match else None


def _gcn_search_items(query):
    import urllib.parse
    import requests

    url = 'https://gcn.nasa.gov/circulars?' + urllib.parse.urlencode(
        {'query': query, 'limit': '20'}
    )
    r = requests.get(url, headers=GCN_HEADERS, timeout=20)
    r.raise_for_status()
    items = []
    ctx = re.search(r'window\.__remixContext = (\{.*?\});</script>', r.text, re.S)
    if ctx:
        import json
        data = json.loads(ctx.group(1))
        loader = data.get('state', {}).get('loaderData', {})
        archive = loader.get('routes/circulars._archive._index') or {}
        items = archive.get('items') or []
    if not items:
        for cid, subject in re.findall(
            r'href="/circulars/(\d+)[^"]*">([^<]+)', r.text
        ):
            items.append({'circularId': cid, 'subject': subject.strip()})
    out = []
    seen = set()
    for item in items:
        cid = str(item.get('circularId') or '')
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append({
            'id': int(cid),
            'subject': (item.get('subject') or '')[:200],
            'url': f'https://gcn.nasa.gov/circulars/{cid}',
        })
    return out


def _gcn_txt(cid):
    import requests
    r = requests.get(
        f'https://gcn.nasa.gov/circulars/{int(cid)}.txt',
        headers=GCN_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.text


def _candidate_names(text):
    names = []
    seen = set()
    for rx in CANDIDATE_RES:
        for name in rx.findall(text or ''):
            key = re.sub(r'\s+', ' ', name).strip()
            if key.lower() in seen:
                continue
            seen.add(key.lower())
            names.append(key)
    return names


def _resolve_star(name):
    try:
        from astropy.coordinates import SkyCoord
        c = SkyCoord.from_name(name)
        return float(c.ra.deg), float(c.dec.deg)
    except Exception:
        return None, None


def match_ep_alert(ra, dec, texts=(), image_path=None):
    """Look up an Einstein Probe WXT trigger in GCN and match FXT candidate stars."""
    extra = list(texts)
    if image_path:
        try:
            from astropy.io import fits
            header = fits.getheader(image_path, -1)
            for kw in ('OBJECT', 'TARGET', 'OBJNAME', 'OBJECTID'):
                extra.append(header.get(kw))
        except Exception:
            pass
    trigger = extract_ep_trigger(*extra)
    empty = {
        'trigger': trigger or '',
        'matched': False,
        'on_target': False,
        'circulars': [],
        'candidates': [],
        'best_name': '',
        'subject': '',
        'url': '',
    }
    if not trigger:
        return empty
    try:
        items = _gcn_search_items(trigger)
    except Exception as exc:
        log.warning('GCN search failed for %s: %s', trigger, exc)
        empty['query_error'] = str(exc)[:120]
        return empty

    circulars = []
    names = []
    for item in items[:8]:
        try:
            body = _gcn_txt(item['id'])
        except Exception:
            body = item.get('subject') or ''
        if trigger not in body and trigger not in (item.get('subject') or ''):
            continue
        circulars.append(item)
        for name in _candidate_names(body):
            if name not in names:
                names.append(name)
    circulars.sort(key=lambda c: (trigger not in (c.get('subject') or ''), -c['id']))

    candidates = []
    if ra is not None and dec is not None:
        for name in names:
            sra, sdec = _resolve_star(name)
            if sra is None:
                candidates.append({
                    'name': name, 'ra': None, 'dec': None,
                    'offset_arcsec': None, 'offset_s': '', 'on_target': False,
                })
                continue
            off = _sep_arcsec(ra, dec, sra, sdec)
            candidates.append({
                'name': name,
                'ra': round(sra, 6),
                'dec': round(sdec, 6),
                'offset_arcsec': round(off, 2),
                'offset_s': format_offset(off),
                'on_target': off <= EP_STAR_MATCH_ARCSEC,
            })
        candidates.sort(key=lambda c: c['offset_arcsec'] if c['offset_arcsec'] is not None else 1e9)

    best = next((c for c in candidates if c.get('on_target')), None)
    empty.update({
        'matched': bool(circulars),
        'on_target': bool(best),
        'circulars': circulars,
        'candidates': candidates,
        'other_candidates': [c for c in candidates if not c.get('on_target')],
        'best_name': (best or (candidates[0] if candidates else {})).get('name') or '',
        'subject': circulars[0]['subject'] if circulars else '',
        'url': circulars[0]['url'] if circulars else f'https://gcn.nasa.gov/circulars?query={trigger}',
    })
    return empty


def attach_ep_to_config(config, log=None, image_path=None):
    say = log if callable(log) else (lambda *a, **k: None)
    ra, dec = config.get('target_ra'), config.get('target_dec')
    result = match_ep_alert(
        ra, dec,
        texts=(config.get('target'), config.get('fits_object')),
        image_path=image_path,
    )
    if not result.get('trigger') and not result.get('matched'):
        config.pop('ep_alert', None)
        return None
    config['ep_alert'] = result
    if result.get('matched') and result.get('on_target'):
        best = next((c for c in result['candidates'] if c.get('on_target')), None)
        say(
            f"EP-WXT trigger {result['trigger']} matches "
            f"{best['name']} ({best['offset_s']})"
        )
        if result.get('url'):
            say(f"GCN: {result['url']}")
        others = [c for c in result['candidates'] if not c.get('on_target') and c.get('offset_s')]
        if others:
            say(
                'Other FXT candidates: '
                + ', '.join(f"{c['name']} ({c['offset_s']})" for c in others[:4])
            )
    elif result.get('matched'):
        say(
            f"EP-WXT trigger {result['trigger']}: {result.get('subject') or 'GCN circular found'}"
        )
        if result.get('url'):
            say(f"GCN: {result['url']}")
        if result.get('candidates'):
            say(
                'FXT candidates: '
                + ', '.join(
                    f"{c['name']}" + (f" ({c['offset_s']})" if c.get('offset_s') else '')
                    for c in result['candidates'][:4]
                )
            )
        else:
            say('No named FXT candidate resolved against the photometry position.')
    elif result.get('trigger'):
        say(f"EP-WXT trigger {result['trigger']}: no GCN circular found")
    return result


def fetch_tns_cone(ra, dec, radius_arcsec=DEFAULT_CONE_ARCSEC):
    status, payload = _tns_csv(
        {
            'ra': f'{float(ra):.6f}',
            'decl': f'{float(dec):.6f}',
            'radius': str(int(radius_arcsec)),
            'coords_unit': 'arcsec',
        },
        near=(ra, dec),
    )
    if status != 'ok':
        return {'_error': payload}
    return payload


def fetch_tns_by_name(name):
    bare = tns_bare_name(name) or str(name or '').strip()
    if not bare:
        return None
    status, payload = _tns_csv({'name': bare, 'name_exact': '0'})
    if status != 'ok':
        return {'_error': payload}
    return payload


def apply_tns_info(meta, info):
    if not info or info.get('_error'):
        return meta
    for field in (
        'tns_name', 'obj_type', 'host_name', 'redshift', 'disc_filter',
        'reporting_group', 'discovery_iso', 'tns_url', 'source',
    ):
        val = info.get(field)
        if val:
            setattr(meta, field, val)
    if info.get('display_name'):
        meta.display_name = info['display_name']
    if info.get('discovery_mjd') is not None:
        meta.discovery_mjd = info['discovery_mjd']
    if info.get('disc_mag') is not None:
        meta.disc_mag = info['disc_mag']
    if info.get('tns_ra') is not None:
        meta.tns_ra = info['tns_ra']
        meta.tns_dec = info['tns_dec']
    meta.save()
    return meta


def ensure_alert_meta(name, first_mjd=None, first_iso='', ra=None, dec=None,
                      radius_arcsec=DEFAULT_CONE_ARCSEC):
    """Fetch / cache TNS metadata by sky position (preferred) or object name."""
    bare = tns_bare_name(name)
    if ra is not None and dec is not None:
        key = bare or f'{float(ra):.4f}_{float(dec):+.4f}_r{int(radius_arcsec)}'
    else:
        key = (bare or str(name or '').strip().lower()[:80]) or ''
    if not key:
        return None

    meta, created = models.AlertMeta.objects.get_or_create(
        name_key=key[:80],
        defaults={'display_name': str(name or '')[:80]},
    )
    if name and not meta.display_name:
        meta.display_name = str(name)[:80]

    stale = created or (now() - meta.fetched > timedelta(hours=CACHE_HOURS))
    # Existing rows from the old scraper have discovery times but no tns_name yet.
    # Retry rate-limit / scrape failures immediately; keep a real empty cone for CACHE_HOURS.
    need = stale or (not meta.tns_name and meta.source not in ('none', 'first_epoch'))
    if meta.source == 'tns_error' and not meta.tns_name:
        need = True
    if need:
        info = None
        err = None
        if ra is not None and dec is not None:
            info = fetch_tns_cone(ra, dec, radius_arcsec=radius_arcsec)
            if isinstance(info, dict) and info.get('_error'):
                err = info['_error']
                info = None
        if info is None and (bare or name):
            by_name = fetch_tns_by_name(name or bare)
            if isinstance(by_name, dict) and by_name.get('_error'):
                err = err or by_name['_error']
            else:
                info = by_name
        if info:
            apply_tns_info(meta, info)
            return meta
        if err:
            meta.source = 'tns_error'
            meta.reporting_group = str(err)[:120]
            meta.save()
            return meta
        if not meta.source or meta.source == 'tns_error':
            meta.source = 'none'
            meta.reporting_group = ''
        meta.save()

    if meta.discovery_mjd is None and first_mjd is not None:
        meta.discovery_mjd = float(first_mjd)
        meta.discovery_iso = (first_iso or '')[:50]
        if not meta.source:
            meta.source = 'first_epoch'
        meta.save()
    return meta


def tns_payload(meta):
    if not meta or not meta.tns_name:
        return None
    return {
        'tns_name': meta.tns_name or meta.display_name,
        'obj_type': meta.obj_type,
        'host_name': meta.host_name,
        'redshift': meta.redshift,
        'disc_mag': meta.disc_mag,
        'disc_filter': meta.disc_filter,
        'reporting_group': meta.reporting_group,
        'discovery_iso': (meta.discovery_iso or '')[:19],
        'tns_url': meta.tns_url,
        'tns_ra': meta.tns_ra,
        'tns_dec': meta.tns_dec,
        'source': meta.source,
    }


def age_days(obs_mjd, discovery_mjd):
    if obs_mjd is None or discovery_mjd is None:
        return None
    return float(obs_mjd) - float(discovery_mjd)
