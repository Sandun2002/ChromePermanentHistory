"""Profile 13 <-> owner-scoped Supabase archive. Standard library; never writes Chrome data."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError

BASE = Path(__file__).resolve().parent
ARCHIVE = BASE / 'PermanentChromeHistory.sqlite'
STATE = BASE / 'SharedArchive.sqlite'
CALLBACK = 'http://127.0.0.1:8765/auth/callback'
SOURCE = 'chrome_desktop_profile13'
ZERO = '00000000-0000-0000-0000-000000000000'
EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
GOOGLE = {'google.com','www.google.com','google.lk','www.google.lk','google.co.uk','www.google.co.uk','google.co.in','www.google.co.in'}
RULES = [(GOOGLE, {'/search'}, 'q'),
    ({'bing.com','www.bing.com','cn.bing.com'}, {'/search'}, 'q'),
    ({'duckduckgo.com','www.duckduckgo.com'}, {'','/','/html','/html/','/lite','/lite/'}, 'q'),
    ({'html.duckduckgo.com'}, {'/html','/html/'}, 'q'),
    ({'lite.duckduckgo.com'}, {'/lite','/lite/'}, 'q'),
    ({'search.brave.com'}, {'/search'}, 'q'),
    ({'ecosia.org','www.ecosia.org'}, {'/search'}, 'q'),
    ({'search.yahoo.com'}, {'/search'}, 'p')]

class SyncError(RuntimeError): pass

def utc(value: str | None = None) -> str:
    at = datetime.fromisoformat(value.replace('Z', '+00:00')) if value else datetime.now(timezone.utc)
    if at.tzinfo is None: raise ValueError('Timestamp needs timezone')
    return at.astimezone(timezone.utc).isoformat()

def parse_search(raw: str):
    try:
        if len(raw) > 16384 or re.search(r'%(?![0-9a-fA-F]{2})', raw) or any(ord(c) <= 32 or ord(c) == 127 for c in raw): return None
        parts = urlsplit(raw)
        if parts.scheme != 'https' or parts.username is not None or parts.password is not None or parts.port is not None: return None
        for hosts, paths, parameter in RULES:
            if parts.hostname in hosts and parts.path in paths:
                queries = [value for key, value in parse_qsl(parts.query, keep_blank_values=True, errors='strict') if key == parameter]
                if len(queries) != 1: return None
                query = queries[0].strip()
                if not query or len(query.encode('utf-16-le')) // 2 > 2000 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in query): return None
                return query, parts.hostname
    except (ValueError, UnicodeError): pass
    return None

def search_url(domain: str, query: str) -> str:
    path = '/html/' if domain == 'html.duckduckgo.com' else '/lite/' if domain == 'lite.duckduckgo.com' else '/' if domain.endswith('duckduckgo.com') else '/search'
    return 'https://' + domain + path + '?' + urlencode({'p' if domain == 'search.yahoo.com' else 'q': query})

class WindowsLock:
    def __init__(self, name='Local\\DataSync-Profile13-API', timeout=5000):
        self.name, self.timeout, self.handle = name, timeout, None
    def __enter__(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
        handle = kernel.CreateMutexW(None, False, self.name)
        if not handle: raise SyncError('Cannot acquire sync lock')
        if kernel.WaitForSingleObject(handle, self.timeout) not in (0, 0x80):
            kernel.CloseHandle(handle)
            raise SyncError('Sync is busy; try again shortly')
        self.kernel, self.handle = kernel, handle
        return self
    def __exit__(self, *args):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None

class Vault:
    """Windows user-bound DPAPI, atomic writes, no plaintext token fallback."""
    def __init__(self, path: Path): self.path = path
    @staticmethod
    def crypt(data: bytes, decrypt=False) -> bytes:
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        buffer = ctypes.create_string_buffer(data)
        incoming = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = Blob()
        api = ctypes.WinDLL('crypt32', use_last_error=True)
        if decrypt:
            okay = api.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
        else:
            okay = api.CryptProtectData(ctypes.byref(incoming), 'DataSync Profile 13', None, None, None, 1, ctypes.byref(outgoing))
        if not okay: raise SyncError('Windows credential protection is unavailable; credentials were not reset')
        try: return ctypes.string_at(outgoing.data, outgoing.size)
        finally:
            kernel = ctypes.WinDLL('kernel32')
            kernel.LocalFree.argtypes = (wintypes.HLOCAL,)
            kernel.LocalFree(ctypes.cast(outgoing.data, wintypes.HLOCAL))
    def load(self) -> dict:
        if not self.path.exists(): return {}
        return json.loads(self.crypt(self.path.read_bytes(), True))
    def save(self, value: dict):
        encrypted = self.crypt(json.dumps(value).encode())
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.path)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def transport(url: str, key: str, body: dict, token: str | None = None):
    headers = {'apikey': key, 'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    request = Request(url, json.dumps(body, ensure_ascii=False).encode(), headers, method='POST')
    try:
        with build_opener(NoRedirect).open(request, timeout=25) as response:
            data = response.read(8_000_001)
            if len(data) > 8_000_000: raise SyncError('Cloud response is too large')
            return json.loads(data)
    except HTTPError as error:
        raise SyncError(f'Cloud HTTP {error.code}; check shared schema, sign-in and configuration') from None
    except (OSError, ValueError):
        raise SyncError('Cloud connection unavailable; records retained') from None

class Bridge:
    def __init__(self, archive=ARCHIVE, state=STATE, vault=None, request=transport, guard=None):
        self.archive, self.state = Path(archive), Path(state)
        self.vault = vault or Vault(self.state.with_name('.cloud-sync.dpapi'))
        self.request = request
        self.guard = guard or (lambda: WindowsLock())
        self.stop = threading.Event()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS outbox(kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,
                    synced INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS remote(kind TEXT NOT NULL,id TEXT NOT NULL,payload TEXT NOT NULL,
                    url TEXT NOT NULL,title TEXT NOT NULL,domain TEXT NOT NULL,visited_at_utc TEXT NOT NULL,
                    chrome_visit_time INTEGER NOT NULL,device_id TEXT NOT NULL,source TEXT NOT NULL,
                    cached_at TEXT NOT NULL,PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS remote_time ON remote(chrome_visit_time);
            ''')
            db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('device_id', str(uuid.uuid4())))
    @contextmanager
    def db(self):
        db = sqlite3.connect(self.state, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db: yield db
        finally: db.close()
    @staticmethod
    def meta(db, key, default=None):
        row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    @staticmethod
    def put(db, key, value):
        db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))
    def device(self, db): return db.execute("SELECT value FROM meta WHERE key='device_id'").fetchone()[0]
    def configure(self, url, key, include_visits=True):
        parts = urlsplit(url.strip().rstrip('/'))
        if parts.scheme != 'https' or not re.fullmatch(r'[a-z0-9-]+\.supabase\.co', parts.netloc) or parts.path or parts.query or parts.fragment:
            raise SyncError('Use the same https://PROJECT.supabase.co URL as Android')
        key = key.strip()
        valid = key.startswith('sb_publishable_')
        if not valid:
            try: valid = json.loads(base64.urlsafe_b64decode(key.split('.')[1] + '==='))['role'] == 'anon'
            except (ValueError, IndexError, KeyError): pass
        if not valid: raise SyncError('Use a public publishable/anon key; never a service-role key')
        with self.guard():
            value = self.vault.load()
            with self.db() as db:
                bound_project = self.meta(db, 'bound_project')
            if bound_project and bound_project != parts.geturl():
                raise SyncError('This archive is bound to its original project')
            if value.get('bound_user') and value.get('url') != parts.geturl():
                raise SyncError('This installation is bound to its original project')
            if value.get('url') != parts.geturl(): value.pop('pkce', None)
            value.update(url=parts.geturl(), key=key, include_visits=bool(include_visits))
            self.vault.save(value)
        self.wake()
    def login(self):
        with self.guard():
            value = self.vault.load()
            if not value.get('url'): raise SyncError('Save cloud configuration first')
            with self.db() as db:
                expected_email = self.meta(db, 'expected_email')
            verifier, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            value['pkce'] = {'verifier': verifier, 'nonce': nonce, 'created': time.time()}
            self.vault.save(value)
            params = dict(provider='google', redirect_to=CALLBACK,
                code_challenge=challenge, code_challenge_method='s256')
            if expected_email: params.update(login_hint=expected_email, prompt='select_account')
            return value['url'] + '/auth/v1/authorize?' + urlencode(params), nonce
    def save_session(self, value, response):
        user = str(uuid.UUID(response['user']['id']))
        if value.get('bound_user') not in (None, user): raise SyncError('Sign into the original archive account')
        with self.db() as db:
            expected_email = self.meta(db, 'expected_email')
            if expected_email and response['user'].get('email', '').casefold() != expected_email.casefold():
                raise SyncError('Sign into the required archive account: ' + expected_email)
            if self.meta(db, 'bound_user') not in (None, user): raise SyncError('Sign into the original archive account')
            if self.meta(db, 'bound_project') not in (None, value['url']): raise SyncError('Use the original archive project')
            self.put(db, 'bound_user', user)
            self.put(db, 'bound_project', value['url'])
        value['bound_user'] = user
        value['email'] = response['user'].get('email', '')
        value['session'] = {'access_token': response['access_token'], 'refresh_token': response['refresh_token'],
            'expires_at': time.time() + int(response['expires_in'])}
        self.vault.save(value)
    def finish_login(self, code, nonce):
        with self.guard():
            value = self.vault.load()
            pending = value.get('pkce', {})
            if not nonce or not secrets.compare_digest(nonce, pending.get('nonce', '')) or not 0 <= time.time() - pending.get('created', 0) <= 600:
                raise SyncError('Sign-in expired or browser changed; start again from this dashboard')
            response = self.request(value['url'] + '/auth/v1/token?grant_type=pkce', value['key'],
                {'auth_code': code, 'code_verifier': pending['verifier']})
            value.pop('pkce', None)
            self.save_session(value, response)
        self.wake()
    def signout(self):
        with self.guard():
            value = self.vault.load()
            value.pop('session', None)
            value.pop('pkce', None)
            self.vault.save(value)
    def api(self, value, rpc, body):
        session = value.get('session')
        if not session: raise SyncError('Sign into the same Google account as Android')
        if session['expires_at'] < time.time() + 60:
            response = self.request(value['url'] + '/auth/v1/token?grant_type=refresh_token', value['key'],
                {'refresh_token': session['refresh_token']})
            self.save_session(value, response)
        try:
            return self.request(value['url'] + '/rest/v1/rpc/' + rpc, value['key'], body, value['session']['access_token'])
        except SyncError as error:
            if 'HTTP 401' in str(error):
                value['session']['expires_at'] = 0
                self.vault.save(value)
            raise
    def stage(self, include_visits):
        with self.db() as db, closing(sqlite3.connect(self.archive.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            source.row_factory = sqlite3.Row
            key = 'stage_full' if include_visits else 'stage_search'
            after = self.meta(db, key, 0)
            device = self.device(db)
            rows = source.execute('SELECT archive_id,url,title,visited_at_utc FROM permanent_visits WHERE archive_id>? ORDER BY archive_id LIMIT 1000', (after,)).fetchall()
            for row in rows:
                try:
                    url = urlsplit(row['url'])
                    if not row['visited_at_utc']: continue
                    if url.hostname in ('127.0.0.1','localhost') and url.path == '/auth/callback': continue
                    if url.scheme not in ('http', 'https') or not url.hostname or len(row['url']) > 32768 or len(url.hostname) > 253 or '\0' in row['url']: continue
                    at = utc(row['visited_at_utc'])
                    identity = str(row['archive_id']) + ':' + at + ':' + row['url']
                    common = {'device_id': device, 'source': SOURCE}
                    search = parse_search(row['url'])
                    payloads = []
                    if search:
                        payloads.append(('search', dict(common, query=search[0], domain=search[1], searched_at=at, capture_version=1)))
                    if include_visits:
                        payloads.append(('visit', dict(common, url=row['url'], title=(row['title'] or '').replace('\0','\ufffd')[:4096], domain=url.hostname, visited_at=at)))
                    for kind, payload in payloads:
                        payload['id'] = str(uuid.uuid5(uuid.UUID(device), kind + ':' + identity))
                        db.execute('INSERT OR IGNORE INTO outbox(kind,id,payload) VALUES (?,?,?)', (kind, payload['id'], json.dumps(payload, ensure_ascii=False, sort_keys=True)))
                except (ValueError, UnicodeError): continue
            if rows: self.put(db, key, rows[-1]['archive_id'])
            return len(rows) == 1000
    def upload(self, value, kind):
        with self.db() as db:
            pending = db.execute('SELECT id,payload FROM outbox WHERE kind=? AND synced=0 ORDER BY rowid LIMIT 100', (kind,)).fetchall()
        batch, size = [], 0
        for row in pending:
            length = len(row['payload'].encode())
            if batch and size + length > 500_000: break
            batch.append(json.loads(row['payload'])); size += length
        if not batch: return False
        expected = {record['id'] for record in batch}
        ack = self.api(value, 'archive_searches' if kind == 'search' else 'archive_visits', {'records': batch})
        if not isinstance(ack, list) or set(ack) != expected: raise SyncError('Incomplete acknowledgment; queued records retained')
        with self.db() as db:
            db.executemany('UPDATE outbox SET synced=1 WHERE kind=? AND id=?', [(kind, identity) for identity in expected])
        return len(batch) == len(pending)
    def cache(self, db, kind, record):
        for key in ('id', 'device_id'): uuid.UUID(record[key])
        if record['source'] not in ('chrome_android','chrome_android_share',SOURCE): raise SyncError('Unsupported capture source')
        at = utc(record['searched_at'] if kind == 'search' else record['visited_at'])
        if kind == 'search':
            url = search_url(record['domain'], record['query'])
            if parse_search(url) != (record['query'], record['domain']): raise SyncError('Invalid shared search record')
            title = record['query']
            keys = ('id','device_id','query','domain','searched_at','source','capture_version')
        else:
            if record['source'] in ('chrome_android','chrome_android_share') and record.get('capture_version') != 2:
                raise SyncError('Unsupported mobile browsing version; cloud copy retained')
            url, title = record['url'], record['title']
            if urlsplit(url).scheme not in ('http','https') or urlsplit(url).hostname != record['domain']: raise SyncError('Invalid shared browser URL')
            keys = ('id','device_id','url','title','domain','visited_at','source')
            if record.get('capture_version') == 2:
                if record['source'] not in ('chrome_android','chrome_android_share') or \
                    record.get('url_quality') not in ('document','address_bar','inferred_scheme','shared') or \
                    record.get('title_source') not in ('unavailable','pane','window','share') or \
                    record.get('tab_mode') not in ('normal','incognito','unknown') or \
                    not isinstance(record.get('observed_url'),str) or not 1 <= len(record['observed_url']) <= 32768:
                    raise SyncError('Invalid mobile browsing metadata; cloud copy retained')
                keys += ('observed_url','url_quality','title_source','tab_mode','capture_version')
        payload = json.dumps({key: record[key] for key in keys}, ensure_ascii=False, sort_keys=True)
        existing = db.execute('SELECT payload FROM remote WHERE kind=? AND id=?', (kind, record['id'])).fetchone()
        if existing and existing[0] != payload: raise SyncError('Conflicting immutable cloud record; existing copy retained')
        delta = datetime.fromisoformat(at) - EPOCH
        stamp = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        db.execute('INSERT OR IGNORE INTO remote VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (kind,record['id'],payload,url,title,record['domain'],at,stamp,record['device_id'],record['source'],utc()))
    def pull(self, value, kind):
        with self.db() as db:
            state = self.meta(db, 'pull_' + kind, {})
            if not state.get('resume'):
                if time.time() - state.get('full_at', 0) >= 86400:
                    state.update(after=None, id=None, full=True)
                elif state.get('after'):
                    state['after'] = (datetime.fromisoformat(utc(state['after'])) - timedelta(minutes=15)).isoformat()
                    state['id'] = ZERO
            page_size = 100 if kind == 'search' else 25
            page = self.api(value, 'read_archive_page' if kind == 'search' else 'read_visits_page',
                {'after_received': state.get('after'), 'after_id': state.get('id'), 'page_size': page_size})
            if not isinstance(page, list) or len(page) > page_size: raise SyncError('Invalid cloud page')
            for record in page: self.cache(db, kind, record)
            if page: state.update(after=page[-1]['received_at'], id=page[-1]['id'])
            state['resume'] = len(page) == page_size
            if not state['resume'] and state.get('full'):
                state.update(full_at=time.time(), full=False)
            self.put(db, 'pull_' + kind, state)
            return state['resume']
    def sync_once(self):
        with self.guard():
            value = self.vault.load()
            if not value.get('session'): return False
            more = False
            for kind in ('search','visit'):
                for _ in range(4):
                    if not self.consume(value, kind): break
                else: more = True
            with self.db() as db:
                self.put(db, 'last_sync', utc())
                self.put(db, 'error', '')
            return more
    def consume(self, value, kind):
        # Always read the head of the relay. Deleted rows and late arrivals need no cursor.
        page_size = 100
        rpc = 'read_archive_page' if kind == 'search' else 'read_visits_page'
        try:
            page = self.api(value, rpc, {'after_received': None, 'after_id': None, 'page_size': page_size})
        except SyncError as error:
            if kind != 'visit' or str(error) != 'Cloud response is too large': raise
            page_size = 25
            page = self.api(value, rpc, {'after_received': None, 'after_id': None, 'page_size': page_size})
        if not isinstance(page, list) or len(page) > page_size: raise SyncError('Invalid relay page')
        if not page: return False
        with self.db() as db:
            for record in page: self.cache(db, kind, record)
        # The FULL-synchronous transaction above is closed and committed BEFORE deletion.
        # Read the committed copy back from a separate connection, including every payload field.
        with self.db() as db:
            for record in page:
                saved = db.execute('SELECT payload FROM remote WHERE kind=? AND id=?', (kind,record['id'])).fetchone()
                if not saved or any(record.get(k) != v for k,v in json.loads(saved[0]).items()):
                    raise SyncError('Local verification failed; cloud copy retained')
        expected = {record['id'] for record in page}
        ack = self.api(value, 'consume_archive_records', {'record_kind': kind, 'records': page})
        if not isinstance(ack,list) or len(ack) != len(expected) or set(ack) != expected:
            raise SyncError('Cloud deletion unconfirmed; permanent local copies retained')
        with self.db() as db:
            self.put(db,'relay_deleted', self.meta(db,'relay_deleted',0) + len(expected))
        return len(page) == page_size
    def status(self):
        try:
            value = self.vault.load()
            with self.db() as db:
                counts = {row[0]: row[1] for row in db.execute('SELECT kind,count(*) FROM remote GROUP BY kind')}
                pending = 0 # Desktop history is local-only; legacy outbox is never sent.
                return dict(configured=bool(value.get('url')), signed_in=bool(value.get('session')), project=value.get('url',''),
                    email=value.get('email',''), include_visits=False, pending=pending, relay_only=True,
                    relay_deleted=self.meta(db,'relay_deleted',0),
                    shared_searches=counts.get('search',0), shared_visits=counts.get('visit',0),
                    last_sync=self.meta(db,'last_sync'), error=self.meta(db,'error',''))
        except (OSError, ValueError, sqlite3.Error, SyncError): return {'error': 'Shared archive unavailable; existing files retained'}
    def wake(self): self.state.with_name('.cloud-sync-now').touch()
    def start(self):
        def run():
            while not self.stop.is_set():
                try:
                    with WindowsLock('Local\\DataSync-Profile13-Worker', 0):
                        while not self.stop.is_set():
                            try: more = self.sync_once()
                            except Exception as error:
                                with self.db() as db:
                                    self.put(db,'error',str(error) if isinstance(error,SyncError) else 'Sync unavailable; records retained')
                                more = False
                            for _ in range(1 if more else 30):
                                wake = self.state.with_name('.cloud-sync-now')
                                if wake.exists(): wake.unlink(missing_ok=True); break
                                if self.stop.wait(1): return
                except SyncError: self.stop.wait(5)
        threading.Thread(target=run, name='SharedArchiveSync', daemon=True).start()

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--daemon', action='store_true')
    arguments = parser.parse_args()
    bridge = Bridge()
    if arguments.daemon:
        try:
            with WindowsLock('Local\\DataSync-Profile13-Daemon',0):
                bridge.start()
                while True: time.sleep(60)
        except KeyboardInterrupt: bridge.stop.set()
        except SyncError: pass
    else: print(json.dumps(bridge.status(), indent=2))
