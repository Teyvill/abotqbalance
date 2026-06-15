"""Database helpers for the ABOT lore tracker.

A single SQLite file holds all data. The schema is created on first run and
the fixed list of lore stats is seeded once.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abot.db")

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


def get_db():
    """Open a connection with row access by column name and foreign keys on."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if needed and seed the fixed lore stats."""
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lore_stats (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS bosses (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS locations (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
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
            count     INTEGER NOT NULL DEFAULT 0,
            UNIQUE (branch_id, stat_id)
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

    # Seed the fixed lore stats once, preserving their canonical order.
    existing = conn.execute("SELECT COUNT(*) AS n FROM lore_stats").fetchone()["n"]
    if existing == 0:
        conn.executemany(
            "INSERT INTO lore_stats (name) VALUES (?)",
            [(name,) for name in LORE_STATS],
        )

    conn.commit()
    conn.close()
