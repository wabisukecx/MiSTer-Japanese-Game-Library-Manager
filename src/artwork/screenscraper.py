"""ScreenScraper API v2 adapter. Credentials never enter cache keys or metadata.

Name search avoids ROM transfer. A provider game ID must be selected explicitly
before mediaJeu.php downloads box-2D(jp). No HTML scraping or borrowed app keys.
"""
from dataclasses import dataclass, field
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .cache import ImageCache, atomic_write, MAX_BYTES

BASE = 'https://api.screenscraper.fr/api2/'
REGIONS = ('jp', 'us', 'eu', 'wor')
SEARCH_CACHE_SECONDS = 30 * 86400  # older successful searches are fetched again


class APIError(RuntimeError):
    pass


class QuotaExceeded(APIError):
    pass


@dataclass(repr=False)
class Credentials:
    developer_id: str
    developer_password: str = field(repr=False)
    username: str
    password: str = field(repr=False)

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (
                self.developer_id, self.developer_password, self.username, self.password)):
            raise ValueError('ScreenScraper developer credentials and member credentials are required')

    @classmethod
    def from_environment(cls):
        return cls(*(os.environ.get('SCREENSCRAPER_' + key, '')
                     for key in ('DEVID', 'DEVPASSWORD', 'SSID', 'SSPASSWORD')))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Auth is in the API query. Never forward it to a redirect target.
        return None


def positive_id(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError('A positive numeric provider ID is required') from None
    if result <= 0:
        raise ValueError('A positive numeric provider ID is required')
    return result


def parse_games(payload, system_id=3):
    """Whitelisted search fields only; never persist API response URLs or auth."""
    response = payload.get('response', {})
    games = response.get('jeux')
    if not isinstance(games, list):
        raise APIError('ScreenScraper returned an unexpected game-search structure')
    result = []
    for game in games:
        if not isinstance(game, dict) or not isinstance(game.get('systeme'), dict):
            continue
        if str(game['systeme'].get('id')) != str(system_id):
            continue
        names = [dict(region=str(n.get('region', '')).lower(), title=n['text'])
                 for n in game.get('noms', []) if isinstance(n, dict) and isinstance(n.get('text'), str)]
        result.append(dict(source='screenscraper', provider_game_id=positive_id(game['id']),
                           system_id=system_id, names=names,
                           japanese_title=next((n['title'] for n in names if n['region'] == 'jp'), None),
                           match_status='candidate', reason='provider name search; user review required'))
    return result


class ScreenScraper:
    def __init__(self, credentials, cache_root, *, timeout=20, transport=None,
                 sleep=time.sleep, clock=time.time, cancelled=lambda: False, state_root=None):
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError('Timeout must be finite and positive')
        self.credentials = credentials
        self.root = Path(cache_root) / 'screenscraper'
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout, self.transport = timeout, transport
        self.sleep, self.clock, self.cancelled = sleep, clock, cancelled
        # Shared across cache locations/CLI processes, without storing account names.
        account = hashlib.sha256(credentials.username.encode()).hexdigest()
        self.state_root = (Path(state_root) if state_root else
                           Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.cache') / 'mister-jp-library' / 'api-rate')
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.state = self.state_root / (account + '.json')
        self.lock = self.state.with_suffix('.lock')
        self.interval, self.speed = 1.0, 64 * 1024
        self.remaining = None
        self.ready = False

    def check_cancelled(self):
        if self.cancelled():
            raise InterruptedError('ScreenScraper operation cancelled')

    def pause(self, seconds):
        while seconds > 0:
            self.check_cancelled()
            duration = min(seconds, 0.25)
            self.sleep(duration)
            seconds -= duration

    @contextmanager
    def reserve(self):
        self.check_cancelled()
        if self.remaining is not None and self.remaining <= 0:
            raise QuotaExceeded('ScreenScraper daily quota exhausted; try after the service resets it')
        try:
            handle = self.lock.open('x')
        except FileExistsError:
            raise APIError('Another ScreenScraper request is active (or a stale API lock exists)') from None
        try:
            handle.close()
            next_allowed = 0
            if self.state.exists():
                try:
                    next_allowed = float(json.loads(self.state.read_text(encoding='utf-8'))['next_allowed'])
                except (ValueError, KeyError, TypeError):
                    raise APIError('Invalid local API rate state; no request was sent') from None
                if not math.isfinite(next_allowed):
                    raise APIError('Invalid local API rate state; no request was sent')
            delay = max(0, next_allowed - self.clock())
            if delay > 60:
                raise APIError('ScreenScraper is cooling down; retry later')
            self.pause(delay)
            self.check_cancelled()
            atomic_write(self.state, json.dumps(dict(next_allowed=self.clock() + self.interval)).encode())
            if self.remaining is not None:
                self.remaining -= 1
            yield
        finally:
            self.lock.unlink(missing_ok=True)

    def _network(self, url, limit):
        request = urllib.request.Request(url, headers={'User-Agent': 'mister-jp-library/0.1'})
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=self.timeout) as response:
            chunks, total = [], 0
            started = self.clock()
            while True:
                self.check_cancelled()
                chunk = response.read(min(16384, limit + 1 - total))
                if not chunk:
                    return b''.join(chunks)
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    raise APIError('ScreenScraper response exceeds the configured size limit')
                self.pause(max(0, total / self.speed - (self.clock() - started)))

    def request(self, endpoint, params, limit=2 * 1024 * 1024):
        auth = dict(devid=self.credentials.developer_id, devpassword=self.credentials.developer_password,
                    ssid=self.credentials.username, sspassword=self.credentials.password,
                    softname='mister-jp-library')
        url = BASE + endpoint + '?' + urlencode(dict(auth, **params))
        for attempt in range(3):
            try:
                with self.reserve():
                    data = self.transport(url, limit=limit) if self.transport else self._network(url, limit)
                    if len(data) > limit:
                        raise APIError('ScreenScraper response exceeds the configured size limit')
                    return data
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                if code in (429, 430, 431):
                    # Stop retries and prevent an immediate new CLI request from hammering the API.
                    self.reserve_cooldown()
                if code not in (500, 502, 503, 504) or attempt == 2:
                    raise APIError('ScreenScraper HTTP %s; check access, credentials or quota' % code) from None
            except InterruptedError:
                raise
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise APIError('ScreenScraper network failure or timeout') from None
            self.pause(2 ** attempt)
        raise APIError('ScreenScraper request failed')

    def reserve_cooldown(self):
        # Best effort only: do not overwrite another process's rate state.
        try:
            with self.lock.open('x'):
                atomic_write(self.state, json.dumps(dict(next_allowed=self.clock() + 3600)).encode())
            self.lock.unlink(missing_ok=True)
        except FileExistsError:
            pass

    def json_request(self, endpoint, params):
        raw = self.request(endpoint, dict(params, output='json'))
        try:
            data = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('response'), dict):
                raise ValueError()
            return data
        except (ValueError, UnicodeError):
            raise APIError('ScreenScraper returned invalid JSON or an API error; response text was redacted') from None

    def ensure_ready(self):
        if self.ready:
            return
        data = self.json_request('ssuserInfos.php', {})
        user = data['response'].get('ssuser', {})
        try:
            per_minute = int(user.get('maxrequestspermin', user.get('maxrequestsperdmin')))
            per_day = int(user['maxrequestsperday'])
            used = int(user['requeststoday'])
            max_threads = int(user['maxthreads'])
            speed = int(user['maxdownloadspeed'])
            ko_limit, ko_used = int(user['maxrequestskoperday']), int(user['requestskotoday'])
            if min(per_minute, per_day, max_threads, speed) <= 0 or min(used, ko_limit, ko_used) < 0:
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise APIError('ScreenScraper quota information missing or invalid; refusing further requests') from None
        self.interval = max(1.0, 60 / per_minute)
        self.speed = speed * 1024
        self.remaining = per_day - used
        if self.remaining <= 0 or (ko_limit > 0 and ko_used >= ko_limit):
            raise QuotaExceeded('ScreenScraper daily or unmatched-game quota exhausted')
        # Apply newly discovered rate even immediately after the quota query.
        atomic_write(self.state, json.dumps(dict(next_allowed=self.clock() + self.interval)).encode())
        self.ready = True

    def search(self, title, system_id=3, refresh=False):
        self.check_cancelled()
        if not isinstance(title, str) or not title.strip() or any(ord(c) < 32 for c in title):
            raise ValueError('A nonempty search title without controls is required')
        system_id = positive_id(system_id)
        key = hashlib.sha256(json.dumps([system_id, title], ensure_ascii=False).encode()).hexdigest()
        path = self.root / ('search-' + key + '.json')
        if path.exists() and not refresh and time.time() - path.stat().st_mtime < SEARCH_CACHE_SECONDS:
            try:
                cached = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(cached['candidates'], list) and cached['system_id'] == system_id and cached['title'] == title:
                    return cached['candidates']
            except (ValueError, KeyError, TypeError):
                pass
        self.ensure_ready()
        payload = self.json_request('jeuRecherche.php', dict(systemeid=system_id, recherche=title))
        try:
            result = parse_games(payload, system_id)
        except (ValueError, TypeError, KeyError):
            raise APIError('ScreenScraper returned an unexpected game-search structure') from None
        # Do not cache misses indefinitely; a future request can find newly added games.
        if result:
            atomic_write(path, json.dumps(dict(system_id=system_id, title=title, candidates=result), ensure_ascii=False).encode('utf-8'))
        return result

    def download(self, provider_game_id, region='jp', system_id=3):
        self.check_cancelled()
        provider_game_id, system_id = positive_id(provider_game_id), positive_id(system_id)
        if region not in REGIONS:
            raise ValueError('Supported regions: jp, us, eu, wor')
        params = dict(systemeid=system_id, jeuid=provider_game_id,
                      media='box-2D(%s)' % region, outputformat='png')
        public_url = BASE + 'mediaJeu.php?' + urlencode(params)

        def validator(url):
            if url != public_url:
                raise ValueError('Unexpected ScreenScraper cache resource')

        def fetch(url):
            self.ensure_ready()
            data = self.request('mediaJeu.php', params, limit=MAX_BYTES)
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise APIError('ScreenScraper image unavailable or invalid; no regional fallback was applied')
            return data

        path = ImageCache(self.root / 'images', fetch=fetch, validator=validator).get(public_url).resolve()
        return dict(source='screenscraper', provider_game_id=provider_game_id, system_id=system_id,
                    region=region.upper(), media='box-2D', url=public_url, local_path=str(path),
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(), region_basis='API region request; visual review required',
                    selected=False, transfer_allowed=False)
