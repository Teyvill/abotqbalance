"""ABOT lore tracker — Flask backend.

Tracks lore stat (skill) and status usage across quest branches for the ABOT
narrative team. Skill usage is tracked per method (roll / option). Access is
protected by a login with three roles (reader, editor, admin). See db.py for
the schema.
"""

import hmac
import os
import sqlite3
from datetime import timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from db import (
    BOSS_TYPES,
    CONTINENTS,
    METHODS,
    SECTORS,
    SEEDED_LOCATION_CODES,
    SETTLEMENTS,
    get_db,
    init_db,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "abot-dev-secret-key")
# Stay logged in for 24 hours after a successful login.
app.permanent_session_lifetime = timedelta(hours=24)

init_db()


# --------------------------------------------------------------------------
# Authentication and roles
# --------------------------------------------------------------------------

# Higher number = more privileges. Each role includes everything below it.
ROLE_LEVELS = {"reader": 1, "editor": 2, "admin": 3}

# Endpoints reachable without being logged in.
PUBLIC_ENDPOINTS = {"login", "static"}


def load_credentials():
    """Read the configured login/password for each role from the environment.

    A role is only usable if both its *_LOGIN and *_PASSWORD vars are set.
    """
    creds = {}
    for role in ROLE_LEVELS:
        login = os.environ.get(f"{role.upper()}_LOGIN")
        password = os.environ.get(f"{role.upper()}_PASSWORD")
        if login and password:
            creds[role] = (login, password)
    return creds


def authenticate(login, password):
    """Return the role matching these credentials, or None.

    Uses constant-time comparison to avoid leaking timing information.
    """
    for role, (expected_login, expected_password) in load_credentials().items():
        login_ok = hmac.compare_digest(login, expected_login)
        password_ok = hmac.compare_digest(password, expected_password)
        if login_ok and password_ok:
            return role
    return None


def has_role(min_role):
    """True if the logged-in user's role is at least `min_role`."""
    role = session.get("role")
    return bool(role) and ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS[min_role]


def require_role(min_role):
    """Decorator: abort with 403 unless the user has at least `min_role`."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not has_role(min_role):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


@app.before_request
def require_login():
    """Force login for everything except the login page and static files."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if "role" not in session:
        return redirect(url_for("login", next=request.path))
    return None


@app.context_processor
def inject_auth():
    """Expose role helpers to all templates for showing/hiding controls."""
    return {
        "current_role": session.get("role"),
        "current_user": session.get("user"),
        "can_edit": has_role("editor"),
        "can_admin": has_role("admin"),
    }


def _safe_next(target):
    """Only allow same-site relative redirects after login."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("dashboard")


@app.route("/login", methods=["GET", "POST"])
def login():
    # Already logged in: go straight to the dashboard.
    if "role" in session and request.method == "GET":
        return redirect(url_for("dashboard"))

    configured = bool(load_credentials())

    if request.method == "POST":
        login_value = request.form.get("login", "")
        password_value = request.form.get("password", "")
        role = authenticate(login_value, password_value)
        if role:
            session.clear()
            session.permanent = True
            session["role"] = role
            session["user"] = login_value
            return redirect(_safe_next(request.form.get("next")))
        flash("Incorrect login or password.", "error")

    return render_template(
        "login.html",
        next=request.args.get("next", ""),
        configured=configured,
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def location_display(row):
    """A location's display text: its name if set, otherwise its code."""
    name = row["location_name"] if "location_name" in row.keys() else None
    code = row["location_code"] if "location_code" in row.keys() else None
    return name or code or "Unknown location"


def branch_label(branch):
    """Human-readable label for a branch row.

    Personal: "Location - Boss (Personal)". Location: "Location".
    """
    location = location_display(branch)
    if branch["type"] == "personal":
        boss = branch["boss_name"] or "Unknown boss"
        return f"{location} - {boss} (Personal)"
    return location


def build_location_code(continent, sector, settlement):
    """Compose a location code from its parts, or None if invalid.

    Sector "Capital" -> continent capital; settlement "Capital" -> sector
    capital; otherwise a plain settlement.
    """
    if continent not in CONTINENTS:
        return None
    if sector == "Capital":
        return continent + "Capital"
    if sector in SECTORS:
        if settlement == "Capital":
            return continent + sector + "Capital"
        if settlement in SETTLEMENTS:
            return continent + sector + settlement
    return None


def fetch_branches(conn):
    """All branches joined with their boss/location names, with totals."""
    return conn.execute(
        """
        SELECT
            b.id, b.type, b.boss_id, b.location_id, b.notes,
            bo.name AS boss_name,
            lo.name AS location_name,
            lo.code AS location_code,
            (SELECT COALESCE(SUM(count), 0) FROM stat_usage
                WHERE branch_id = b.id) AS stat_total,
            (SELECT COALESCE(SUM(count), 0) FROM status_usage
                WHERE branch_id = b.id) AS status_total
        FROM branches b
        LEFT JOIN bosses bo ON bo.id = b.boss_id
        LEFT JOIN locations lo ON lo.id = b.location_id
        ORDER BY b.id
        """
    ).fetchall()


def build_branch_subquery(branch_type, boss_id, location_id):
    """Build a `SELECT id FROM branches ...` subquery for the active filters."""
    conditions = []
    params = []
    if branch_type in ("personal", "location"):
        conditions.append("type = ?")
        params.append(branch_type)
    if boss_id:
        conditions.append("boss_id = ?")
        params.append(boss_id)
    if location_id:
        conditions.append("location_id = ?")
        params.append(location_id)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return "SELECT id FROM branches" + where, params


def parse_filter_args():
    """Pull and normalise dashboard filter query parameters."""
    branch_type = request.args.get("branch_type", "all")
    if branch_type not in ("personal", "location"):
        branch_type = "all"
    boss_id = request.args.get("boss_id", type=int)
    location_id = request.args.get("location_id", type=int)
    return branch_type, boss_id, location_id


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@app.route("/")
def dashboard():
    conn = get_db()
    bosses = conn.execute(
        "SELECT * FROM bosses ORDER BY name COLLATE NOCASE"
    ).fetchall()
    locations = conn.execute(
        "SELECT id, code, name FROM locations ORDER BY code COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return render_template("dashboard.html", bosses=bosses, locations=locations)


@app.route("/api/stats")
def api_stats():
    branch_type, boss_id, location_id = parse_filter_args()
    subquery, params = build_branch_subquery(branch_type, boss_id, location_id)

    method = request.args.get("method")
    if method not in METHODS:
        method = None
    method_clause = " AND su.method = ?" if method else ""
    query_params = list(params) + ([method] if method else [])

    conn = get_db()
    rows = conn.execute(
        f"""
        SELECT ls.id, ls.name,
               COALESCE(SUM(su.count), 0) AS total
        FROM lore_stats ls
        LEFT JOIN stat_usage su
            ON su.stat_id = ls.id
            AND su.branch_id IN ({subquery}){method_clause}
        GROUP BY ls.id
        ORDER BY ls.id
        """,
        query_params,
    ).fetchall()
    conn.close()
    return jsonify(
        labels=[r["name"] for r in rows],
        values=[r["total"] for r in rows],
    )


@app.route("/api/statuses")
def api_statuses():
    branch_type, boss_id, location_id = parse_filter_args()
    subquery, params = build_branch_subquery(branch_type, boss_id, location_id)
    conn = get_db()
    rows = conn.execute(
        f"""
        SELECT s.id, s.name,
               COALESCE(SUM(su.count), 0) AS total
        FROM statuses s
        LEFT JOIN status_usage su
            ON su.status_id = s.id AND su.branch_id IN ({subquery})
        GROUP BY s.id
        ORDER BY s.name COLLATE NOCASE
        """,
        params,
    ).fetchall()
    conn.close()
    return jsonify(
        labels=[r["name"] for r in rows],
        values=[r["total"] for r in rows],
    )


# --------------------------------------------------------------------------
# Branches
# --------------------------------------------------------------------------

@app.route("/branches", methods=["GET", "POST"])
def branches():
    conn = get_db()

    if request.method == "POST":
        if not has_role("admin"):
            conn.close()
            abort(403)
        branch_type = request.form.get("type")
        notes = request.form.get("notes", "").strip()
        if branch_type not in ("personal", "location"):
            flash("Please choose a valid branch type.", "error")
            conn.close()
            return redirect(url_for("branches"))

        boss_id = None
        location_id = request.form.get("location_id", type=int)
        if not location_id:
            flash("Please choose a location for the branch.", "error")
            conn.close()
            return redirect(url_for("branches"))
        if branch_type == "personal":
            # Personal branches tie a boss to a location.
            boss_id = request.form.get("boss_id", type=int)
            if not boss_id:
                flash("Please choose a boss for a personal branch.", "error")
                conn.close()
                return redirect(url_for("branches"))

        cur = conn.execute(
            "INSERT INTO branches (type, boss_id, location_id, notes) "
            "VALUES (?, ?, ?, ?)",
            (branch_type, boss_id, location_id, notes or None),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return redirect(url_for("edit_branch", branch_id=new_id))

    branch_rows = fetch_branches(conn)
    bosses = conn.execute(
        "SELECT * FROM bosses ORDER BY name COLLATE NOCASE"
    ).fetchall()
    locations = conn.execute(
        "SELECT id, code, name FROM locations ORDER BY code COLLATE NOCASE"
    ).fetchall()
    conn.close()

    branch_list = [
        {**dict(b), "label": branch_label(b)} for b in branch_rows
    ]
    return render_template(
        "branches.html",
        branches=branch_list,
        bosses=bosses,
        locations=locations,
    )


@app.route("/branches/<int:branch_id>/edit", methods=["GET", "POST"])
def edit_branch(branch_id):
    conn = get_db()
    branch = conn.execute(
        """
        SELECT b.*, bo.name AS boss_name,
               lo.name AS location_name, lo.code AS location_code
        FROM branches b
        LEFT JOIN bosses bo ON bo.id = b.boss_id
        LEFT JOIN locations lo ON lo.id = b.location_id
        WHERE b.id = ?
        """,
        (branch_id,),
    ).fetchone()
    if branch is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        if not has_role("editor"):
            conn.close()
            abort(403)

        # Section 1: skill usage — upsert one row per stat per method.
        stat_ids = [
            r["id"] for r in conn.execute("SELECT id FROM lore_stats").fetchall()
        ]
        for stat_id in stat_ids:
            for method in METHODS:
                count = request.form.get(f"stat_{stat_id}_{method}", type=int) or 0
                if count < 0:
                    count = 0
                conn.execute(
                    """
                    INSERT INTO stat_usage (branch_id, stat_id, method, count)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (branch_id, stat_id, method)
                    DO UPDATE SET count = excluded.count
                    """,
                    (branch_id, stat_id, method, count),
                )

        # Section 2: status usage — replace the whole set from the form.
        conn.execute("DELETE FROM status_usage WHERE branch_id = ?", (branch_id,))
        su_status_ids = request.form.getlist("status_usage_id")
        su_counts = request.form.getlist("status_usage_count")
        seen = set()
        for sid_raw, count_raw in zip(su_status_ids, su_counts):
            try:
                sid = int(sid_raw)
                count = int(count_raw)
            except (TypeError, ValueError):
                continue
            if sid in seen or count <= 0:
                continue
            seen.add(sid)
            conn.execute(
                "INSERT INTO status_usage (branch_id, status_id, count) "
                "VALUES (?, ?, ?)",
                (branch_id, sid, count),
            )

        conn.commit()
        conn.close()
        flash("Branch data saved.", "success")
        return redirect(url_for("edit_branch", branch_id=branch_id))

    # Section 1 data: every stat with its roll and option counts (default 0).
    stats = conn.execute(
        """
        SELECT ls.id, ls.name,
               COALESCE(MAX(CASE WHEN su.method = 'roll' THEN su.count END), 0) AS roll,
               COALESCE(MAX(CASE WHEN su.method = 'option' THEN su.count END), 0) AS opt
        FROM lore_stats ls
        LEFT JOIN stat_usage su
            ON su.stat_id = ls.id AND su.branch_id = ?
        GROUP BY ls.id
        ORDER BY ls.id
        """,
        (branch_id,),
    ).fetchall()

    # Section 2 data: status usages already recorded for this branch.
    status_usages = conn.execute(
        """
        SELECT su.status_id, s.name, su.count
        FROM status_usage su
        JOIN statuses s ON s.id = su.status_id
        WHERE su.branch_id = ?
        ORDER BY s.name COLLATE NOCASE
        """,
        (branch_id,),
    ).fetchall()

    # Section 3 data (read-only): statuses acquirable in this branch.
    acquirable = conn.execute(
        """
        SELECT s.id, s.name
        FROM statuses s
        JOIN status_branch_acquire sba ON sba.status_id = s.id
        WHERE sba.branch_id = ?
        ORDER BY s.name COLLATE NOCASE
        """,
        (branch_id,),
    ).fetchall()

    all_statuses = conn.execute(
        "SELECT id, name FROM statuses ORDER BY name COLLATE NOCASE"
    ).fetchall()
    conn.close()

    return render_template(
        "branch_edit.html",
        branch=branch,
        branch_label=branch_label(branch),
        stats=stats,
        methods=METHODS,
        status_usages=status_usages,
        acquirable=acquirable,
        all_statuses=all_statuses,
    )


@app.route("/branches/<int:branch_id>/delete", methods=["POST"])
@require_role("admin")
def delete_branch(branch_id):
    conn = get_db()
    conn.execute("DELETE FROM branches WHERE id = ?", (branch_id,))
    conn.commit()
    conn.close()
    flash("Branch deleted.", "success")
    return redirect(url_for("branches"))


# --------------------------------------------------------------------------
# Statuses
# --------------------------------------------------------------------------

@app.route("/statuses", methods=["GET", "POST"])
def statuses():
    conn = get_db()

    if request.method == "POST":
        if not has_role("admin"):
            conn.close()
            abort(403)
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        branch_ids = request.form.getlist("branch_ids", type=int)
        if not name:
            flash("A status needs a name.", "error")
            conn.close()
            return redirect(url_for("statuses"))
        cur = conn.execute(
            "INSERT INTO statuses (name, description) VALUES (?, ?)",
            (name, description or None),
        )
        status_id = cur.lastrowid
        for bid in branch_ids:
            conn.execute(
                "INSERT OR IGNORE INTO status_branch_acquire "
                "(status_id, branch_id) VALUES (?, ?)",
                (status_id, bid),
            )
        conn.commit()
        conn.close()
        flash("Status added.", "success")
        return redirect(url_for("statuses"))

    status_rows = conn.execute(
        "SELECT * FROM statuses ORDER BY name COLLATE NOCASE"
    ).fetchall()
    branch_rows = fetch_branches(conn)
    branch_list = [{**dict(b), "label": branch_label(b)} for b in branch_rows]
    label_by_id = {b["id"]: b["label"] for b in branch_list}

    statuses_view = []
    for s in status_rows:
        acquire_ids = [
            r["branch_id"]
            for r in conn.execute(
                "SELECT branch_id FROM status_branch_acquire WHERE status_id = ?",
                (s["id"],),
            ).fetchall()
        ]
        statuses_view.append(
            {
                **dict(s),
                "branch_labels": [
                    label_by_id[bid] for bid in acquire_ids if bid in label_by_id
                ],
            }
        )
    conn.close()

    return render_template(
        "statuses.html",
        statuses=statuses_view,
        branches=branch_list,
    )


@app.route("/statuses/<int:status_id>/edit", methods=["GET", "POST"])
@require_role("editor")
def edit_status(status_id):
    conn = get_db()
    status = conn.execute(
        "SELECT * FROM statuses WHERE id = ?", (status_id,)
    ).fetchone()
    if status is None:
        conn.close()
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        branch_ids = request.form.getlist("branch_ids", type=int)
        if not name:
            flash("A status needs a name.", "error")
            conn.close()
            return redirect(url_for("edit_status", status_id=status_id))
        conn.execute(
            "UPDATE statuses SET name = ?, description = ? WHERE id = ?",
            (name, description or None, status_id),
        )
        conn.execute(
            "DELETE FROM status_branch_acquire WHERE status_id = ?", (status_id,)
        )
        for bid in branch_ids:
            conn.execute(
                "INSERT OR IGNORE INTO status_branch_acquire "
                "(status_id, branch_id) VALUES (?, ?)",
                (status_id, bid),
            )
        conn.commit()
        conn.close()
        flash("Status updated.", "success")
        return redirect(url_for("statuses"))

    branch_rows = fetch_branches(conn)
    branch_list = [{**dict(b), "label": branch_label(b)} for b in branch_rows]
    acquire_ids = {
        r["branch_id"]
        for r in conn.execute(
            "SELECT branch_id FROM status_branch_acquire WHERE status_id = ?",
            (status_id,),
        ).fetchall()
    }
    conn.close()

    return render_template(
        "status_edit.html",
        status=status,
        branches=branch_list,
        acquire_ids=acquire_ids,
    )


@app.route("/statuses/<int:status_id>/delete", methods=["POST"])
@require_role("admin")
def delete_status(status_id):
    conn = get_db()
    conn.execute("DELETE FROM statuses WHERE id = ?", (status_id,))
    conn.commit()
    conn.close()
    flash("Status deleted.", "success")
    return redirect(url_for("statuses"))


# --------------------------------------------------------------------------
# Reference data (bosses and locations)
# --------------------------------------------------------------------------

@app.route("/reference")
def reference():
    conn = get_db()
    # Each boss shows how many distinct locations it links to via its branches.
    bosses = conn.execute(
        """
        SELECT bo.id, bo.name, bo.type,
               (SELECT COUNT(DISTINCT b.location_id) FROM branches b
                    WHERE b.boss_id = bo.id AND b.location_id IS NOT NULL)
                   AS location_count
        FROM bosses bo
        ORDER BY bo.name COLLATE NOCASE
        """
    ).fetchall()
    # Each location shows how many branches are linked to it.
    locations = conn.execute(
        """
        SELECT l.id, l.code, l.name,
               (SELECT COUNT(*) FROM branches b WHERE b.location_id = l.id)
                   AS branch_count
        FROM locations l
        ORDER BY l.code COLLATE NOCASE
        """
    ).fetchall()
    conn.close()
    return render_template(
        "reference.html",
        bosses=bosses,
        locations=locations,
        continents=CONTINENTS,
        sectors=SECTORS,
        settlements=SETTLEMENTS,
        boss_types=BOSS_TYPES,
        seeded_codes=SEEDED_LOCATION_CODES,
    )


@app.route("/reference/bosses/add", methods=["POST"])
@require_role("admin")
def add_boss():
    name = request.form.get("name", "").strip()
    boss_type = request.form.get("type")
    if not name:
        flash("A boss needs a name.", "error")
        return redirect(url_for("reference"))
    if boss_type not in BOSS_TYPES:
        flash("Please choose a boss type.", "error")
        return redirect(url_for("reference"))
    conn = get_db()
    conn.execute("INSERT INTO bosses (name, type) VALUES (?, ?)", (name, boss_type))
    conn.commit()
    conn.close()
    flash("Boss added.", "success")
    return redirect(url_for("reference"))


@app.route("/reference/bosses/<int:boss_id>/delete", methods=["POST"])
@require_role("admin")
def delete_boss(boss_id):
    conn = get_db()
    conn.execute("DELETE FROM bosses WHERE id = ?", (boss_id,))
    conn.commit()
    conn.close()
    flash("Boss deleted.", "success")
    return redirect(url_for("reference"))


@app.route("/reference/locations/add", methods=["POST"])
@require_role("admin")
def add_location():
    continent = request.form.get("continent")
    sector = request.form.get("sector")
    settlement = request.form.get("settlement")
    name = request.form.get("name", "").strip()

    code = build_location_code(continent, sector, settlement)
    if not code:
        flash("Please choose a valid continent, sector, and settlement.", "error")
        return redirect(url_for("reference"))

    # Store the structured parts; sector/settlement are unset for capitals.
    sector_value = sector if sector in SECTORS else None
    settlement_value = settlement if settlement in SETTLEMENTS else None

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO locations (code, name, continent, sector, settlement) "
            "VALUES (?, ?, ?, ?, ?)",
            (code, name or None, continent, sector_value, settlement_value),
        )
        conn.commit()
        flash(f"Location {code} added.", "success")
    except sqlite3.IntegrityError:
        flash(f"Location {code} already exists.", "error")
    finally:
        conn.close()
    return redirect(url_for("reference"))


@app.route("/reference/locations/<int:location_id>/name", methods=["POST"])
@require_role("editor")
def rename_location(location_id):
    # The code is fixed; only the display name can be edited (or cleared).
    name = request.form.get("name", "").strip()
    conn = get_db()
    conn.execute(
        "UPDATE locations SET name = ? WHERE id = ?", (name or None, location_id)
    )
    conn.commit()
    conn.close()
    flash("Location name saved.", "success")
    return redirect(url_for("reference"))


@app.route("/reference/locations/<int:location_id>/delete", methods=["POST"])
@require_role("admin")
def delete_location(location_id):
    conn = get_db()
    row = conn.execute(
        "SELECT code FROM locations WHERE id = ?", (location_id,)
    ).fetchone()
    if row and row["code"] in SEEDED_LOCATION_CODES:
        conn.close()
        flash("Seeded locations cannot be deleted.", "error")
        return redirect(url_for("reference"))
    conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))
    conn.commit()
    conn.close()
    flash("Location deleted.", "success")
    return redirect(url_for("reference"))


@app.errorhandler(403)
def forbidden(_error):
    return render_template("403.html"), 403


if __name__ == "__main__":
    app.run(debug=True, port=5000)
