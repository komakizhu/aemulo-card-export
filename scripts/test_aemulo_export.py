"""Synthetic data only. Run with python3 -m unittest discover -s scripts."""
import contextlib
import io
import json
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

import aemulo_export as lib


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'source'
        self.folder.mkdir()
        self.db = sqlite3.connect(self.folder / lib.DB)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE MiFareTagTable (uuid TEXT, isFavourite INTEGER, dateAdded TEXT, name TEXT, uid BLOB, sak INTEGER, atqa INTEGER, family TEXT, RF_PROTOCOL INTEGER)')
        for i in range(3):
            self.db.execute('INSERT INTO MiFareTagTable VALUES (?,?,?,?,?,?,?,?,?)',
                            (f'00000000-0000-4000-8000-{i:012d}', i % 2, '2024-01-01T00:00:00', None,
                             bytes([0, 1, 2, i]), 8, 4, 'MiFARE Classic 1K', 2))
        self.db.commit()

    def json_input(self, obj=None):
        source = self.root / 'records.json'
        source.write_bytes(lib.dump(obj if obj is not None else lib.extract(self.folder)))
        return source

    def test_roundtrip_all_source_fields(self):
        source = self.json_input()
        destination = self.root / 'cards.nfccards'
        with contextlib.redirect_stdout(io.StringIO()):
            lib.convert(source, destination)
        rows = json.loads(source.read_text())['tables'][0]['rows']
        cards = json.loads(destination.read_text())['cards']
        self.assertEqual(len(cards), 3)
        for row, card in zip(rows, cards):
            self.assertEqual(json.loads(card['details']['Aemulo original record (JSON)']), row)
            self.assertEqual(card['uid'], row['uid']['data'])
            self.assertEqual(card['rfParameters']['atqa'], '0004')
            self.assertEqual(card['records'], [])
        self.assertEqual(cards[0]['name'], '卡片 1')

    def test_wal_rows_included_without_changing_original(self):
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        self.db.execute("UPDATE MiFareTagTable SET name='WAL update'")
        self.db.commit()
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        obj = lib.extract(self.folder)
        self.assertTrue(all(r['name'] == 'WAL update' for r in obj['tables'][0]['rows']))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir()})

    def test_unsupported_and_invalid_records_create_no_archive(self):
        obj = lib.extract(self.folder)
        obj['tables'].append({'name': 'FutureTag', 'rows': [{'data': 'x'}], 'columns': ['data']})
        destination = self.root / 'cards.nfccards'
        with self.assertRaises(ValueError):
            lib.convert(self.json_input(obj), destination)
        self.assertFalse(destination.exists())
        obj['tables'].pop()
        obj['tables'][0]['rows'][0]['sak'] = 256
        with self.assertRaises(ValueError):
            lib.convert(self.json_input(obj), destination)
        self.assertFalse(destination.exists())

    def test_existing_output_preserved(self):
        target = self.root / 'existing.nfccards'
        target.write_bytes(b'keep me')
        with self.assertRaises(FileExistsError):
            lib.convert(self.json_input(), target)
        self.assertEqual(target.read_bytes(), b'keep me')

    def test_changing_source_stops(self):
        class Changing:
            counter = 0
            def ls(self, path):
                return [{'path': '/group/' + lib.DB}]
            def request(self, path):
                self.counter += 1
                return str(self.counter).encode()
        with self.assertRaisesRegex(ValueError, 'changing'):
            lib.stable_snapshot(Changing(), '/group/' + lib.DB)

    def test_webdav_export_end_to_end(self):
        group = lib.GROUP_ROOT + 'SYNTHETIC-GROUP/'
        storage_group = lib.GROUP_ROOT + 'SYNTHETIC-FILES/'
        storage = storage_group + 'File Provider Storage/'
        files = {group + lib.DB: (self.folder / lib.DB).read_bytes(),
                 group + '.com.apple.mobile_container_manager.metadata.plist':
                 plistlib.dumps({'MCMMetadataIdentifier': 'group.amywhile.aemulo'}),
                 storage_group + '.com.apple.mobile_container_manager.metadata.plist':
                 plistlib.dumps({'MCMMetadataIdentifier': 'group.com.apple.FileProvider.LocalStorage'})}
        methods = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                methods.append('GET')
                path = urllib.parse.unquote(self.path)
                if path not in files:
                    self.send_error(404); return
                self.send_response(200); self.end_headers(); self.wfile.write(files[path])
            def do_PUT(self):
                methods.append('PUT')
                path = urllib.parse.unquote(self.path)
                if not path.startswith(storage) or '/' in path[len(storage):] or not path.endswith('.nfccards'):
                    self.send_error(403); return
                if path in files or self.headers.get('If-None-Match') != '*':
                    self.send_error(412); return
                files[path] = self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(201); self.end_headers()
            def do_PROPFIND(self):
                methods.append('PROPFIND')
                path = urllib.parse.unquote(self.path)
                if path == lib.GROUP_ROOT:
                    children = [group, storage_group]
                elif path == group:
                    children = [p for p in files if p.startswith(group)]
                elif path == storage_group:
                    children = [storage] + [p for p in files if p.startswith(storage_group) and not p.startswith(storage)]
                else:
                    children = [p for p in files if p.startswith(storage)] if path == storage else []
                items = []
                for path in [path] + children:
                    kind = '<D:collection/>' if path.endswith('/') else ''
                    items.append(f'<D:response><D:href>{escape(path)}</D:href><D:propstat><D:prop><D:resourcetype>{kind}</D:resourcetype><D:getetag>stable</D:getetag></D:prop></D:propstat></D:response>')
                self.send_response(207); self.end_headers()
                self.wfile.write(('<D:multistatus xmlns:D="DAV:">' + ''.join(items) + '</D:multistatus>').encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                lib.export(f'http://127.0.0.1:{server.server_port}', self.root / 'export')
            manifest = json.loads((self.root / 'export/manifest.json').read_text())
            self.assertEqual(manifest['tableCounts'], {'MiFareTagTable': 3})
            self.assertEqual((self.root / 'export' / lib.DB).read_bytes(), files[group + lib.DB])
            self.assertEqual(set(methods), {'GET', 'PROPFIND'})
            archive = self.root / 'Aemulo-cards.nfccards'
            dav = lib.DAV(f'http://127.0.0.1:{server.server_port}')
            with contextlib.redirect_stdout(io.StringIO()):
                lib.convert(self.root / 'export/aemulo-records.json', archive)
                lib.deliver(dav, archive)
                lib.deliver(dav, archive)  # Identical delivery is idempotent.
            self.assertEqual(files[storage + archive.name], archive.read_bytes())
            self.assertEqual(methods.count('PUT'), 1)
            archive.write_bytes(b'different')
            with self.assertRaisesRegex(ValueError, 'differs'):
                lib.deliver(dav, archive)
            self.assertEqual(methods.count('PUT'), 1)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_default_database_avoids_recursive_search(self):
        class Default:
            calls = []
            def ls(self, path):
                self.calls.append(path)
                return [{'path': '/aemulo/' + lib.DB, 'directory': False}, {'path': '/aemulo/Library/', 'directory': True}]
        dav = Default()
        with patch.object(lib, 'containers_for', return_value=['/aemulo/']):
            self.assertEqual(lib.locate(dav), '/aemulo/' + lib.DB)
        self.assertEqual(dav.calls, ['/aemulo/'])

    def test_default_miss_searches_aemulo_subdirectories(self):
        class Fallback:
            def ls(self, path):
                if path == '/aemulo/':
                    return [{'path': '/aemulo/Library/', 'directory': True}]
                if path == '/aemulo/Library/':
                    return [{'path': path + lib.DB, 'directory': False}]
                return []
        with patch.object(lib, 'containers_for', side_effect=[['/aemulo/'], []]):
            self.assertEqual(lib.locate(Fallback()), '/aemulo/Library/' + lib.DB)

    def test_missing_files_root_does_not_write(self):
        class Missing:
            def ls(self, path):
                return []
            def request(self, *args):
                raise AssertionError('Must not write or guess a destination')
        with patch.object(lib, 'containers_for', return_value=['/files/']):
            with self.assertRaisesRegex(ValueError, 'missing'):
                lib.deliver(Missing(), self.root / 'unused.nfccards')


if __name__ == '__main__':
    unittest.main()
