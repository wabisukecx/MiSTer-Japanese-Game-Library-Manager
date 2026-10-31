"""Library CLI, including explicit journal recovery and optional GUI entry."""
import argparse
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path

from artwork.cache import ImageCache
from artwork.libretro import artwork_url
from artwork.screenscraper import Credentials, ScreenScraper
from consolemode.export import export_consolemode, review_manifest
from database.store import Store
from metadata.matcher import candidates, regions
from mister.ftp_client import FTPReader, validate_path
from systems import SYSTEMS, system_profile


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Refuse silent overwrites, including outputs produced by an earlier scan.
    with target.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def screenscraper_credentials(no_prompt=False):
    values = []
    for key, label, secret in (
            ('DEVID', 'ScreenScraper developer ID', False),
            ('DEVPASSWORD', 'ScreenScraper developer password', True),
            ('SSID', 'ScreenScraper member username', False),
            ('SSPASSWORD', 'ScreenScraper member password', True)):
        value = os.environ.get('SCREENSCRAPER_' + key, '')
        if not value and not no_prompt:
            value = getpass.getpass(label + ' (not saved): ') if secret else input(label + ': ')
        values.append(value)
    return Credentials(*values)


def parser():
    cli = argparse.ArgumentParser(description='MiSTer Japanese metadata manager (FTP scan and explicit recovery)')
    cli.add_argument('--db', default='work/library.sqlite3', help='local SQLite database')
    commands = cli.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('scan', help='list ROMs via FTP; never download ROMs')
    scan.add_argument('--host', required=True)
    scan.add_argument('--user', default='root')
    scan.add_argument('--port', type=int, default=21)
    scan.add_argument('--timeout', type=float, default=15)
    scan.add_argument('--system', choices=SYSTEMS, default='NES')
    scan.add_argument('--root', help='defaults to the selected system ROM folder')
    scan.add_argument('--recursive', action='store_true')
    scan.add_argument('--out', help='optional inventory JSON (must not already exist)')
    conn = commands.add_parser('connection-test', help='login and list one FTP directory')
    conn.add_argument('--host', required=True)
    conn.add_argument('--user', default='root')
    conn.add_argument('--port', type=int, default=21)
    conn.add_argument('--timeout', type=float, default=15)
    conn.add_argument('--root', default='/media/fat/games/NES')
    inventory = commands.add_parser('import-inventory', help='import local/mock inventory JSON')
    inventory.add_argument('path')
    aliases = commands.add_parser('import-aliases', help='import attributed title aliases JSON')
    aliases.add_argument('path')
    match = commands.add_parser('match', help='persist automatic candidates without confirming')
    listing = commands.add_parser('list', help='show titles and candidate evidence')
    listing.add_argument('--pending', action='store_true')
    confirm = commands.add_parser('confirm', help='explicitly confirm a Japanese title locally')
    confirm.add_argument('game_id', type=int)
    confirm.add_argument('title')
    unconfirm = commands.add_parser('unconfirm', help='drop a confirmed title so it can be matched again')
    unconfirm.add_argument('game_id', type=int)
    drop = commands.add_parser('delete-aliases', help='delete imported aliases from one source')
    drop.add_argument('source')
    art = commands.add_parser('artwork-url', help='preview exact Libretro catalog URL; no network')
    art.add_argument('catalog_title')
    art.add_argument('--system', choices=SYSTEMS, default='NES')
    download = commands.add_parser('artwork-download', help='download one explicitly selected Libretro image')
    download.add_argument('game_id', type=int)
    download.add_argument('catalog_title', help='exact Libretro title, including region tags')
    download.add_argument('--cache', default='work/artwork')
    download.add_argument('--replace', action='store_true', help='replace local artwork selection')
    search = commands.add_parser('screenscraper-search', help='find game candidates using the official API')
    search.add_argument('game_id', type=int, help='local game ID from list')
    search.add_argument('--refresh', action='store_true', help='refresh cached successful search')
    api_download = commands.add_parser('screenscraper-download', help='download a selected provider ID via official API')
    api_download.add_argument('game_id', type=int, help='local game ID from list')
    api_download.add_argument('--provider-game-id', type=int, required=True, help='ID from screenscraper-search')
    api_download.add_argument('--region', choices=('jp', 'us', 'eu', 'wor'), default='jp')
    api_download.add_argument('--select', action='store_true', help='select the downloaded candidate locally')
    api_download.add_argument('--replace', action='store_true', help='allow replacing the local artwork selection')
    for command in (search, api_download):
        command.add_argument('--cache', default='work/artwork')
        command.add_argument('--timeout', type=float, default=20)
        command.add_argument('--no-prompt', action='store_true', help='require credentials from environment')
    plan = commands.add_parser('preview', help='write blocked review manifest; not a transfer plan')
    plan.add_argument('--out', required=True)
    commands.add_parser('export', help='report why Console Mode export is blocked')
    titles = commands.add_parser('export-title-db', help='write a portable attributed Japanese title database JSON')
    titles.add_argument('--out', required=True, help='new output JSON; existing files are never overwritten')
    titles.add_argument('--system', choices=SYSTEMS)
    titles.add_argument('--confirmed-only', action='store_true')
    titles.add_argument('--hash-roms', action='store_true', help='read registered local ROMs to compute SHA-256')
    merge_titles = commands.add_parser('merge-title-db', help='merge title database JSONs offline without confirming titles')
    merge_titles.add_argument('paths', nargs='+')
    merge_titles.add_argument('--out', required=True)
    commands.add_parser('gui', help='open the local PyQt6 library and Skraper image importer')
    register = commands.add_parser('register-roms', help='register a PC folder recursively using the shared workflow')
    register.add_argument('folder')
    register.add_argument('--system', choices=SYSTEMS, required=True)
    register.add_argument('--root', help='defaults to the selected system ROM folder')
    register.add_argument('--lookup', action='store_true', help='also look up Japanese titles online')
    recovery = commands.add_parser('recover-transfer', help='restore a v2 batch from its transfer.json')
    recovery.add_argument('report')
    recovery.add_argument('--host', required=True)
    recovery.add_argument('--user', default='root')
    return cli


def execute(args, db):
    if args.command == 'export-title-db':
        from metadata.title_catalog import export_catalog, write_catalog
        games = [g for g in db.games() if (not args.system or g['system'] == args.system)
                 and (not args.confirmed_only or g['match_status'] == 'confirmed')]
        emit(write_catalog(args.out, export_catalog(db, games, hash_roms=args.hash_roms)))
    elif args.command == 'register-roms':
        from workflow import register_folder
        emit(register_folder(db, args.folder, args.system,
            args.root or system_profile(args.system).root,
            Path(args.db).parent / 'metadata' / 'retronian', lookup=args.lookup))
    elif args.command in ('scan', 'connection-test'):
        if args.command == 'scan':
            profile = system_profile(args.system)
            args.root = args.root or profile.root
        validate_path(args.root)
        if args.timeout <= 0 or not 1 <= args.port <= 65535:
            raise ValueError('Timeout must be positive; port must be 1..65535')
        if getattr(args, 'out', None) and Path(args.out).exists():
            raise ValueError('Output already exists; choose a new path')
        password = getpass.getpass('FTP password (not saved): ')
        with FTPReader(args.host, args.user, password, args.port, args.timeout) as client:
            password = None
            if args.command == 'connection-test':
                emit(dict(connected=True, entries=client.list_directory(args.root)))
                return
            inventory = client.scan(args.root, recursive=args.recursive, extensions=profile.extensions)
        inventory['system'] = profile.id
        for path in inventory['roms']:
            db.upsert_game(path, system=profile.id)
        if args.out:
            write_new_json(args.out, inventory)
        emit(inventory)
    elif args.command == 'import-inventory':
        inventory = read_json(args.path)
        profile = system_profile(inventory.get('system', 'NES'))
        paths = inventory['roms']
        if not isinstance(paths, list):
            raise ValueError('roms must be a list of absolute paths')
        for path in paths:
            validate_path(path)
            if Path(path).suffix.lower() not in profile.extensions:
                raise ValueError(profile.id + ' inventory accepts only ' + ', '.join(profile.extensions))
        for path in paths:
            db.upsert_game(path, system=profile.id)
        emit(dict(imported=len(paths), system=profile.id, network_access=False))
    elif args.command == 'import-aliases':
        items = read_json(args.path)
        db.import_aliases(items)
        emit(dict(imported=len(items)))
    elif args.command == 'match':
        aliases = db.aliases()
        for game in db.games():
            if game['match_status'] != 'confirmed':
                db.save_candidates(game['id'], candidates(game['rom_filename'], game['system'], aliases))
        emit(dict(processed=len(db.games()), automatic_confirmation=False))
    elif args.command == 'list':
        games = db.games(pending=args.pending)
        for game in games:
            game['candidates'] = db.get_candidates(game['id'])
        emit(games)
    elif args.command == 'confirm':
        db.confirm(args.game_id, args.title)
        emit(db.game(args.game_id))
    elif args.command == 'unconfirm':
        db.unconfirm(args.game_id)
        emit(db.game(args.game_id))
    elif args.command == 'delete-aliases':
        emit(dict(deleted=db.delete_aliases(args.source)))
    elif args.command == 'artwork-url':
        emit(dict(source='libretro', url=artwork_url(args.catalog_title, args.system),
                  catalog_regions=regions(args.catalog_title),
                  note='URL candidate only; existence, identity and image region require review'))
    elif args.command == 'artwork-download':
        game = db.game(args.game_id)
        if game['artwork_path'] and not args.replace:
            raise ValueError('Artwork already selected; use --replace to change it explicitly')
        url = artwork_url(args.catalog_title, game['system'])
        path = ImageCache(Path(args.cache)).get(url).resolve()
        # Catalog tags are provenance, not a claim that the image was visually verified.
        region = ','.join(regions(args.catalog_title)) or 'UNKNOWN'
        db.save_artwork(args.game_id, 'libretro', region, url, path,
                        hashlib.sha256(path.read_bytes()).hexdigest())
        emit(dict(local_preview=str(path), source='libretro', catalog_region=region,
                  note='Inspect the image before treating it as Japanese box art'))
    elif args.command in ('screenscraper-search', 'screenscraper-download'):
        game = db.game(args.game_id)
        profile = system_profile(game['system'])
        if args.command == 'screenscraper-download' and args.select and game['artwork_path'] and not args.replace:
            raise ValueError('Artwork already selected; use --replace to change it explicitly')
        credentials = screenscraper_credentials(args.no_prompt)
        client = ScreenScraper(credentials, args.cache, timeout=args.timeout)
        if args.command == 'screenscraper-search':
            # jeuRecherche name lookup needs no ROM hash, size or transfer.
            from metadata.matcher import normalize
            title = normalize(game['rom_filename'])
            emit(dict(game_id=game['id'], query=title, candidates=client.search(title, system_id=profile.screenscraper_id, refresh=args.refresh),
                      automatic_confirmation=False))
        else:
            artwork = client.download(args.provider_game_id, region=args.region, system_id=profile.screenscraper_id)
            db.save_artwork(game['id'], artwork['source'], artwork['region'], artwork['url'],
                            artwork['local_path'], artwork['sha256'], select=args.select)
            artwork['selected'] = args.select
            artwork['game_id'] = game['id']
            emit(artwork)
    elif args.command == 'preview':
        games = db.games()
        for game in games:
            game['candidates'] = db.get_candidates(game['id'])
        manifest = review_manifest(games)
        write_new_json(args.out, manifest)
        db.save_manifest(manifest)
        emit(manifest)
    elif args.command == 'export':
        export_consolemode(db.games())


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    args = parser().parse_args(argv)
    if args.command == 'merge-title-db':
        from metadata.title_catalog import read_catalog, merge_catalogs, write_catalog
        try:
            emit(write_catalog(args.out, merge_catalogs(read_catalog(p) for p in args.paths)))
            return 0
        except (ValueError, OSError, KeyError, TypeError) as error:
            print('Error: ' + str(error), file=sys.stderr)
            return 2
    if args.command == 'recover-transfer':
        from mister.recovery import recover_transfer
        try:
            result = recover_transfer(args.report, args.host, args.user,
                                      getpass.getpass('FTP password (not saved): '))
            emit(result)
            return 0 if result['recovered'] else 2
        except (RuntimeError, ValueError, OSError, KeyError, TypeError) as error:
            print('Error: ' + str(error), file=sys.stderr)
            return 2
    if args.command == 'gui':
        try:
            from gui.app import run
        except ImportError:
            print('GUI requires PyQt6. Install with: python -m pip install -e ".[gui]"', file=sys.stderr)
            return 2
        return run(args.db)
    db = None
    try:
        db = Store(args.db)
        execute(args, db)
        return 0
    except KeyboardInterrupt:
        print('Cancelled. No files were written to MiSTer.', file=sys.stderr)
        return 130
    except (RuntimeError, ValueError, OSError, KeyError, TypeError, EOFError) as error:
        print('Error: ' + str(error), file=sys.stderr)
        return 2
    finally:
        if db:
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())
