#!/usr/bin/env python3
"""Filza source backup, conversion and delivery to On My iPhone. Standard library."""
import argparse
import base64
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET

LIMIT = 32 * 1024 * 1024
GROUP_ROOT = '/private/var/mobile/Containers/Shared/AppGroup/'
DB = 'TagDatabase.sqlite3'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected redirect; verify the Filza URL')


class DAV:
    def __init__(self, url):
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('', '/'):
            raise ValueError('Supply a Filza origin URL, without credentials or path')
        self.base = url.rstrip('/')
        self.headers = {}
        if os.environ.get('FILZA_USER') is not None:
            value = os.environ['FILZA_USER'] + ':' + os.environ.get('FILZA_PASSWORD', '')
            self.headers['Authorization'] = 'Basic ' + base64.b64encode(value.encode()).decode()

    def request(self, path, method='GET', headers=None, data=None):
        if not path.startswith('/') or path.startswith('//') or any(x in ('.', '..') for x in path.split('/')):
            raise ValueError('Invalid device path')
        url = self.base + urllib.parse.quote(path, safe='/')
        req = urllib.request.Request(url, method=method, headers={**self.headers, **(headers or {})}, data=data)
        # Separate opener per call permits bounded parallel metadata reads.
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with op.open(req, timeout=15) as response:
            data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('Response exceeds 32 MB; preserve data and inspect manually')
        return data

    def ls(self, path):
        data = self.request(path, 'PROPFIND', {'Depth': '1'})
        entries = []
        prefix = path.rstrip('/') + '/'
        for response in ET.fromstring(data).findall('{DAV:}response'):
            href = response.findtext('{DAV:}href', '')
            u = urllib.parse.urlsplit(href)
            if u.netloc and (u.scheme + '://' + u.netloc) != self.base:
                raise ValueError('Cross-origin DAV listing')
            child = urllib.parse.unquote(u.path)
            if child.rstrip('/') == path.rstrip('/'):
                continue
            if not child.startswith(prefix) or '/' in child[len(prefix):].rstrip('/'):
                raise ValueError('Unexpected non-child DAV entry')
            entries.append({'path': child, 'directory': response.find('.//{DAV:}collection') is not None,
                            'size': response.findtext('.//{DAV:}getcontentlength'),
                            'etag': response.findtext('.//{DAV:}getetag'),
                            'modified': response.findtext('.//{DAV:}getlastmodified')})
        return sorted(entries, key=lambda e: e['path'])


def containers_for(dav, root, identifier):
    cache = getattr(dav, '_container_index', {})
    if root in cache:
        return cache[root].get(identifier, [])
    containers = [e['path'] for e in dav.ls(root) if e['directory']]
    failures = []

    def identify(path):
        try:
            metadata = plistlib.loads(dav.request(path.rstrip('/') + '/.com.apple.mobile_container_manager.metadata.plist'))
            return metadata.get('MCMMetadataIdentifier'), path
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise
            failures.append(path)
        except (ValueError, OSError):
            failures.append(path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        index = {}
        for item in pool.map(identify, containers):
            if item:
                ident, path = item
                index.setdefault(ident, []).append(path)
    cache[root] = index
    dav._container_index = cache
    matches = index.get(identifier, [])
    if not matches and failures:
        raise ValueError(f'Container not found; {len(failures)} metadata files unreadable')
    return matches


def optional_ls(dav, path):
    try:
        return dav.ls(path)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return []
        raise


def locate(dav):
    matches = containers_for(dav, GROUP_ROOT, 'group.amywhile.aemulo')
    if len(matches) > 1:
        raise ValueError('Multiple Aemulo groups; inspect before selecting')
    # Normal location first: no recursive search when the root database exists.
    if matches:
        for entry in dav.ls(matches[0]):
            if not entry['directory'] and entry['path'] == matches[0].rstrip('/') + '/' + DB:
                return entry['path']
    # Only after a default miss: known Aemulo-specific containers and legacy folder.
    app_paths = containers_for(dav, '/private/var/mobile/Containers/Data/Application/', 'com.amywhile.Aemulo')
    roots = matches + app_paths
    legacy = '/private/var/mobile/Library/Application Support/Aemulo/'
    if optional_ls(dav, legacy):
        roots.append(legacy)
    queue, found, visited = [(p, 0) for p in roots], [], 0
    while queue:
        path, depth = queue.pop(0)
        visited += 1
        if visited > 200:
            raise ValueError('Aemulo container search exceeded 200 directories')
        for entry in dav.ls(path):
            if entry['directory'] and depth < 4:
                queue.append((entry['path'], depth + 1))
            elif not entry['directory'] and entry['path'].rsplit('/', 1)[-1] == DB:
                found.append(entry['path'])
    if len(found) != 1:
        raise ValueError(f'Expected one database within search depth; found {len(found)}')
    return found[0]


def iphone_root(dav):
    groups = containers_for(dav, GROUP_ROOT, 'group.com.apple.FileProvider.LocalStorage')
    if len(groups) != 1:
        raise ValueError('Could not uniquely locate On My iPhone; local export is preserved')
    root = groups[0].rstrip('/') + '/File Provider Storage/'
    entries = dav.ls(groups[0])
    if not any(e['directory'] and e['path'].rstrip('/') == root.rstrip('/') for e in entries):
        raise ValueError('File Provider Storage missing; do not create a guessed root or substitute LocalSend')
    dav.ls(root)
    return root


def deliver(dav, source):
    # The only remote mutation is a new result file in the identified Files root.
    root = iphone_root(dav)
    target = root + source.name
    data = source.read_bytes()
    try:
        previous = dav.request(target)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        dav.request(target, 'PUT', {'If-None-Match': '*', 'Content-Type': 'application/octet-stream'}, data)
    else:
        if previous != data:
            raise ValueError('Existing phone file differs; choose a new output filename')
    if dav.request(target) != data:
        raise ValueError('Phone readback mismatch; delivery is not verified')
    print(json.dumps({'savedTo': 'On My iPhone/' + source.name, 'bytes': len(data), 'readBackMatches': True,
                      'importedIntoApp': False}))


def stable_snapshot(dav, path):
    folder = path.rsplit('/', 1)[0] + '/'

    def inventory():
        return [e for e in dav.ls(folder) if e['path'] in (path, path + '-wal', path + '-shm')]

    for _ in range(3):
        before = inventory()
        if path not in [e['path'] for e in before]:
            raise ValueError('Database disappeared')
        first = {e['path'].rsplit('/', 1)[-1]: dav.request(e['path']) for e in before}
        middle = inventory()
        second = {e['path'].rsplit('/', 1)[-1]: dav.request(e['path']) for e in middle}
        if before == middle == inventory() and first == second:
            return first
    raise ValueError('Database is changing; stop activity in Aemulo and retry to a new folder')


def exclusive_write(path, data):
    with path.open('xb') as output:
        os.chmod(path, 0o600)
        output.write(data)


def encode(value):
    return {'encoding': 'base64', 'data': base64.b64encode(value).decode()} if isinstance(value, bytes) else value


def extract(folder):
    # SQLite can update shared memory/recover journals: only open a disposable copy.
    with tempfile.TemporaryDirectory(prefix='aemulo-inspect-') as temp:
        for name in (DB, DB + '-wal', DB + '-shm'):
            if (folder / name).exists():
                shutil.copy2(folder / name, Path(temp) / name)
        conn = sqlite3.connect((Path(temp) / DB).as_uri() + '?mode=ro', uri=True)
        try:
            conn.execute('PRAGMA query_only=ON')
            checks = [r[0] for r in conn.execute('PRAGMA integrity_check')]
            if checks != ['ok']:
                raise ValueError('SQLite integrity check failed')
            tables = []
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for name in names:
                cur = conn.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
                columns = [d[0] for d in cur.description]
                rows = [{k: encode(v) for k, v in zip(columns, row)} for row in cur]
                tables.append({'name': name, 'columns': columns, 'rows': rows})
            return {'format': 'aemulo.sqlite-export.v1', 'tables': tables}
        finally:
            conn.close()


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def export(url, folder, dav=None):
    if folder.exists():
        raise ValueError('Output folder already exists; choose a new one')
    dav = dav or DAV(url)
    path = locate(dav)
    files = stable_snapshot(dav, path)
    folder.mkdir(parents=True, mode=0o700)
    for name, data in files.items():
        exclusive_write(folder / name, data)
    records = extract(folder)
    exclusive_write(folder / 'aemulo-records.json', dump(records))
    counts = {t['name']: len(t['rows']) for t in records['tables']}
    exclusive_write(folder / 'manifest.json', dump({'databasePath': path, 'files': {k: len(v) for k, v in files.items()}, 'integrity': 'ok', 'tableCounts': counts}))
    print(json.dumps({'backup': str(folder), 'integrity': 'ok', 'tableCounts': counts}))


def convert(source, destination):
    if source.stat().st_size > LIMIT:
        raise ValueError('Input exceeds 32 MB')
    obj = json.loads(source.read_text(encoding='utf-8'))
    if obj.get('format') != 'aemulo.sqlite-export.v1':
        raise ValueError('Expected aemulo.sqlite-export.v1 JSON')
    unsupported = [t['name'] for t in obj['tables'] if t['name'] != 'MiFareTagTable' and t['rows']]
    if unsupported:
        raise ValueError('Nonempty unsupported tables; nothing converted: ' + ', '.join(unsupported))
    rows = [r for t in obj['tables'] if t['name'] == 'MiFareTagTable' for r in t['rows']]
    if not 1 <= len(rows) <= 512:
        raise ValueError('Expected 1-512 cards')
    now = datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000
    cards, ids = [], set()
    for i, row in enumerate(rows, 1):
        identity = str(uuid.UUID(row['uuid'])).upper()
        if identity in ids:
            raise ValueError('Duplicate card UUID')
        ids.add(identity)
        blob = row['uid']
        if blob['encoding'] != 'base64' or len(base64.b64decode(blob['data'], validate=True)) not in (4, 7, 10):
            raise ValueError('Invalid UID representation or length')
        if type(row['sak']) is not int or not 0 <= row['sak'] <= 255 or type(row['atqa']) is not int or not 0 <= row['atqa'] <= 65535:
            raise ValueError('Invalid SAK/ATQA')
        if type(row['isFavourite']) is not int or row['isFavourite'] not in (0, 1) or type(row['RF_PROTOCOL']) is not int:
            raise ValueError('Invalid favorite/protocol value')
        if row['name'] is not None and not isinstance(row['name'], str):
            raise ValueError('Invalid name type')
        name = row['name'] if row['name'] and row['name'].strip() else f'卡片 {i}'
        if len(name) > 80 or not isinstance(row['family'], str) or len(row['family']) > 120 or not isinstance(row['dateAdded'], str):
            raise ValueError('Name, family or date incompatible with target')
        sak, atqa = f"{row['sak']:02X}", f"{row['atqa']:04X}"
        details = {'Source': 'Aemulo / MiFareTagTable', 'Original dateAdded': row['dateAdded'], 'SAK': sak, 'ATQA': atqa,
                   'Family': row['family'], 'RF_PROTOCOL': str(row['RF_PROTOCOL']),
                   'Aemulo original record (JSON)': json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False)}
        if any(len(v) > 4096 for v in details.values()) or len(str(row['RF_PROTOCOL'])) > 100:
            raise ValueError('Source record exceeds target detail limits; raw backup preserved')
        cards.append({'id': identity, 'name': name, 'notes': '', 'accent': 'ocean', 'isFavorite': bool(row['isFavourite']),
                      'createdAt': now, 'modifiedAt': now, 'technology': 'mifare', 'uid': blob['data'], 'records': [], 'details': details,
                      'rfParameters': {'atqa': atqa, 'sak': sak, 'family': row['family'], 'protocolName': 'RF_PROTOCOL=' + str(row['RF_PROTOCOL']),
                                       'internalType': 'MiFareTagTable', 'sourceIdentifier': row['uuid']}})
    data = dump({'format': 'app.nfccardwallet.archive', 'version': 1, 'exportedAt': now, 'cards': cards})
    if len(data) > LIMIT:
        raise ValueError('Archive exceeds 32 MB')
    exclusive_write(destination, data)
    print(json.dumps({'archive': str(destination), 'cards': len(cards), 'installedOrImported': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    exp = sub.add_parser('export'); exp.add_argument('--url', required=True); exp.add_argument('--out', type=Path, required=True)
    conv = sub.add_parser('convert'); conv.add_argument('--input', type=Path, required=True); conv.add_argument('--output', type=Path, required=True)
    run = sub.add_parser('run', help='Export, convert all supported cards, deliver to On My iPhone root')
    run.add_argument('--url', required=True); run.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'export':
        export(args.url, args.out)
    elif args.command == 'convert':
        convert(args.input, args.output)
    else:
        dav = DAV(args.url)
        export(args.url, args.out, dav)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        archive = args.out / f'Aemulo-cards-{stamp}.nfccards'
        convert(args.out / 'aemulo-records.json', archive)
        deliver(dav, archive)


if __name__ == '__main__':
    main()
