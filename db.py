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

# ---- Book of Tales enums (shared with the book blueprint) ----
DECISION_TYPES = ["base", "advanced", "minor"]
BAM_DIRECTIONS = ["benevolent", "ambitious", "malevolent"]
WEIGHT_TIERS = ["light", "heavy"]
QUEST_TIERS = ["pretender", "count", "duke", "king"]
WORLD_SLOTS = ["setup", "ripple", "coda"]
PARTY_STATES = [
    "won_rule_together",
    "won_one_king",
    "won_fought_throne",
    "won_refused",
    "lost_serve_king",
    "lost_died",
    "never_met_king",
]
# Per-quest decision limits and per-decision option limits (app invariants).
MAX_ADVANCED = 1
MAX_MINOR = 3
MIN_OPTIONS = 2
MAX_OPTIONS = 5

# Seeded default for the fixed closing sentence (editable in globals).
DEFAULT_LAST_SENTENCE = (
    "And so the tale was told, as all tales are, by those who lived to tell it."
)


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


# The special World Pillar location, seeded alongside the scheme locations.
WORLD_CODE = "World"
WORLD_NAME = "World Pillar"

# Codes of all seeded locations. These are protected: they cannot be deleted.
SEEDED_LOCATION_CODES = frozenset(
    [code for code, _c, _s, _st in seed_location_codes()] + [WORLD_CODE]
)


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

        -- ----------------------------------------------------------------
        -- Book of Tales editor. All tables are namespaced with book_ and
        -- reference the existing branches table for quest branch pairing.
        -- ----------------------------------------------------------------

        -- A quest pairs one personal branch and one locational branch (both
        -- from the existing branches table used for lore-stat tracking).
        CREATE TABLE IF NOT EXISTS book_quests (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            name                   TEXT NOT NULL,
            personal_branch_id     INTEGER REFERENCES branches(id) ON DELETE SET NULL,
            locational_branch_id   INTEGER REFERENCES branches(id) ON DELETE SET NULL,
            first_sentence         TEXT NOT NULL DEFAULT '',
            last_sentence_override TEXT,
            tier                   TEXT,   -- pretender/count/duke/king
            external_ref           TEXT,   -- StoryFlow file/project id
            created_at             TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at             TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (personal_branch_id, locational_branch_id)
        );

        -- Substitution variables per quest: {loc}, {giant}, {npc}, {tree}...
        CREATE TABLE IF NOT EXISTS book_quest_vars (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_id INTEGER NOT NULL REFERENCES book_quests(id) ON DELETE CASCADE,
            key      TEXT NOT NULL,
            value    TEXT NOT NULL,
            UNIQUE (quest_id, key)
        );

        -- A question. App invariant: exactly 1 base, 0..1 advanced, 0..3 minor.
        CREATE TABLE IF NOT EXISTS book_decisions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_id   INTEGER NOT NULL REFERENCES book_quests(id) ON DELETE CASCADE,
            type       TEXT NOT NULL CHECK (type IN ('base', 'advanced', 'minor')),
            question   TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );

        -- An answer option. App invariant: 2..5 options per decision.
        CREATE TABLE IF NOT EXISTS book_options (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id   INTEGER NOT NULL REFERENCES book_decisions(id) ON DELETE CASCADE,
            label         TEXT NOT NULL,
            bam_direction TEXT CHECK (bam_direction IN ('benevolent', 'ambitious', 'malevolent')),
            sort_order    INTEGER DEFAULT 0
        );

        -- A consequence sentence attached to an option.
        CREATE TABLE IF NOT EXISTS book_fragments (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            option_id               INTEGER NOT NULL REFERENCES book_options(id) ON DELETE CASCADE,
            text                    TEXT NOT NULL,
            weight_tier             TEXT CHECK (weight_tier IN ('light', 'heavy')),
            modifies_base_option_id INTEGER REFERENCES book_options(id) ON DELETE CASCADE,
            party_state             TEXT,
            sort_order              INTEGER DEFAULT 0
        );

        -- Global: party ending (panel 1).
        CREATE TABLE IF NOT EXISTS book_party_endings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            state         TEXT NOT NULL CHECK (state IN (
                              'won_rule_together', 'won_one_king', 'won_fought_throne',
                              'won_refused', 'lost_serve_king', 'lost_died',
                              'never_met_king')),
            bam_direction TEXT,
            text          TEXT NOT NULL,
            sort_order    INTEGER DEFAULT 0
        );

        -- Global: world fragments (panel 5), assembled from run aggregates.
        CREATE TABLE IF NOT EXISTS book_world_fragments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            slot            TEXT NOT NULL CHECK (slot IN ('setup', 'ripple', 'coda')),
            condition_key   TEXT NOT NULL,
            condition_value TEXT NOT NULL,
            text            TEXT NOT NULL,
            sort_order      INTEGER DEFAULT 0
        );

        -- Global key/value store (e.g. default_last_sentence).
        CREATE TABLE IF NOT EXISTS book_globals (
            key   TEXT PRIMARY KEY,
            value TEXT
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

    # Migration: rebuild a legacy locations table. Old databases have a
    # locations table with `name NOT NULL` and no `code` column; ALTER TABLE
    # cannot drop the NOT NULL, so the seed (which has no name) would fail.
    # Rebuild with the current schema. Legacy rows are test data with no code
    # in the new model, so they are dropped (cascading to their branches).
    loc_info = conn.execute("PRAGMA table_info(locations)").fetchall()
    if loc_info:
        col_names = [r["name"] for r in loc_info]
        name_not_null = any(r["name"] == "name" and r["notnull"] for r in loc_info)
        if "code" not in col_names or name_not_null:
            conn.execute("DROP TABLE locations")
            conn.execute(
                """
                CREATE TABLE locations (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    code       TEXT NOT NULL UNIQUE,
                    name       TEXT,
                    continent  TEXT,
                    sector     TEXT,
                    settlement TEXT
                )
                """
            )

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

    # The World Pillar is a special location with the fixed code "World" (no
    # continent/sector/settlement). Added idempotently so it appears in both
    # new and already-seeded databases.
    conn.execute(
        "INSERT OR IGNORE INTO locations (code, name) VALUES (?, ?)",
        (WORLD_CODE, WORLD_NAME),
    )

    # Seed the default closing sentence for the Book of Tales (editable later).
    conn.execute(
        "INSERT OR IGNORE INTO book_globals (key, value) VALUES (?, ?)",
        ("default_last_sentence", DEFAULT_LAST_SENTENCE),
    )

    conn.commit()
    conn.close()
