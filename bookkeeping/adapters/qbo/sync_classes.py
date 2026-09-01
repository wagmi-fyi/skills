#!/usr/bin/env python3
"""
Sync Classes from QuickBooks Online
Pulls class list from QBO and upserts to local tags table with category='Class'.

Usage:
    BOOKKEEPING_CONFIG_PATH=_local-bookkeeping/config.yaml \
      {python} {module_root}/adapters/qbo/sync_classes.py [--dry_run]
"""

import argparse
import json
import sqlite3
import sys
import os
from typing import Dict, List, Optional, Tuple

try:
    from quickbooks.objects import Class as QBOClass
    QBO_IMPORTS_AVAILABLE = True
except ImportError:
    QBO_IMPORTS_AVAILABLE = False

# Bootstrap config
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(script_dir, '..', '..', 'scripts', '_shared'))
sys.path.insert(0, script_dir)
import config_loader

from _shared.client import (
    validate_qbo_env_vars, create_qbo_client, save_tokens_if_available,
    fetch_all_pages
)

from dotenv import load_dotenv

_config = config_loader.load_config()
ENV_PATH = os.path.join(_config['local_dir'], 'adapters', '.env')
load_dotenv(ENV_PATH)


def parse_arguments():
    parser = argparse.ArgumentParser(description='Sync Classes from QuickBooks Online')
    parser.add_argument('--dry_run', action='store_true', help='Show what would be synced without making changes')
    return parser.parse_args()


def fetch_classes_from_qbo(client) -> Tuple[List[Dict], Optional[str]]:
    """Fetch all classes from QBO, paginated (.all() alone caps at MAXRESULTS 100)."""
    try:
        classes = fetch_all_pages(QBOClass, client)
        save_tokens_if_available(client, ENV_PATH)

        result = []
        for cls in classes:
            parent_ref = getattr(cls, 'ParentRef', None)
            parent_id = parent_ref.value if parent_ref else None

            result.append({
                'name': cls.Name,
                'remote_id': cls.Id,
                'parent_remote_id': parent_id,
                'fully_qualified_name': getattr(cls, 'FullyQualifiedName', cls.Name),
                'active': getattr(cls, 'Active', True),
            })

        return result, None

    except Exception as e:
        return [], f"Failed to fetch classes: {str(e)}"


def upsert_classes(conn: sqlite3.Connection, classes: List[Dict], dry_run: bool) -> Dict:
    """Upsert classes to tags table with category='Class'."""
    cursor = conn.cursor()

    inserted = 0
    updated = 0
    skipped = 0
    details = []

    for cls in classes:
        if not cls.get('active', True):
            skipped += 1
            continue

        cursor.execute(
            "SELECT name, category FROM tags WHERE remote_id = ? AND category = 'Class'",
            (cls['remote_id'],)
        )
        existing_by_remote = cursor.fetchone()

        cursor.execute(
            "SELECT name, remote_id FROM tags WHERE name = ? AND category = 'Class'",
            (cls['name'],)
        )
        existing_by_name = cursor.fetchone()

        if existing_by_remote:
            if not dry_run:
                cursor.execute(
                    "UPDATE tags SET name = ? WHERE remote_id = ? AND category = 'Class'",
                    (cls['name'], cls['remote_id'])
                )
            updated += 1
            details.append({'action': 'update', 'name': cls['name'], 'remote_id': cls['remote_id'], 'fully_qualified_name': cls['fully_qualified_name']})

        elif existing_by_name:
            if not dry_run:
                cursor.execute(
                    "UPDATE tags SET remote_id = ? WHERE name = ? AND category = 'Class'",
                    (cls['remote_id'], cls['name'])
                )
            updated += 1
            details.append({'action': 'update', 'name': cls['name'], 'remote_id': cls['remote_id'], 'note': 'added remote_id to existing name'})

        else:
            if not dry_run:
                cursor.execute(
                    "INSERT INTO tags (name, category, remote_id) VALUES (?, 'Class', ?)",
                    (cls['name'], cls['remote_id'])
                )
            inserted += 1
            details.append({'action': 'insert', 'name': cls['name'], 'remote_id': cls['remote_id'], 'fully_qualified_name': cls['fully_qualified_name']})

    if not dry_run:
        conn.commit()

    return {'inserted': inserted, 'updated': updated, 'skipped': skipped, 'details': details}


def main():
    try:
        args = parse_arguments()
        credentials = validate_qbo_env_vars()

        client, client_error = create_qbo_client(credentials)
        if client_error:
            print(json.dumps({"success": False, "error": client_error}, indent=2))
            sys.exit(1)

        classes, fetch_error = fetch_classes_from_qbo(client)
        if fetch_error:
            print(json.dumps({"success": False, "error": fetch_error}, indent=2))
            sys.exit(1)

        conn = sqlite3.connect(config_loader.get_db_path())
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            result = upsert_classes(conn, classes, args.dry_run)
            output = {
                "success": True,
                "dry_run": args.dry_run,
                "classes_fetched": len(classes),
                "inserted": result['inserted'],
                "updated": result['updated'],
                "skipped": result['skipped'],
            }
            if args.dry_run or len(classes) <= 20:
                output['details'] = result['details']

            print(json.dumps(output, indent=2))
            sys.exit(0)
        finally:
            conn.close()

    except ValueError as e:
        print(json.dumps({"success": False, "error": str(e)}, indent=2))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": f"Unexpected error: {str(e)}"}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
