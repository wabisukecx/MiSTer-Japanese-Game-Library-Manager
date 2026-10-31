"""Conflict-aware reverse publication, used automatically and from saved journals."""
import ftplib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from systems import SYSTEMS, transfer_rom
from .ftp_client import validate_path


def remote_hash(ftp, path):
    sha = hashlib.sha256()
    ftp.retrbinary('RETR ' + path, sha.update)
    return sha.hexdigest()


def save_journal(report, journal):
    report = Path(report)
    temp = report.with_suffix('.json.tmp')
    temp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(report)


def pending_recovery_reports(folder):
    """Find unfinished writes or failed rollback, excluding successful transfers."""
    reports = []
    for report in sorted(Path(folder).glob('*/transfer.json')):
        try:
            journal = json.loads(report.read_text(encoding='utf-8'))
            if not isinstance(journal, dict):
                continue
            validate_journal(journal)
            if journal.get('status') in ('verified', 'rolled_back'):
                continue
            if journal.get('status') == 'rollback_failed' or any(
                    row['status'] in ('staging', 'moving_original', 'publishing', 'verified', 'restoring', 'restore_failed')
                    for row in journal['items']):
                reports.append(str(report.resolve()))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return reports


def validate_journal(journal):
    run_id = journal.get('run_id', '')
    if journal.get('schema') != 'mister-jp-transfer/v2' or not re.fullmatch('[0-9a-f]{32}', run_id):
        raise ValueError('この転送記録は復旧対応形式ではありません')
    seen = set()
    for row in journal['items']:
        target = validate_path(row['remote_path'])
        p = PurePosixPath(target)
        if target in seen:
            raise ValueError('復旧先が重複しています')
        seen.add(target)
        if row['kind'] == 'rom':
            transfer_rom(dict(system=row['system'], rom_path=target))
            if row['original_existed']:
                raise ValueError('既存ROMを置換する記録は復旧できません')
        elif row['kind'] == 'image':
            if str(p.parent) not in {m for s in SYSTEMS.values() for m in s.media_dirs} or p.suffix != '.png':
                raise ValueError('画像の復旧先が不正です')
            expected = str(p.parent) + '/.mister-jp-backup-' + run_id + '/' + p.name
            if row.get('remote_backup') != expected:
                raise ValueError('画像バックアップ先が不正です')
        elif row['kind'] == 'titles':
            if not target.startswith('/media/fat/ConsoleMode/') or p.suffix != '.txt':
                raise ValueError('別名の復旧先が不正です')
            if row.get('remote_backup') != target + '.mister-jp-' + run_id + '.bak':
                raise ValueError('別名バックアップ先が不正です')
        else:
            raise ValueError('復旧するファイル種別が不正です')
        for key in ('sha256', 'original_sha256'):
            if (key == 'sha256' or row['original_existed']) and not re.fullmatch('[0-9a-f]{64}', row.get(key, '')):
                raise ValueError('復旧用ハッシュが不正です')


def rollback(ftp, journal, save):
    """Restore originals in reverse order. Preserve unexpected remote changes.

    New published files are renamed to quarantine rather than deleted. The layout
    and hashes make retries safe even if a rename succeeded but its reply was lost.
    """
    validate_journal(journal)
    failed = False
    for row in reversed(journal['items']):
        if row['status'] in ('ready', 'backed_up', 'staging', 'restored'):
            continue
        try:
            target = row['remote_path']
            parent = str(PurePosixPath(target).parent)
            entries = dict(ftp.mlsd(parent))
            fact = entries.get(PurePosixPath(target).name)
            if fact and fact.get('type') != 'file':
                raise ValueError('復旧先は通常ファイルではありません')
            current = remote_hash(ftp, target) if fact else None
            if row['original_existed'] and current == row['original_sha256']:
                row['status'] = 'restored'
                save()
                continue
            if current is not None and current != row['sha256']:
                raise ValueError('転送後に実機のファイルが変更されています')
            if row['original_existed']:
                if remote_hash(ftp, row['remote_backup']) != row['original_sha256']:
                    raise ValueError('実機バックアップが一致しません')
            row['status'] = 'restoring'
            save()
            if current is not None:
                quarantine = target + '.mister-jp-rollback-' + journal['run_id']
                if PurePosixPath(quarantine).name in entries:
                    raise ValueError('退避先が既に存在します')
                row['rollback_copy'] = quarantine
                save()
                ftp.rename(target, quarantine)
            if row['original_existed']:
                ftp.rename(row['remote_backup'], target)
                if remote_hash(ftp, target) != row['original_sha256']:
                    raise ValueError('復旧後の照合に失敗しました')
            row['status'] = 'restored'
            row.pop('recovery_error', None)
            save()
        except Exception:
            failed = True
            row['status'] = 'restore_failed'
            row['recovery_error'] = '復旧未完了。接続・実機の変更・バックアップを確認してください'
            save()
    journal['status'] = 'rollback_failed' if failed else 'rolled_back'
    save()
    return not failed


def recover_transfer(report, host, username, password, *, ftp_factory=ftplib.FTP):
    report = Path(report)
    journal = json.loads(report.read_text(encoding='utf-8'))
    validate_journal(journal)
    if journal.get('host') != host:
        raise ValueError('記録と同じ実機のIPを指定してください')
    ftp = ftp_factory()
    try:
        ftp.encoding = 'utf-8'
        ftp.connect(host, 21, timeout=20)
        ftp.login(username, password)
        password = None
        ok = rollback(ftp, journal, lambda: save_journal(report, journal))
        return dict(status=journal['status'], recovered=ok, report=str(report.resolve()))
    except Exception:
        raise RuntimeError('復旧できませんでした。実機と転送記録を確認してください: ' + str(report.resolve())) from None
    finally:
        ftp.close()
