"""Reentrant asset.media_id -> asset_image_candidates backfill.

Writes are disabled unless --apply is supplied. Run with API and worker writes paused.
"""

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from short_drama.utils.snowflake import next_id


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main():
    args = arguments()
    if not args.database_url:
        raise SystemExit("--database-url or DATABASE_URL is required")
    if not 1 <= args.batch_size <= 5000:
        raise SystemExit("--batch-size must be between 1 and 5000")
    url = make_url(args.database_url)
    if url.drivername != "mysql+pymysql" or not url.database:
        raise SystemExit("database URL must be mysql+pymysql and name an explicit database")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    try:
        with engine.connect() as connection:
            bad = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM assets a LEFT JOIN media_files m ON m.id=a.media_id "
                    "WHERE a.media_id IS NOT NULL AND m.id IS NULL"
                )
            )
            if bad:
                raise SystemExit(f"stopped: {bad} assets reference missing media files")
            pending = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM assets a LEFT JOIN asset_image_candidates c "
                    "ON c.asset_id=a.id AND c.media_id=a.media_id "
                    "WHERE a.media_id IS NOT NULL AND c.id IS NULL"
                )
            )
        if not args.apply:
            print(f"dry-run: {pending} candidate rows require backfill")
            return 0
        inserted = 0
        while True:
            with engine.begin() as connection:
                rows = connection.execute(
                    text(
                        "SELECT a.id, a.media_id FROM assets a "
                        "LEFT JOIN asset_image_candidates c "
                        "ON c.asset_id=a.id AND c.media_id=a.media_id "
                        "WHERE a.media_id IS NOT NULL AND c.id IS NULL "
                        "ORDER BY a.id LIMIT :limit FOR UPDATE"
                    ),
                    {"limit": args.batch_size},
                ).all()
                if not rows:
                    break
                for asset_id, media_id in rows:
                    connection.execute(
                        text(
                            "INSERT INTO asset_image_candidates (id,asset_id,media_id) "
                            "VALUES (:id,:asset_id,:media_id) "
                            "ON DUPLICATE KEY UPDATE id=id"
                        ),
                        {"id": next_id(), "asset_id": asset_id, "media_id": media_id},
                    )
                inserted += len(rows)
                print(f"processed {inserted}/{pending}")
        print(f"backfill complete: processed {inserted} rows")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
