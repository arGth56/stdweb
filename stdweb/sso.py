"""Ephemeris positions of solar-system objects (comets, asteroids, NEOCP objects).

Designated objects are looked up in JPL Horizons, objects on the MPC NEO
Confirmation Page in JPL Scout. Positions are astrometric (ICRF) RA/Dec as seen
from the observatory at the requested epoch.
"""

import json
import re

import numpy as np
import requests
from astropy.coordinates import Angle
from astropy.time import Time
import astropy.units as u

HORIZONS_URL = 'https://ssd.jpl.nasa.gov/api/horizons.api'
SCOUT_URL = 'https://ssd-api.jpl.nasa.gov/scout.api'

_comet_re = re.compile(r'^([CPDXIA])/(\d{4}\s+[A-Z]{1,2}\d{0,3}(?:-[A-Z])?)(?:\s*\(.*\))?$')
_periodic_re = re.compile(r'^(\d{1,4}[PDI])(?:-[A-Z]+)?(?:/.*)?$')
_provisional_re = re.compile(r'^(\d{4})\s*([A-Z]{2}\d{0,4})$')
_numbered_re = re.compile(r'^\((\d+)\)(?:\s+.*)?$|^(\d+)\s+[A-Z][a-z]+.*$')
# NEOCP temporary designations are up to 7 alphanumeric characters, assigned by the observer
_neocp_re = re.compile(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{3,7}$')


def horizons_command(name):
    """Horizons COMMAND for a comet or asteroid designation, or None if it does not look like one."""
    name = ' '.join(str(name).split())

    m = _comet_re.match(name)
    if m:
        return f"DES={m[1]}/{m[2]};CAP;NOFRAG"

    m = _periodic_re.match(name)
    if m:
        return f"DES={m[1]};CAP;NOFRAG"

    m = _provisional_re.match(name)
    if m:
        return f"DES={m[1]} {m[2]};"

    m = _numbered_re.match(name)
    if m:
        return f"{m[1] or m[2]};"

    return None


def looks_like_neocp(name):
    return bool(_neocp_re.match(str(name).strip()))


def observer_site(header):
    """Observing site from FITS keywords.

    Returns a dict with either 'code' (MPC observatory code) or 'lon'/'lat'/'alt'
    (east longitude and latitude in degrees, altitude in metres), or None.
    """
    for kw in ('IAU_CODE', 'MPC-CODE', 'MPCCODE', 'OBS-CODE', 'OBSCODE'):
        value = str(header.get(kw) or '').strip().upper()
        if re.match(r'^[A-Z0-9]\d{2}$', value):
            return {'code': value, 'source': kw}

    try:
        if header.get('OBSGEO-L') is not None and header.get('OBSGEO-B') is not None:
            return {
                'lon': float(header['OBSGEO-L']),
                'lat': float(header['OBSGEO-B']),
                'alt': float(header.get('OBSGEO-H') or 0),
                'source': 'OBSGEO-L/B/H',
            }
    except (TypeError, ValueError):
        pass

    lat, lon = header.get('SITELAT'), header.get('SITELONG')
    if lat is not None and lon is not None:
        try:
            # Most acquisition software writes east-positive longitudes here
            return {
                'lon': Angle(str(lon).strip(), unit=u.deg).wrap_at(180*u.deg).deg,
                'lat': Angle(str(lat).strip(), unit=u.deg).deg,
                'alt': float(header.get('SITEELEV') or 0),
                'source': 'SITELAT/SITELONG',
            }
        except Exception:
            pass

    return None


def ephemeris_epoch(config, header):
    """Exposure mid-time: DATE-AVG is already the midpoint, otherwise start + EXPTIME/2."""
    if not config.get('time'):
        return None

    time = Time(config['time'])
    if config.get('time_date_avg') == config['time']:
        return time

    exptime = header.get('EXPTIME', header.get('EXPOSURE'))
    try:
        exptime = float(exptime)
    except (TypeError, ValueError):
        exptime = None

    if exptime and np.isfinite(exptime) and exptime > 0:
        time = time + 0.5*exptime*u.s

    return time


def _horizons(command, time, site, timeout):
    params = {
        'format': 'json',
        'COMMAND': f"'{command}'",
        'OBJ_DATA': 'NO',
        'MAKE_EPHEM': 'YES',
        'EPHEM_TYPE': 'OBSERVER',
        'TLIST': f"'{time.utc.jd:.8f}'",
        'TLIST_TYPE': 'JD',
        'TIME_TYPE': 'UT',
        'QUANTITIES': "'1,3,36'",
        'ANG_FORMAT': 'DEG',
        'CSV_FORMAT': 'YES',
        'EXTRA_PREC': 'YES',
    }
    if site and site.get('code'):
        params['CENTER'] = f"'{site['code']}@399'"
    elif site:
        params['CENTER'] = "'coord@399'"
        params['COORD_TYPE'] = 'GEODETIC'
        params['SITE_COORD'] = f"'{site['lon']:.6f},{site['lat']:.6f},{site['alt']/1000:.4f}'"
    else:
        params['CENTER'] = "'500@399'"

    r = requests.get(HORIZONS_URL, params=params, timeout=timeout)
    r.raise_for_status()
    result = r.json().get('result', '')

    if '$$SOE' not in result:
        return None

    line = result.split('$$SOE')[1].split('$$EOE')[0].strip().splitlines()[0]
    fields = [_.strip() for _ in line.split(',')]
    # Date, solar presence, lunar presence, RA, Dec, dRA*cosD, dDec, RA 3-sigma, Dec 3-sigma
    ra, dec = float(fields[3]), float(fields[4])
    res = {'ra': ra, 'dec': dec, 'source': 'JPL Horizons'}

    try:
        res['rate_ra'], res['rate_dec'] = float(fields[5]), float(fields[6])  # arcsec/hour
    except (IndexError, ValueError):
        pass

    try:
        res['unc'] = float(np.hypot(float(fields[7]), float(fields[8])))  # 3-sigma, arcsec
    except (IndexError, ValueError):
        pass

    return res


def _scout(name, time, site, timeout):
    # Cheap existence check before the slower ephemeris request
    r = requests.get(SCOUT_URL, params={'tdes': name}, timeout=timeout)
    r.raise_for_status()
    if 'error' in r.json():
        return None

    params = {
        'tdes': name,
        'eph-start': time.utc.strftime('%Y-%m-%dT%H:%M:%S'),
        'obs-code': site['code'] if site and site.get('code') else '500',
    }
    r = requests.get(SCOUT_URL, params=params, timeout=timeout)
    r.raise_for_status()
    eph = r.json().get('eph')
    if not eph:
        return None

    median = eph[0]['median']
    ra, dec = float(median['ra']), float(median['dec'])
    res = {'ra': ra, 'dec': dec, 'source': 'JPL Scout (MPC NEOCP)'}

    try:
        res['rate'] = float(median['rate'])*60  # arcsec/min -> arcsec/hour
    except (KeyError, TypeError, ValueError):
        pass

    # Spread of the sampled orbits around the median position
    try:
        pos = np.array([[float(_[0]), float(_[1])] for _ in eph[0]['data']])
        sep = np.hypot((pos[:, 0] - ra)*np.cos(np.deg2rad(dec)), pos[:, 1] - dec)*3600
        res['unc'] = float(np.percentile(sep, 99.7))
    except Exception:
        pass

    return res


def resolve_sso(name, time, site=None, timeout=60, verbose=False):
    """Ephemeris position of a comet, asteroid or NEOCP object, or None if not found.

    Network errors are logged and treated as "not found".
    """
    log = (verbose if callable(verbose) else print) if verbose else lambda *args, **kwargs: None

    name = str(name).strip()
    if time is None or not name:
        return None

    command = horizons_command(name)
    res = None

    try:
        if command:
            res = _horizons(command, time, site, timeout)
        elif looks_like_neocp(name):
            res = _scout(name, time, site, timeout)
    except Exception as e:
        log(f"  Solar-system lookup for {name} failed: {e}")
        return None

    if res is None:
        return None

    res['name'] = name
    res['epoch'] = time.utc.isot
    if site and site.get('code'):
        res['site'] = f"observatory code {site['code']}"
    elif site and command:
        res['site'] = f"lon {site['lon']:.4f} lat {site['lat']:.4f} ({site['source']})"
    elif site:
        res['site'] = 'geocentre (Scout only accepts observatory codes)'
    else:
        res['site'] = 'geocentre (no observatory code or coordinates in the header)'

    return res
