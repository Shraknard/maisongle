"""Drop the legacy Melo location columns from the ``saved_searches`` table.

The search perimeter is now stored INSEE-keyed in the ``selected_cities`` JSON
column. The old single-city columns (``city_id`` held a Melo ``/cities/<id>``
path, plus ``city_name``/``city_insee``/``zipcode``) are dead weight.

``Base.metadata.create_all`` never drops columns, so this one-off migration does
it. It is idempotent — ``DROP COLUMN IF EXISTS`` is a no-op on a clean table.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from app.database import engine

LEGACY_COLUMNS = ("city_id", "city_name", "city_insee", "zipcode")


def migrate():
    with engine.begin() as conn:
        for column in LEGACY_COLUMNS:
            conn.execute(text(f"ALTER TABLE saved_searches DROP COLUMN IF EXISTS {column}"))
            print(f"  - dropped saved_searches.{column} (if it existed)")
    print("Migration terminée.")


if __name__ == "__main__":
    migrate()
