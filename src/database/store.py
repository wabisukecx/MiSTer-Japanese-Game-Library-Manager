import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from metadata.matcher import normalize, regions
from systems import SYSTEMS, system_profile
from local_rom import rom_info


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS systems (id TEXT PRIMARY KEY);
        INSERT OR IGNORE INTO systems VALUES ('NES');
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY, device TEXT NOT NULL, system TEXT NOT NULL REFERENCES systems(id),
            rom_path TEXT NOT NULL, rom_filename TEXT NOT NULL, original_title TEXT NOT NULL,
            japanese_title TEXT, region TEXT, crc32 TEXT, sha1 TEXT,
            artwork_source TEXT, artwork_region TEXT, artwork_path TEXT,
            match_status TEXT NOT NULL DEFAULT 'unmatched', sync_status TEXT NOT NULL DEFAULT 'blocked',
            UNIQUE(device,system,rom_path));
        CREATE TABLE IF NOT EXISTS title_aliases (
            id INTEGER PRIMARY KEY, system TEXT NOT NULL REFERENCES systems(id), alias TEXT NOT NULL,
            japanese_title TEXT NOT NULL, source TEXT NOT NULL,
            UNIQUE(system,alias,japanese_title,source));
        CREATE TABLE IF NOT EXISTS match_candidates (
            id INTEGER PRIMARY KEY, game_id INTEGER REFERENCES games(id), payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS title_decisions (
            id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL REFERENCES games(id), payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS title_decisions_game ON title_decisions(game_id);
        CREATE TABLE IF NOT EXISTS artworks (
            id INTEGER PRIMARY KEY, game_id INTEGER REFERENCES games(id), source TEXT NOT NULL,
            region TEXT NOT NULL, url TEXT NOT NULL, local_path TEXT NOT NULL, sha256 TEXT NOT NULL,
            UNIQUE(game_id,url));
        CREATE TABLE IF NOT EXISTS sync_manifests (
            id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS local_roms (
            game_id INTEGER PRIMARY KEY REFERENCES games(id), local_rom_path TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artwork_edits (
            artwork_id INTEGER PRIMARY KEY REFERENCES artworks(id),
            source_artwork_id INTEGER NOT NULL REFERENCES artworks(id),
            rotation_degrees INTEGER NOT NULL CHECK(rotation_degrees IN (90,180,270)),
            original_sha256 TEXT NOT NULL);
        ''')
        with self.db:
            if 'archive_member' not in {r['name'] for r in self.db.execute('PRAGMA table_info(local_roms)')}:
                self.db.execute('ALTER TABLE local_roms ADD COLUMN archive_member TEXT')
            self.db.executemany('INSERT OR IGNORE INTO systems VALUES (?)', [(s,) for s in SYSTEMS])
        try:
            self._remove_library_groups(path)
            self._standardize_ngp_destination(path)
        except Exception:
            self.db.close()
            raise

    def _backup_before_migration(self, path, suffix):
        backup = Path(str(path) + suffix)
        if not backup.exists():
            temporary = backup.with_suffix(backup.suffix + '.tmp')
            try:
                with closing(sqlite3.connect(temporary)) as destination:
                    self.db.backup(destination)
                temporary.replace(backup)
            finally:
                temporary.unlink(missing_ok=True)

    def _standardize_ngp_destination(self, path):
        """Normalize old transfer targets; PC ROM and image paths remain per system."""
        old_root, new_root = '/media/fat/games/NGP', '/media/fat/games/NGPC'
        games = self.db.execute("SELECT * FROM games WHERE system IN ('NGP','NGPC') AND rom_path LIKE ?",
                                (old_root + '/%',)).fetchall()
        profiles = []
        for system in ('NGP', 'NGPC'):
            key = 'workflow/' + system
            profile = self.setting(key, {})
            if profile.get('remote_root') == old_root:
                profiles.append((key, dict(profile, remote_root=new_root)))
        if not games and not profiles:
            return
        self._backup_before_migration(path, '.before-ngpc-folder.sqlite3')
        with self.db:
            for old in games:
                destination = new_root + old['rom_path'][len(old_root):]
                target = self.db.execute('SELECT id FROM games WHERE system=? AND rom_path=?',
                                         (old['system'], destination)).fetchone()
                if target:
                    self._merge_legacy_game(target['id'], old)
                else:
                    self.db.execute('UPDATE games SET rom_path=? WHERE id=?', (destination, old['id']))
            for key, profile in profiles:
                self.db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(profile, ensure_ascii=False), key))

    def _remove_library_groups(self, path):
        """Keep the legacy device column empty; merge old groups with a backup first."""
        legacy = self.db.execute("SELECT 1 FROM games WHERE device!='' LIMIT 1").fetchone()
        profiles = self.db.execute("SELECT key,value FROM settings WHERE key LIKE 'workflow/%/%' ORDER BY rowid").fetchall()
        if not legacy and not profiles:
            return
        self._backup_before_migration(path, '.before-library-removal.sqlite3')
        scope = self.setting('ui/scope', {}) or {}
        with self.db:
            profiles = sorted(profiles, key=lambda r: r['key'].split('/')[1] == scope.get('device'))
            existing = {r[0] for r in self.db.execute("SELECT key FROM settings WHERE key LIKE 'workflow/%' AND key NOT LIKE 'workflow/%/%'")}
            for row in profiles:
                profile = json.loads(row['value'])
                profile.pop('device', None)
                key = 'workflow/' + row['key'].rsplit('/', 1)[1]
                if key not in existing:
                    self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(profile, ensure_ascii=False)))
                self.db.execute('DELETE FROM settings WHERE key=?', (row['key'],))
            if 'device' in scope:
                scope.pop('device')
                self.db.execute('UPDATE settings SET value=? WHERE key=?', (json.dumps(scope), 'ui/scope'))
            for old in self.db.execute('SELECT * FROM games ORDER BY id').fetchall():
                target = self.db.execute("SELECT id FROM games WHERE system=? AND rom_path=? AND device=''",
                                         (old['system'], old['rom_path'])).fetchone()
                if target is None:
                    self.db.execute("UPDATE games SET device='' WHERE id=?", (old['id'],))
                elif target['id'] != old['id']:
                    self._merge_legacy_game(target['id'], old)

    def _merge_legacy_game(self, game_id, old):
        """Preserve evidence and image edits when old groups contain the same ROM."""
        current = self.game(game_id)
        if current['match_status'] == 'unmatched' and old['match_status'] == 'review':
            self.db.execute("UPDATE games SET match_status='review' WHERE id=?", (game_id,))
        self.db.execute('UPDATE match_candidates SET game_id=? WHERE game_id=?', (game_id, old['id']))
        self.db.execute('UPDATE title_decisions SET game_id=? WHERE game_id=?', (game_id, old['id']))
        for artwork in self.db.execute('SELECT id,url FROM artworks WHERE game_id=?', (old['id'],)).fetchall():
            url = artwork['url']
            if self.db.execute('SELECT 1 FROM artworks WHERE game_id=? AND url=?', (game_id, url)).fetchone():
                url += '#legacy-artwork-' + str(artwork['id'])
            self.db.execute('UPDATE artworks SET game_id=?,url=? WHERE id=?', (game_id, url, artwork['id']))
        if old['japanese_title'] and old['match_status'] == 'confirmed':
            conflicted = any(c.get('source') == 'legacy-library' for c in self.get_candidates(game_id))
            if conflicted or (current['japanese_title'] and current['japanese_title'] != old['japanese_title']):
                for title in (current['japanese_title'], old['japanese_title']):
                    if not title:
                        continue
                    self.db.execute('INSERT INTO match_candidates(game_id,payload) VALUES (?,?)',
                        (game_id, json.dumps(dict(japanese_title=title, score=1, source='legacy-library',
                            reason='旧ライブラリの確定名が競合'), ensure_ascii=False)))
                self.db.execute("UPDATE games SET japanese_title=NULL,match_status='review',sync_status='blocked' WHERE id=?", (game_id,))
            elif current['match_status'] != 'confirmed':
                self.db.execute("UPDATE games SET japanese_title=?,match_status='confirmed' WHERE id=?", (old['japanese_title'], game_id))
        if not current['artwork_path'] and old['artwork_path']:
            self.db.execute('UPDATE games SET artwork_path=?,artwork_source=?,artwork_region=? WHERE id=?',
                            (old['artwork_path'], old['artwork_source'], old['artwork_region'], game_id))
        local = self.db.execute('SELECT local_rom_path,archive_member FROM local_roms WHERE game_id=?', (old['id'],)).fetchone()
        if local:
            self.db.execute('INSERT OR IGNORE INTO local_roms VALUES (?,?,?)', (game_id, local['local_rom_path'], local['archive_member']))
        self.db.execute('DELETE FROM local_roms WHERE game_id=?', (old['id'],))
        self.db.execute('DELETE FROM games WHERE id=?', (old['id'],))

    def close(self):
        self.db.close()

    def prune_missing_artworks(self, *, cancelled=lambda: False, game_ids=None):
        """Remove missing file references; keep unreadable files and valid edited copies."""
        def missing(path):
            try:
                Path(path).stat()
            except (FileNotFoundError, NotADirectoryError):
                return True
            except OSError:
                # A permissions/network failure is not evidence of deletion.
                return False
            return False
        rows = list(self.db.execute('SELECT id,game_id,local_path FROM artworks'))
        missing_ids = []
        for row in rows:
            if game_ids is not None and row['game_id'] not in game_ids:
                continue
            if cancelled():
                raise InterruptedError('画像リンクの確認を中断しました')
            if missing(row['local_path']):
                missing_ids.append(row['id'])
        selected = []
        for game in self.db.execute('SELECT id,artwork_path FROM games WHERE artwork_path IS NOT NULL'):
            if game_ids is not None and game['id'] not in game_ids:
                continue
            if cancelled():
                raise InterruptedError('画像リンクの確認を中断しました')
            if missing(game['artwork_path']):
                selected.append(game['id'])
        with self.db:
            for artwork_id in missing_ids:
                self.db.execute('DELETE FROM artwork_edits WHERE artwork_id=? OR source_artwork_id=?',
                                (artwork_id, artwork_id))
            self.db.executemany('DELETE FROM artworks WHERE id=?', [(i,) for i in missing_ids])
            self.db.executemany('''UPDATE games SET artwork_path=NULL,artwork_source=NULL,
                artwork_region=NULL WHERE id=?''', [(i,) for i in selected])
        return dict(removed=len(missing_ids), cleared=len(selected))

    def setting(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            (key, json.dumps(value, ensure_ascii=False)))

    def upsert_game(self, rom_path, system='NES'):
        system_profile(system)
        name = PurePosixPath(rom_path).name
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO games
                (device,system,rom_path,rom_filename,original_title,region) VALUES(?,?,?,?,?,?)''',
                ('', system, rom_path, name, normalize(name), ','.join(regions(name))))
        return self.db.execute('SELECT id FROM games WHERE device=? AND system=? AND rom_path=?',
                               ('', system, rom_path)).fetchone()[0]

    def games(self, pending=False):
        return [dict(r) for r in self.db.execute('''SELECT games.*,local_roms.local_rom_path,local_roms.archive_member FROM games
            LEFT JOIN local_roms ON local_roms.game_id=games.id
            WHERE (?=0 OR match_status!='confirmed') ORDER BY id''',
            (int(pending),))]

    def game(self, game_id):
        row = self.db.execute('''SELECT games.*,local_roms.local_rom_path,local_roms.archive_member FROM games
            LEFT JOIN local_roms ON local_roms.game_id=games.id WHERE games.id=?''', (game_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown game ID')
        return dict(row)

    def register_local_rom(self, system, path, remote_root=None):
        profile = system_profile(system)
        root = remote_root or profile.root
        source = Path(path).resolve(strict=True)
        if (root not in profile.roots or not source.is_file()
                or source.suffix.lower() not in profile.extensions
                or any(ord(c) < 32 for c in source.name) or '\\' in source.name):
            raise ValueError('機種・ROMファイル・転送先を確認してください')
        info = rom_info(source, system)
        game_id = self.upsert_game(str(PurePosixPath(root) / info['name']), system)
        with self.db:
            self.db.execute('''INSERT INTO local_roms(game_id,local_rom_path,archive_member) VALUES (?,?,?) ON CONFLICT(game_id)
                DO UPDATE SET local_rom_path=excluded.local_rom_path,archive_member=excluded.archive_member''',
                (game_id, str(source), info['archive_member']))
        if info['archive_member'] is not None:
            self.adopt_legacy_extracted(game_id)
        return game_id

    def adopt_legacy_extracted(self, game_id):
        """Fold a game registered earlier under the extracted ROM name (Game.nes) into its ZIP (Game.zip).

        Games (including ones listed from the MiSTer scan) with the same system/folder and file stem qualify; titles, images and
        the selection move to the ZIP entry unless it already has its own."""
        new = self.game(game_id)
        stem = PurePosixPath(new['rom_path']).stem.casefold()
        folder = str(PurePosixPath(new['rom_path']).parent)
        for old in self.db.execute('''SELECT games.* FROM games
                WHERE device=? AND system=? AND games.id!=?''', (new['device'], new['system'], game_id)).fetchall():
            path = PurePosixPath(old['rom_path'])
            if str(path.parent) != folder or path.stem.casefold() != stem or path.suffix.lower() == '.zip':
                continue
            with self.db:
                self.db.execute('UPDATE title_decisions SET game_id=? WHERE game_id=?', (game_id, old['id']))
                self.db.execute('UPDATE OR IGNORE artworks SET game_id=? WHERE game_id=?', (game_id, old['id']))
                if new['match_status'] != 'confirmed' and old['match_status'] == 'confirmed':
                    self.db.execute("UPDATE games SET japanese_title=?,match_status='confirmed' WHERE id=?",
                                    (old['japanese_title'], game_id))
                    self.record_title_decision(game_id, 'adopted', None, old['japanese_title'])
                if not new['artwork_path'] and old['artwork_path']:
                    self.db.execute('UPDATE games SET artwork_source=?,artwork_region=?,artwork_path=? WHERE id=?',
                                    (old['artwork_source'], old['artwork_region'], old['artwork_path'], game_id))
                self.db.execute('DELETE FROM artwork_edits WHERE artwork_id IN (SELECT id FROM artworks WHERE game_id=?)',
                                (old['id'],))
                self.db.execute('DELETE FROM match_candidates WHERE game_id=?', (old['id'],))
                self.db.execute('DELETE FROM local_roms WHERE game_id=?', (old['id'],))
                self.db.execute('DELETE FROM artworks WHERE game_id=?', (old['id'],))
                self.db.execute('DELETE FROM games WHERE id=?', (old['id'],))
            new = self.game(game_id)

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
        else:
            with self.db:
                self.db.execute('BEGIN')
                yield

    def save_game_review(self, game_id, title, artwork_id=None, rotation=None):
        """Commit the displayed title and image together, or roll back both."""
        with self.transaction():
            if rotation is not None:
                artwork_id = self.register_rotated_artwork(game_id, artwork_id, rotation)
            if artwork_id is not None:
                self.select_artwork(game_id, artwork_id)
            game = self.game(game_id)
            if title.strip() and (game['match_status'] != 'confirmed' or game['japanese_title'] != title):
                self.confirm(game_id, title)
        return artwork_id

    def confirm(self, game_id, title):
        game = self.game(game_id)
        if not title.strip() or any(ord(c) < 32 for c in title):
            raise ValueError('Title must be nonempty and contain no control characters')
        with self.transaction():
            self.db.execute("UPDATE games SET japanese_title=?,match_status='confirmed' WHERE id=?",
                            (title, game_id))
            self.record_title_decision(game_id, 'confirmed', game['japanese_title'], title)

    def unconfirm(self, game_id):
        """Drop a confirmation so the next match can propose titles again; candidates are kept."""
        game = self.game(game_id)
        has_candidates = self.db.execute('SELECT 1 FROM match_candidates WHERE game_id=? LIMIT 1',
                                         (game_id,)).fetchone() is not None
        with self.db:
            self.db.execute('UPDATE games SET japanese_title=NULL,match_status=? WHERE id=?',
                            ('review' if has_candidates else 'unmatched', game['id']))
            self.record_title_decision(game_id, 'unconfirmed', game['japanese_title'], None)

    def update_confirmed_title(self, game_id, expected, title):
        """Replace a confirmed title only if it is still the one the user reviewed."""
        if not title.strip() or any(ord(c) < 32 for c in title):
            raise ValueError('Title must be nonempty and contain no control characters')
        with self.db:
            changed = self.db.execute("""UPDATE games SET japanese_title=? WHERE id=? AND match_status='confirmed'
                AND japanese_title IS ?""", (title, game_id, expected)).rowcount
            if changed:
                self.record_title_decision(game_id, 'updated', expected, title)
        return bool(changed)

    def record_title_decision(self, game_id, action, previous_title, title):
        """Append inside the caller's transaction so a failed approval also rolls back its history."""
        from datetime import datetime, timezone
        from uuid import uuid4
        payload = dict(id=str(uuid4()), action=action, previous_title=previous_title, title=title,
            recorded_at=datetime.now(timezone.utc).isoformat(), evidence=self.get_candidates(game_id))
        self.db.execute('INSERT INTO title_decisions(game_id,payload) VALUES (?,?)',
                        (game_id, json.dumps(payload, ensure_ascii=False)))

    def title_decisions(self, game_id):
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT payload FROM title_decisions WHERE game_id=? ORDER BY id', (game_id,))]

    def delete_aliases(self, source):
        with self.db:
            return self.db.execute('DELETE FROM title_aliases WHERE source=?', (source,)).rowcount

    def save_candidates(self, game_id, items):
        self.game(game_id)
        with self.db:
            self.db.execute('DELETE FROM match_candidates WHERE game_id=?', (game_id,))
            self.db.executemany('INSERT INTO match_candidates(game_id,payload) VALUES(?,?)',
                                [(game_id, json.dumps(item, ensure_ascii=False)) for item in items])
            self.db.execute("""UPDATE games SET match_status=? WHERE id=? AND match_status!='confirmed'""",
                            ('review' if items else 'unmatched', game_id))

    def get_candidates(self, game_id):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT payload FROM match_candidates WHERE game_id=? ORDER BY id', (game_id,))]

    def import_aliases(self, items):
        with self.db:
            for a in items:
                system_profile(a['system'])
                if not all(isinstance(a[k], str) and a[k].strip()
                                                   for k in ('alias', 'japanese_title', 'source')):
                    raise ValueError('Aliases require a supported system, alias, japanese_title and source')
                self.db.execute('''INSERT OR IGNORE INTO title_aliases(system,alias,japanese_title,source)
                    VALUES(?,?,?,?)''', (a['system'], a['alias'], a['japanese_title'], a['source']))

    def aliases(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM title_aliases')]

    def save_artwork(self, game_id, source, region, url, path, sha256, *, select=True):
        self.game(game_id)
        with self.db:
            previous = self.db.execute('SELECT local_path,sha256 FROM artworks WHERE game_id=? AND url=?',
                                       (game_id, url)).fetchone()
            if previous and previous['sha256'] != sha256:
                self.db.execute('''UPDATE games SET artwork_path=NULL,artwork_source=NULL,artwork_region=NULL
                    WHERE id=? AND artwork_path=?''', (game_id, previous['local_path']))
            self.db.execute('''INSERT INTO artworks(game_id,source,region,url,local_path,sha256)
                VALUES(?,?,?,?,?,?) ON CONFLICT(game_id,url) DO UPDATE SET
                local_path=excluded.local_path,sha256=excluded.sha256''',
                (game_id, source, region, url, str(path), sha256))
            if select:
                self.db.execute('''UPDATE games SET artwork_source=?,artwork_region=?,artwork_path=? WHERE id=?''',
                                (source, region, str(path), game_id))

    def adopt_library_copy(self, game_id, old_uri, new_path):
        """Point an artwork row recorded with a Skraper-folder URL at its library copy, keeping selection."""
        new_path = Path(new_path)
        new_uri = new_path.as_uri()
        row = self.db.execute('SELECT id,local_path FROM artworks WHERE game_id=? AND url=?',
                              (game_id, old_uri)).fetchone()
        if row is None or old_uri == new_uri or self.db.execute(
                'SELECT 1 FROM artworks WHERE game_id=? AND url=?', (game_id, new_uri)).fetchone():
            return
        with self.db:
            self.db.execute('UPDATE artworks SET url=?,local_path=? WHERE id=?', (new_uri, str(new_path), row['id']))
            self.db.execute('UPDATE games SET artwork_path=? WHERE id=? AND artwork_path=?',
                            (str(new_path), game_id, row['local_path']))

    def save_manifest(self, manifest):
        with self.db:
            self.db.execute('INSERT INTO sync_manifests(payload) VALUES(?)',
                            (json.dumps(manifest, ensure_ascii=False),))

    def artworks(self, game_id):
        # edit_stale: the source image was re-imported with different content after this copy was made.
        rows = [dict(row) for row in self.db.execute('''SELECT a.*,e.rotation_degrees,e.source_artwork_id,
            COALESCE(e.original_sha256!=s.sha256,0) AS edit_stale
            FROM artworks a LEFT JOIN artwork_edits e ON e.artwork_id=a.id
            LEFT JOIN artworks s ON s.id=e.source_artwork_id
            WHERE a.game_id=? ORDER BY a.id''', (game_id,))]
        by_id = {row['id']: row for row in rows}
        def stale(row, seen):
            parent = row.get('source_artwork_id')
            if parent is None:
                return bool(row['edit_stale'])
            return (row['id'] in seen or parent not in by_id or bool(row['edit_stale'])
                    or stale(by_id[parent], seen | {row['id']}))
        for row in rows:
            row['edit_stale'] = int(stale(row, set()))
        return rows

    def register_rotated_artwork(self, game_id, source_artwork_id, result):
        source = self.db.execute('SELECT * FROM artworks WHERE id=? AND game_id=?',
                                 (source_artwork_id, game_id)).fetchone()
        if source is None or source['sha256'] != result['original_sha256']:
            raise ValueError('補正元画像の登録情報が変更されています')
        path = Path(result['path']).resolve()
        uri = path.as_uri()
        if str(path) == str(Path(source['local_path']).resolve()):
            raise ValueError('補正画像は元画像と別ファイルにしてください')
        with self.transaction():
            self.db.execute('''INSERT OR IGNORE INTO artworks(game_id,source,region,url,local_path,sha256)
                VALUES(?,?,?,?,?,?)''', (game_id, source['source'], source['region'], uri, str(path), result['sha256']))
            artwork = self.db.execute('SELECT id,sha256 FROM artworks WHERE game_id=? AND url=?',
                                      (game_id, uri)).fetchone()
            if artwork['sha256'] != result['sha256']:
                raise ValueError('登録済みの補正画像と内容が異なります')
            self.db.execute('''INSERT OR IGNORE INTO artwork_edits
                (artwork_id,source_artwork_id,rotation_degrees,original_sha256) VALUES(?,?,?,?)''',
                (artwork['id'], source_artwork_id, result['rotation_degrees'], source['sha256']))
        return artwork['id']

    def select_artwork(self, game_id, artwork_id):
        candidate = next((a for a in self.artworks(game_id) if a['id'] == artwork_id), None)
        if candidate and candidate.get('edit_stale'):
            raise ValueError('補正元画像が更新されています。新しい元画像から補正し直してください')
        row = self.db.execute('SELECT * FROM artworks WHERE id=? AND game_id=?', (artwork_id, game_id)).fetchone()
        if row is None:
            raise ValueError('選択した画像はこのゲームの候補ではありません')
        with self.transaction():
            self.db.execute('UPDATE games SET artwork_source=?,artwork_region=?,artwork_path=? WHERE id=?',
                            (row['source'], row['region'], row['local_path'], game_id))
