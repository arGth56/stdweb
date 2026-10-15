"""
Copy Django auth account data from one SQLite database to another.

Does not copy tasks, sessions, or FITS data — only users, API tokens, and
user/group permission links.
"""
from django.core.management.base import BaseCommand, CommandError

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


USER_TABLES = (
    'auth_user',
    'auth_group',
    'auth_group_permissions',
    'auth_user_groups',
    'auth_user_user_permissions',
    'authtoken_token',
)


class Command(BaseCommand):
    help = 'Copy user account data between two stdweb SQLite databases'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source',
            required=True,
            help='Path to source db.sqlite3 (accounts copied from here)',
        )
        parser.add_argument(
            '--target',
            required=True,
            help='Path to target db.sqlite3 (accounts merged into here)',
        )
        parser.add_argument(
            '--no-backup',
            action='store_true',
            help='Do not create a timestamped backup of the target database',
        )
        parser.add_argument(
            '--passwords-only',
            action='store_true',
            help=(
                'Only copy login credentials and profile flags for matching usernames; '
                'insert missing users. Does not replace API tokens or group membership.'
            ),
        )

    def handle(self, *args, **options):
        source = Path(options['source']).resolve()
        target = Path(options['target']).resolve()

        if source == target:
            raise CommandError('Source and target must be different files')
        if not source.is_file():
            raise CommandError(f'Source not found: {source}')
        if not target.is_file():
            raise CommandError(f'Target not found: {target}')

        if not options['no_backup']:
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = target.with_suffix(f'.sqlite3.bak-{stamp}')
            shutil.copy2(target, backup)
            self.stdout.write(f'Target backup: {backup}')

        src = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
        src.row_factory = sqlite3.Row
        dst = sqlite3.connect(target)
        dst.row_factory = sqlite3.Row

        try:
            if options['passwords_only']:
                stats = merge_passwords_only(src, dst)
            else:
                stats = merge_accounts(src, dst)
        finally:
            src.close()
            dst.close()

        msg = (
            f"Done — users updated {stats['users_updated']}, "
            f"inserted {stats['users_inserted']}"
        )
        if not options['passwords_only']:
            msg += (
                f", tokens {stats['tokens_upserted']}, "
                f"group links {stats['group_links']}"
            )
        self.stdout.write(self.style.SUCCESS(msg))


def merge_passwords_only(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict:
    """Align web login passwords (and account flags) with source; keep target tokens/tasks."""
    stats = {'users_updated': 0, 'users_inserted': 0, 'tokens_upserted': 0, 'group_links': 0}

    user_cols = [r[1] for r in src.execute('PRAGMA table_info(auth_user)')]
    if user_cols != [r[1] for r in dst.execute('PRAGMA table_info(auth_user)')]:
        raise CommandError('auth_user schema differs between source and target')

    username_to_id = {
        row['username']: row['id']
        for row in dst.execute('SELECT id, username FROM auth_user')
    }

    for row in src.execute('SELECT * FROM auth_user ORDER BY id'):
        data = {col: row[col] for col in user_cols}
        username = data['username']
        if username in username_to_id:
            dst.execute(
                """UPDATE auth_user SET
                    password=:password, is_superuser=:is_superuser,
                    last_name=:last_name, email=:email, is_staff=:is_staff,
                    is_active=:is_active, first_name=:first_name
                    WHERE id=:id""",
                {
                    'password': data['password'],
                    'is_superuser': data['is_superuser'],
                    'last_name': data['last_name'],
                    'email': data['email'],
                    'is_staff': data['is_staff'],
                    'is_active': data['is_active'],
                    'first_name': data['first_name'],
                    'id': username_to_id[username],
                },
            )
            stats['users_updated'] += 1
        else:
            max_id = dst.execute('SELECT MAX(id) FROM auth_user').fetchone()[0] or 0
            data['id'] = max_id + 1
            cols = ', '.join(user_cols)
            placeholders = ', '.join(f':{c}' for c in user_cols)
            dst.execute(f'INSERT INTO auth_user ({cols}) VALUES ({placeholders})', data)
            username_to_id[username] = data['id']
            stats['users_inserted'] += 1

    max_uid = dst.execute('SELECT MAX(id) FROM auth_user').fetchone()[0] or 0
    dst.execute("UPDATE sqlite_sequence SET seq=? WHERE name='auth_user'", (max_uid,))
    if dst.execute('SELECT changes()').fetchone()[0] == 0:
        dst.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('auth_user', ?)",
            (max_uid,),
        )

    dst.commit()
    return stats


def merge_accounts(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict:
    stats = {
        'users_updated': 0,
        'users_inserted': 0,
        'tokens_upserted': 0,
        'group_links': 0,
    }

    user_cols = [r[1] for r in src.execute('PRAGMA table_info(auth_user)')]
    if user_cols != [r[1] for r in dst.execute('PRAGMA table_info(auth_user)')]:
        raise CommandError('auth_user schema differs between source and target')

    dst.execute('PRAGMA foreign_keys = OFF')

    # Groups and permissions (small; replace from source)
    for table in ('auth_group_permissions', 'auth_user_groups', 'auth_user_user_permissions'):
        if _table_exists(src, table):
            dst.execute(f'DELETE FROM {table}')
    if _table_exists(src, 'auth_group'):
        dst.execute('DELETE FROM auth_group')
        for row in src.execute('SELECT * FROM auth_group'):
            dst.execute(
                'INSERT INTO auth_group (id, name) VALUES (?, ?)',
                (row['id'], row['name']),
            )
    if _table_exists(src, 'auth_group_permissions'):
        for row in src.execute('SELECT * FROM auth_group_permissions'):
            dst.execute(
                'INSERT INTO auth_group_permissions (id, group_id, permission_id) VALUES (?, ?, ?)',
                (row['id'], row['group_id'], row['permission_id']),
            )

    username_to_id = {
        row['username']: row['id']
        for row in dst.execute('SELECT id, username FROM auth_user')
    }

    for row in src.execute('SELECT * FROM auth_user ORDER BY id'):
        data = {col: row[col] for col in user_cols}
        username = data['username']
        if username in username_to_id:
            uid = username_to_id[username]
            dst.execute(
                f"""UPDATE auth_user SET
                    password=:password, last_login=:last_login, is_superuser=:is_superuser,
                    last_name=:last_name, email=:email, is_staff=:is_staff,
                    is_active=:is_active, date_joined=:date_joined, first_name=:first_name
                    WHERE id=:id""",
                {**data, 'id': uid},
            )
            stats['users_updated'] += 1
        else:
            cols = ', '.join(user_cols)
            placeholders = ', '.join(f':{c}' for c in user_cols)
            dst.execute(f'INSERT INTO auth_user ({cols}) VALUES ({placeholders})', data)
            username_to_id[username] = data['id']
            stats['users_inserted'] += 1

    max_uid = dst.execute('SELECT MAX(id) FROM auth_user').fetchone()[0] or 0
    dst.execute(
        "UPDATE sqlite_sequence SET seq=? WHERE name='auth_user'",
        (max_uid,),
    )
    if dst.execute("SELECT changes()").fetchone()[0] == 0:
        dst.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('auth_user', ?)",
            (max_uid,),
        )

    # User ↔ group / permission links (source user ids)
    if _table_exists(src, 'auth_user_groups'):
        for row in src.execute('SELECT * FROM auth_user_groups'):
            dst.execute(
                'INSERT INTO auth_user_groups (id, user_id, group_id) VALUES (?, ?, ?)',
                (row['id'], row['user_id'], row['group_id']),
            )
            stats['group_links'] += 1
    if _table_exists(src, 'auth_user_user_permissions'):
        for row in src.execute('SELECT * FROM auth_user_user_permissions'):
            dst.execute(
                'INSERT INTO auth_user_user_permissions (id, user_id, permission_id) VALUES (?, ?, ?)',
                (row['id'], row['user_id'], row['permission_id']),
            )

    if _table_exists(src, 'authtoken_token'):
        dst.execute('DELETE FROM authtoken_token')
        for row in src.execute('SELECT user_id, key, created FROM authtoken_token'):
            if row['user_id'] not in {r[0] for r in dst.execute('SELECT id FROM auth_user')}:
                continue
            dst.execute(
                'INSERT INTO authtoken_token (key, created, user_id) VALUES (?, ?, ?)',
                (row['key'], row['created'], row['user_id']),
            )
            stats['tokens_upserted'] += 1

    dst.execute('PRAGMA foreign_keys = ON')
    dst.commit()
    return stats


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None
