"""Lazy disk caches. Importing this module never starts network work."""
import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import requests

DAY = 86400
CACHE_ROOT = Path(__file__).resolve().parent / '.cache'
FEED_LOCK = threading.Lock()
IMAGE_LOCK = threading.Lock()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, path)


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def feed_definitions():
    recent = '>' + str(datetime.now().year - 1)
    result = {}
    # Local time; each feed owns a distinct daily hour.
    for typ, suffix, start in [('1', 'movies', 0), ('3', 'series', 1)]:
        for kind, hour, extra in [
            ('new', start, {'of': 'datum'}),
            ('trending', start + 4, {'of': 'minfo.play', 'y': recent}),
            ('popular', start + 8, {'of': 'minfo.play'}),
            ('dub', start + 14, {'of': 'datum', 'dub': '1'}),
        ]:
            result[kind + '-' + suffix] = (hour, {'typ': typ, 'od': 'desc', **extra})
    result['rating-movies'] = (12, {'typ': '1', 'of': 'rating', 'od': 'desc'})
    return result


def feed_id_for(params):
    actual = dict(params)
    actual = {k: v for k, v in actual.items() if k not in ('limit', 'page')}
    return next((key for key, (_, filters) in feed_definitions().items() if filters == actual), None)


def get_feed(feed_id, fetch, full=False):
    definitions = feed_definitions()
    if feed_id not in definitions:
        raise ValueError('Unknown feed')
    with FEED_LOCK:
        now = time.time()
        directory = CACHE_ROOT / 'feeds'
        schedule_path = directory / 'schedule.json'
        schedule = read_json(schedule_path)
        entries = {key: read_json(directory / (key + '.json')) for key in definitions}
        target = None
        if full:
            entry = entries[feed_id]
            cooldown = 900 if entry.get('failed') else 60
            if now - entry.get('updated', 0) >= 60 and now - entry.get('attempted', 0) >= cooldown:
                target = feed_id
        elif now - schedule.get('last_attempt', 0) >= 3600:
            candidates = []
            for key, (hour, _) in definitions.items():
                entry = entries[key]
                slot = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0).timestamp()
                if now >= slot and now - entry.get('updated', 0) >= DAY and now - entry.get('attempted', 0) >= 900:
                    candidates.append(key)
            # Refresh even unvisited feeds; prioritize missing/oldest cache so
            # repeatedly opening Home cannot starve the other categories.
            if candidates:
                target = min(candidates, key=lambda key: (entries[key].get('updated', 0), entries[key].get('attempted', 0), definitions[key][0]))
        if target:
            entry = entries[target]
            path = directory / (target + '.json')
            entry['attempted'] = now
            atomic_json(path, entry)
            atomic_json(schedule_path, {'last_attempt': now})
            try:
                data = fetch('/Filter', {**definitions[target][1], 'limit': '40', 'page': '1'})
                if not isinstance(data, dict) or not isinstance(data.get('menu'), list):
                    raise ValueError('Invalid feed response')
                entries[target] = {'updated': now, 'attempted': now, 'data': data, 'failed': False}
                atomic_json(path, entries[target])
            except Exception:
                entry['failed'] = True
                atomic_json(path, entry)
        entry = entries[feed_id]
        updated = entry.get('updated', 0)
        status = 'cached' if entry.get('data') else 'waiting'
        if target == feed_id:
            status = 'refreshed' if not entry.get('failed') else 'stale' if entry.get('data') else 'unavailable'
        elif updated and now - updated >= DAY:
            status = 'stale'
        data = dict(entry.get('data') or {'menu': []})
        if not full:
            data['menu'] = [x for x in data.get('menu', []) if str(x.get('url', '')).startswith(('/Play/', '/FGet/'))][:4]
        data['_cache'] = {'status': status, 'updated': entry.get('updated'), 'refresh_hour': definitions[feed_id][0]}
        return data


IMAGE_HOSTS = {'img.stream-cinema.online', 'stream-cinema.online', 'image.tmdb.org', 'assets.fanart.tv'}
IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif'}


def image_url_allowed(url):
    parsed = urlsplit(url)
    return parsed.scheme == 'https' and parsed.hostname in IMAGE_HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 443)


def get_image(url):
    if not image_url_allowed(url):
        raise ValueError('Unsupported image host')
    key = hashlib.sha256(url.encode()).hexdigest()
    directory = CACHE_ROOT / 'images'
    path, meta_path = directory / (key + '.bin'), directory / (key + '.json')
    with IMAGE_LOCK:
        meta = read_json(meta_path)
        if path.is_file() and meta.get('type') in IMAGE_TYPES:
            return path.read_bytes(), meta['type']
        if time.time() - meta.get('attempted', 0) < 900:
            raise ValueError('Image temporarily unavailable')
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(meta_path, {'attempted': time.time()})
        current = url
        for _ in range(4):
            if not image_url_allowed(current):
                raise ValueError('Unsupported image redirect')
            with requests.get(current, timeout=15, stream=True, allow_redirects=False) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    current = urljoin(current, response.headers.get('Location', ''))
                    continue
                response.raise_for_status()
                content_type = response.headers.get('Content-Type', '').split(';')[0]
                if content_type not in IMAGE_TYPES:
                    raise ValueError('Unsupported image type')
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 10 * 1024 * 1024:
                        raise ValueError('Image too large')
                # Unique temp file also handles two requests for the same poster.
                import tempfile
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temp:
                    temp.write(body)
                    temp_name = temp.name
                os.replace(temp_name, path)
                atomic_json(meta_path, {'type': content_type})
                return bytes(body), content_type
        raise ValueError('Too many image redirects')
