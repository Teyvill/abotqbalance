"""Database helpers for the ABOT lore tracker.

A single SQLite file holds all data. The schema is created on first run and
the fixed list of lore stats is seeded once.
"""

import os
import sqlite3

# Database location. In production (e.g. Render) point DATABASE_PATH at a
# persistent disk so data survives restarts; locally it defaults to a file
# next to the code.
DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "abot.db"
)

# Fixed lore stats in their canonical display order. Seeded once and never
# edited from the UI.
LORE_STATS = [
    "Courageous",
    "Cowardly",
    "Cunning",
    "Honest",
    "Generous",
    "Greedy",
    "Hottempered",
    "Patient",
    "Charismatic",
    "Infamous",
    "Smart",
    "Foolish",
]

# Each skill's usage is tracked separately per method.
METHODS = ("roll", "option")

# Location building blocks. A location's code is composed from these; a
# "Capital" at the sector level is a continent capital, and at the settlement
# level a sector capital.
CONTINENTS = ["Fo", "Mo", "Il"]
SECTORS = ["Sector1", "Sector2", "Sector3"]
SETTLEMENTS = ["Settlement1", "Settlement2"]

# Bosses are either settled in a place or roaming nomads.
BOSS_TYPES = ["Settled", "Nomad"]


def seed_location_codes():
    """Return the full set of seeded locations as (code, continent, sector,
    settlement) tuples: continent capitals, sector capitals, and settlements."""
    rows = []
    for continent in CONTINENTS:
        # Continent capital, e.g. FoCapital.
        rows.append((continent + "Capital", continent, None, None))
        for sector in SECTORS:
            # Sector capital, e.g. FoSector1Capital.
            rows.append((continent + sector + "Capital", continent, sector, None))
            for settlement in SETTLEMENTS:
                # Settlement, e.g. FoSector1Settlement1.
                rows.append(
                    (continent + sector + settlement, continent, sector, settlement)
                )
    return rows


def get_db():
    """Open a connection with row access by column name and foreign keys on."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if needed and seed the fixed lore stats."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    # Surface the resolved path so deploy logs show where data is written.
    # On hosts like Render the code directory is read-only at runtime, so
    # DATABASE_PATH must point at a writable persistent disk.
    print(f"[abot] Using database at: {DB_PATH}", flush=True)
    try:
        conn = get_db()
    except sqlite3.OperationalError as exc:
        raise sqlite3.OperationalError(
            f"Cannot open database at {DB_PATH!r}: {exc}. "
            "Set DATABASE_PATH to a writable location (e.g. a mounted disk)."
        ) from exc
    # WAL improves concurrency when gunicorn serves with multiple threads.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lore_stats (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS bosses (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT
        );

        CREATE TABLE IF NOT EXISTS locations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            code       TEXT NOT NULL UNIQUE,
            name       TEXT,
            continent  TEXT,
            sector     TEXT,
            settlement TEXT
        );

        CREATE TABLE IF NOT EXISTS branches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            type        TEXT NOT NULL CHECK (type IN ('personal', 'location')),
            boss_id     INTEGER REFERENCES bosses(id) ON DELETE CASCADE,
            location_id INTEGER REFERENCES locations(id) ON DELETE CASCADE,
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS stat_usage (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
            stat_id   INTEGER NOT NULL REFERENCES lore_stats(id) ON DELETE CASCADE,
            method    TEXT NOT NULL CHECK (method IN ('roll', 'option')),
            count     INTEGER NOT NULL DEFAULT 0,
            UNIQUE (branch_id, stat_id, method)
        );

        CREATE TABLE IF NOT EXISTS statuses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            description TEXT
        );

        CREATE TABLE IF NOT EXISTS status_branch_acquire (
            status_id INTEGER NOT NULL REFERENCES statuses(id) ON DELETE CASCADE,
            branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
            PRIMARY KEY (status_id, branch_id)
        );

        CREATE TABLE IF NOT EXISTS status_usage (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
            status_id INTEGER NOT NULL REFERENCES statuses(id) ON DELETE CASCADE,
            count     INTEGER NOT NULL DEFAULT 0,
            UNIQUE (branch_id, status_id)
        );
        """
    )

    # Migration: older databases have a stat_usage table without the `method`
    # column. Recreate it so roll/option tracking works. Existing stat counts
    # are intentionally dropped (the schema can't tell roll from option).
    stat_cols = [r["name"] for r in conn.execute("PRAGMA table_info(stat_usage)")]
    if stat_cols and "method" not in stat_cols:
        conn.execute("DROP TABLE stat_usage")
        conn.execute(
            """
            CREATE TABLE stat_usage (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
                stat_id   INTEGER NOT NULL REFERENCES lore_stats(id) ON DELETE CASCADE,
                method    TEXT NOT NULL CHECK (method IN ('roll', 'option')),
                count     INTEGER NOT NULL DEFAULT 0,
                UNIQUE (branch_id, stat_id, method)
            )
            """
        )

    # Migration: add the boss `type` column to older databases.
    boss_cols = [r["name"] for r in conn.execute("PRAGMA table_info(bosses)")]
    if boss_cols and "type" not in boss_cols:
        conn.execute("ALTER TABLE bosses ADD COLUMN type TEXT")

    # Migration: add the structured location columns to older databases.
    loc_cols = [r["name"] for r in conn.execute("PRAGMA table_info(locations)")]
    if loc_cols:
        for col in ("code", "continent", "sector", "settlement"):
            if col not in loc_cols:
                conn.execute(f"ALTER TABLE locations ADD COLUMN {col} TEXT")

    # Seed the fixed lore stats once, preserving their canonical order.
    # INSERT OR IGNORE keeps this idempotent even if init runs concurrently.
    existing = conn.execute("SELECT COUNT(*) AS n FROM lore_stats").fetchone()["n"]
    if existing == 0:
        for name in LORE_STATS:
            conn.execute("INSERT OR IGNORE INTO lore_stats (name) VALUES (?)", (name,))

    # Seed the full location set once, into an empty locations table. This
    # covers every continent capital, sector capital, and settlement.
    loc_count = conn.execute("SELECT COUNT(*) AS n FROM locations").fetchone()["n"]
    if loc_count == 0:
        for code, continent, sector, settlement in seed_location_codes():
            conn.execute(
                "INSERT INTO locations (code, continent, sector, settlement) "
                "VALUES (?, ?, ?, ?)",
                (code, continent, sector, settlement),
            )
        # One named capital to start with.
        conn.execute(
            "UPDATE locations SET name = ? WHERE code = ?",
            ("Lindenmoor", "FoSector1Capital"),
        )

    conn.commit()
    conn.close()
