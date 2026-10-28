import json
import sqlite3
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
        CREATE TABLE IF NOT EXISTS artworks (
            id INTEGER PRIMARY KEY, game_id INTEGER REFERENCES games(id), source TEXT NOT NULL,
            region TEXT NOT NULL, url TEXT NOT NULL, local_path TEXT NOT NULL, sha256 TEXT NOT NULL,
            UNIQUE(game_id,url));
        CREATE TABLE IF NOT EXISTS sync_manifests (
            id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_history (
            id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT, detail TEXT);
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

    def close(self):
        self.db.close()

    def prune_missing_artworks(self, *, cancelled=lambda: False):
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
        rows = list(self.db.execute('SELECT id,local_path FROM artworks'))
        missing_ids = []
        for row in rows:
            if cancelled():
                raise InterruptedError('画像リンクの確認を中断しました')
            if missing(row['local_path']):
                missing_ids.append(row['id'])
        selected = []
        for game in self.db.execute('SELECT id,artwork_path FROM games WHERE artwork_path IS NOT NULL'):
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

    def upsert_game(self, device, rom_path, system='NES'):
        system_profile(system)
        name = PurePosixPath(rom_path).name
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO games
                (device,system,rom_path,rom_filename,original_title,region) VALUES(?,?,?,?,?,?)''',
                (device, system, rom_path, name, normalize(name), ','.join(regions(name))))
        return self.db.execute('SELECT id FROM games WHERE device=? AND system=? AND rom_path=?',
                               (device, system, rom_path)).fetchone()[0]

    def games(self, device=None, pending=False):
        return [dict(r) for r in self.db.execute('''SELECT games.*,local_roms.local_rom_path,local_roms.archive_member FROM games
            LEFT JOIN local_roms ON local_roms.game_id=games.id
            WHERE (? IS NULL OR device=?) AND (?=0 OR match_status!='confirmed') ORDER BY id''',
            (device, device, int(pending)))]

    def game(self, game_id):
        row = self.db.execute('''SELECT games.*,local_roms.local_rom_path,local_roms.archive_member FROM games
            LEFT JOIN local_roms ON local_roms.game_id=games.id WHERE games.id=?''', (game_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown game ID')
        return dict(row)

    def register_local_rom(self, device, system, path, remote_root=None):
        profile = system_profile(system)
        root = remote_root or profile.root
        source = Path(path).resolve(strict=True)
        if (root not in profile.roots or not source.is_file()
                or source.suffix.lower() not in (*profile.transfer_extensions, '.zip')
                or any(ord(c) < 32 for c in source.name) or '\\' in source.name):
            raise ValueError('機種・ROMファイル・転送先を確認してください')
        info = rom_info(source, system)
        game_id = self.upsert_game(device, str(PurePosixPath(root) / info['name']), system)
        with self.db:
            self.db.execute('''INSERT INTO local_roms(game_id,local_rom_path,archive_member) VALUES (?,?,?) ON CONFLICT(game_id)
                DO UPDATE SET local_rom_path=excluded.local_rom_path,archive_member=excluded.archive_member''',
                (game_id, str(source), info['archive_member']))
        if info['archive_member'] is not None:
            self.adopt_legacy_extracted(game_id)
        return game_id

    def adopt_legacy_extracted(self, game_id):
        """Fold a game registered earlier under the extracted ROM name (Game.nes) into its ZIP (Game.zip).

        Games (including ones listed from the MiSTer scan) with the same library/system/folder and file stem qualify; titles, images and
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
                self.db.execute('UPDATE OR IGNORE artworks SET game_id=? WHERE game_id=?', (game_id, old['id']))
                if new['match_status'] != 'confirmed' and old['match_status'] == 'confirmed':
                    self.db.execute("UPDATE games SET japanese_title=?,match_status='confirmed' WHERE id=?",
                                    (old['japanese_title'], game_id))
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

    def confirm(self, game_id, title):
        self.game(game_id)
        if not title.strip() or any(ord(c) < 32 for c in title):
            raise ValueError('Title must be nonempty and contain no control characters')
        with self.db:
            self.db.execute("UPDATE games SET japanese_title=?,match_status='confirmed' WHERE id=?",
                            (title, game_id))

    def unconfirm(self, game_id):
        """Drop a confirmation so the next match can propose titles again; candidates are kept."""
        game = self.game(game_id)
        has_candidates = self.db.execute('SELECT 1 FROM match_candidates WHERE game_id=? LIMIT 1',
                                         (game_id,)).fetchone() is not None
        with self.db:
            self.db.execute('UPDATE games SET japanese_title=NULL,match_status=? WHERE id=?',
                            ('review' if has_candidates else 'unmatched', game['id']))

    def update_confirmed_title(self, game_id, expected, title):
        """Replace a confirmed title only if it is still the one the user reviewed."""
        if not title.strip() or any(ord(c) < 32 for c in title):
            raise ValueError('Title must be nonempty and contain no control characters')
        with self.db:
            changed = self.db.execute("""UPDATE games SET japanese_title=? WHERE id=? AND match_status='confirmed'
                AND japanese_title IS ?""", (title, game_id, expected)).rowcount
        return bool(changed)

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
        with self.db:
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
        with self.db:
            self.db.execute('UPDATE games SET artwork_source=?,artwork_region=?,artwork_path=? WHERE id=?',
                            (row['source'], row['region'], row['local_path'], game_id))
