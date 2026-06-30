# ABOT Lore Tracker

Internal tool for the ABOT narrative team to track **lore stat** and
**status** usage across quest branches.

## Stack

- Python + Flask
- SQLite (single file: `abot.db`, created on first run)
- Chart.js (loaded via CDN) for the dashboard charts
- Plain HTML/CSS/JS frontend (no frameworks)

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure secrets and login credentials.
cp .env.example .env
# then edit .env (SECRET_KEY and the role logins/passwords)

python app.py
```

The app runs at http://127.0.0.1:5000.

## Access control

The whole app is behind a login. There are three roles, each with its own
login/password set via environment variables:

| Role | Can do |
|------|--------|
| **Reader** | View everything (read-only) |
| **Editor** | View, plus edit existing data (branch skill/status usage, existing status details) |
| **Admin** | Everything, including adding branches/statuses/bosses/locations and deleting |

A successful login lasts **24 hours**. Set these variables (a role is only
usable if both halves are set):

```
READER_LOGIN / READER_PASSWORD
EDITOR_LOGIN / EDITOR_PASSWORD
ADMIN_LOGIN  / ADMIN_PASSWORD
```

`SECRET_KEY` must be set and stable so sessions survive restarts.

## Skill usage: roll vs option

Each of the 12 skills is tracked per **method** — `roll` and `option` — for
every branch. The branch edit page has a Roll and an Option column, and the
dashboard's Lore Stats tab has a Method filter (All / Roll / Option).

## Pages

- `/` — Dashboard with two tabs (Lore Stats, Statuses). Each tab has its own
  filter panel (branch type / boss / location) and a bar chart that updates
  without a page reload. Bars are one color when unfiltered and a different
  color when any filter is active.
- `/branches` — List of branches with stat/status totals, plus a form to add a
  new branch (redirects to its edit page).
- `/branches/<id>/edit` — Fill in lore stat usage (12 stats), status usage
  (used in this branch), and view the read-only list of statuses acquirable in
  this branch.
- `/statuses` — Manage statuses: name, description, and which branches they can
  be acquired in.
- `/reference` — Manage bosses and locations.

## Deletion

Deleting a boss, location, branch, or status is **admin only**. A confirmation
modal asks the admin to confirm before the delete goes through.

## Deploying to Render

This app needs a host that runs Python with a persistent disk for the SQLite
file. (It cannot run on Netlify — Netlify has no Python function runtime and no
persistent filesystem.) A `render.yaml` blueprint is included:

1. Push this repo to GitHub.
2. In Render: **New > Blueprint**, point it at this repo. Render reads
   `render.yaml` and provisions a web service.
3. Set the role login/password environment variables (see Access control) as
   secrets in the Render dashboard. `SECRET_KEY` is generated automatically.
4. Deploy. The SQLite database lives on the mounted disk at
   `/var/data/abot.db` (via the `DATABASE_PATH` env var) so it survives
   restarts and redeploys.

**Important:** a persistent disk requires a **paid** Render instance (the
`plan: starter` in `render.yaml`). On the free plan, remove the `disk:` block —
but then the database is wiped on every restart/redeploy, so only do that for
throwaway testing.

## Cleaning the database

To wipe all data and start fresh (the lore stats reseed automatically), delete
the SQLite file and let the app recreate it. On Render, open the service
**Shell** and run:

```bash
rm -f /var/data/abot.db /var/data/abot.db-wal /var/data/abot.db-shm
```

Then restart the service. Locally, delete `abot.db` in the project folder.

## Notes

- The 12 lore stats are seeded on first run in a fixed order and cannot be
  edited from the UI.
- `.env` and the SQLite database are git-ignored.
