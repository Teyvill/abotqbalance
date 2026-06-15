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

# Configure the password required for deletions.
cp .env.example .env
# then edit .env and set DELETE_PASSWORD

python app.py
```

The app runs at http://127.0.0.1:5000.

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

Deleting a boss, location, branch, or status requires the `DELETE_PASSWORD`
from `.env`. A confirmation modal asks for the password; a wrong password
shows an error and deletes nothing.

## Notes

- The 12 lore stats are seeded on first run in a fixed order and cannot be
  edited from the UI.
- `.env` and the SQLite database are git-ignored.
