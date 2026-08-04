"""Book of Tales editor — Flask blueprint.

An ending-text editor layered on top of the ABOT Lore Tracker. A Quest pairs
one personal branch and one locational branch (from the existing branches
table). Each quest has decisions (questions), options (answers), and fragments
(consequence sentences) that an assembly engine stitches into the final text.

Roles: readers view; editors create and edit; admins additionally delete.
"""

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from auth import require_role
from db import (
    BAM_DIRECTIONS,
    DECISION_TYPES,
    MAX_MINOR,
    MAX_OPTIONS,
    MIN_OPTIONS,
    QUEST_TIERS,
    get_db,
)

book = Blueprint("book", __name__, url_prefix="/book")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _branch_label(row):
    """Label a branch row: 'Location - Boss (Personal)' or 'Location'."""
    location = row["location_name"] or row["location_code"] or "Unknown location"
    if row["type"] == "personal":
        boss = row["boss_name"] or "Unknown boss"
        return f"{location} - {boss} (Personal)"
    return location


def fetch_branch_options(conn):
    """Branches with display labels, split by kind, for dropdowns."""
    rows = conn.execute(
        """
        SELECT b.id, b.type,
               bo.name AS boss_name,
               lo.name AS location_name, lo.code AS location_code
        FROM branches b
        LEFT JOIN bosses bo ON bo.id = b.boss_id
        LEFT JOIN locations lo ON lo.id = b.location_id
        ORDER BY b.id
        """
    ).fetchall()
    personal, locational = [], []
    for b in rows:
        item = {"id": b["id"], "label": _branch_label(b)}
        (personal if b["type"] == "personal" else locational).append(item)
    return personal, locational


def get_quest_or_404(conn, quest_id):
    quest = conn.execute(
        """
        SELECT q.*,
               pb.type AS p_type, pbo.name AS p_boss, plo.name AS p_loc_name,
               plo.code AS p_loc_code,
               lb.type AS l_type, llo.name AS l_loc_name, llo.code AS l_loc_code
        FROM book_quests q
        LEFT JOIN branches pb ON pb.id = q.personal_branch_id
        LEFT JOIN bosses pbo ON pbo.id = pb.boss_id
        LEFT JOIN locations plo ON plo.id = pb.location_id
        LEFT JOIN branches lb ON lb.id = q.locational_branch_id
        LEFT JOIN locations llo ON llo.id = lb.location_id
        WHERE q.id = ?
        """,
        (quest_id,),
    ).fetchone()
    if quest is None:
        abort(404)
    return quest


def touch_quest(conn, quest_id):
    conn.execute(
        "UPDATE book_quests SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (quest_id,),
    )


# --------------------------------------------------------------------------
# Quests
# --------------------------------------------------------------------------

@book.route("/")
@book.route("/quests")
def quests():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT q.id, q.name, q.tier,
               (SELECT COUNT(*) FROM book_decisions d WHERE d.quest_id = q.id)
                   AS decision_count,
               (SELECT COUNT(*) FROM book_fragments f
                    JOIN book_options o ON o.id = f.option_id
                    JOIN book_decisions d ON d.id = o.decision_id
                    WHERE d.quest_id = q.id) AS fragment_count
        FROM book_quests q
        ORDER BY q.name COLLATE NOCASE
        """
    ).fetchall()
    personal, locational = fetch_branch_options(conn)
    conn.close()
    return render_template(
        "book/quests.html",
        quests=rows,
        personal_branches=personal,
        locational_branches=locational,
        tiers=QUEST_TIERS,
    )


@book.route("/quests", methods=["POST"])
@require_role("editor")
def create_quest():
    name = request.form.get("name", "").strip()
    personal_id = request.form.get("personal_branch_id", type=int)
    locational_id = request.form.get("locational_branch_id", type=int)
    first_sentence = request.form.get("first_sentence", "").strip()
    tier = request.form.get("tier") or None
    if tier and tier not in QUEST_TIERS:
        tier = None
    if not name:
        flash("A quest needs a name.", "error")
        return redirect(url_for("book.quests"))

    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO book_quests
            (name, personal_branch_id, locational_branch_id, first_sentence, tier)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, personal_id or None, locational_id or None, first_sentence, tier),
    )
    conn.commit()
    quest_id = cur.lastrowid
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=quest_id))


@book.route("/quests/<int:quest_id>")
def quest_config(quest_id):
    conn = get_db()
    quest = get_quest_or_404(conn, quest_id)
    variables = conn.execute(
        "SELECT * FROM book_quest_vars WHERE quest_id = ? ORDER BY key",
        (quest_id,),
    ).fetchall()

    decision_rows = conn.execute(
        """
        SELECT * FROM book_decisions WHERE quest_id = ?
        ORDER BY CASE type WHEN 'base' THEN 0 WHEN 'advanced' THEN 1 ELSE 2 END,
                 sort_order, id
        """,
        (quest_id,),
    ).fetchall()
    decisions = []
    for d in decision_rows:
        options = conn.execute(
            "SELECT * FROM book_options WHERE decision_id = ? ORDER BY sort_order, id",
            (d["id"],),
        ).fetchall()
        decisions.append({**dict(d), "options": options})

    personal, locational = fetch_branch_options(conn)
    conn.close()

    counts = {t: sum(1 for d in decisions if d["type"] == t) for t in DECISION_TYPES}
    return render_template(
        "book/quest_config.html",
        quest=quest,
        variables=variables,
        decisions=decisions,
        counts=counts,
        personal_branches=personal,
        locational_branches=locational,
        tiers=QUEST_TIERS,
        bam_directions=BAM_DIRECTIONS,
        decision_types=DECISION_TYPES,
        min_options=MIN_OPTIONS,
        max_options=MAX_OPTIONS,
    )


@book.route("/quests/<int:quest_id>", methods=["POST"])
@require_role("editor")
def update_quest(quest_id):
    conn = get_db()
    get_quest_or_404(conn, quest_id)
    name = request.form.get("name", "").strip()
    if not name:
        flash("A quest needs a name.", "error")
        conn.close()
        return redirect(url_for("book.quest_config", quest_id=quest_id))
    tier = request.form.get("tier") or None
    if tier and tier not in QUEST_TIERS:
        tier = None
    conn.execute(
        """
        UPDATE book_quests
        SET name = ?, personal_branch_id = ?, locational_branch_id = ?,
            first_sentence = ?, last_sentence_override = ?, tier = ?,
            external_ref = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            name,
            request.form.get("personal_branch_id", type=int) or None,
            request.form.get("locational_branch_id", type=int) or None,
            request.form.get("first_sentence", "").strip(),
            request.form.get("last_sentence_override", "").strip() or None,
            tier,
            request.form.get("external_ref", "").strip() or None,
            quest_id,
        ),
    )
    conn.commit()
    conn.close()
    flash("Quest saved.", "success")
    return redirect(url_for("book.quest_config", quest_id=quest_id))


@book.route("/quests/<int:quest_id>/delete", methods=["POST"])
@require_role("admin")
def delete_quest(quest_id):
    conn = get_db()
    conn.execute("DELETE FROM book_quests WHERE id = ?", (quest_id,))
    conn.commit()
    conn.close()
    flash("Quest deleted.", "success")
    return redirect(url_for("book.quests"))


# --------------------------------------------------------------------------
# Quest variables
# --------------------------------------------------------------------------

@book.route("/quests/<int:quest_id>/vars", methods=["POST"])
@require_role("editor")
def add_var(quest_id):
    conn = get_db()
    get_quest_or_404(conn, quest_id)
    key = request.form.get("key", "").strip()
    value = request.form.get("value", "").strip()
    if not key or not value:
        flash("A variable needs both a key and a value.", "error")
    else:
        try:
            conn.execute(
                "INSERT INTO book_quest_vars (quest_id, key, value) VALUES (?, ?, ?)",
                (quest_id, key, value),
            )
            touch_quest(conn, quest_id)
            conn.commit()
        except Exception:
            flash(f"Variable '{key}' already exists.", "error")
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=quest_id))


@book.route("/vars/<int:var_id>/delete", methods=["POST"])
@require_role("admin")
def delete_var(var_id):
    conn = get_db()
    row = conn.execute(
        "SELECT quest_id FROM book_quest_vars WHERE id = ?", (var_id,)
    ).fetchone()
    if row is None:
        conn.close()
        abort(404)
    quest_id = row["quest_id"]
    conn.execute("DELETE FROM book_quest_vars WHERE id = ?", (var_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=quest_id))


# --------------------------------------------------------------------------
# Decisions (questions)
# --------------------------------------------------------------------------

@book.route("/quests/<int:quest_id>/decisions", methods=["POST"])
@require_role("editor")
def add_decision(quest_id):
    conn = get_db()
    get_quest_or_404(conn, quest_id)
    dtype = request.form.get("type")
    question = request.form.get("question", "").strip()
    if dtype not in DECISION_TYPES or not question:
        flash("A decision needs a type and a question.", "error")
        conn.close()
        return redirect(url_for("book.quest_config", quest_id=quest_id))

    # Enforce the structural caps the assembly engine relies on.
    existing = conn.execute(
        "SELECT COUNT(*) AS n FROM book_decisions WHERE quest_id = ? AND type = ?",
        (quest_id, dtype),
    ).fetchone()["n"]
    if dtype == "base" and existing >= 1:
        flash("A quest can only have one base decision.", "error")
        conn.close()
        return redirect(url_for("book.quest_config", quest_id=quest_id))
    if dtype == "advanced" and existing >= 1:
        flash("A quest can only have one advanced decision.", "error")
        conn.close()
        return redirect(url_for("book.quest_config", quest_id=quest_id))
    if dtype == "minor" and existing >= MAX_MINOR:
        flash(f"A quest can have at most {MAX_MINOR} minor decisions.", "error")
        conn.close()
        return redirect(url_for("book.quest_config", quest_id=quest_id))

    conn.execute(
        "INSERT INTO book_decisions (quest_id, type, question, sort_order) "
        "VALUES (?, ?, ?, ?)",
        (quest_id, dtype, question, existing),
    )
    touch_quest(conn, quest_id)
    conn.commit()
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=quest_id))


@book.route("/decisions/<int:decision_id>", methods=["POST"])
@require_role("editor")
def update_decision(decision_id):
    conn = get_db()
    row = conn.execute(
        "SELECT quest_id FROM book_decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if row is None:
        conn.close()
        abort(404)
    question = request.form.get("question", "").strip()
    if question:
        conn.execute(
            "UPDATE book_decisions SET question = ? WHERE id = ?",
            (question, decision_id),
        )
        touch_quest(conn, row["quest_id"])
        conn.commit()
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=row["quest_id"]))


@book.route("/decisions/<int:decision_id>/delete", methods=["POST"])
@require_role("admin")
def delete_decision(decision_id):
    conn = get_db()
    row = conn.execute(
        "SELECT quest_id FROM book_decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if row is None:
        conn.close()
        abort(404)
    quest_id = row["quest_id"]
    conn.execute("DELETE FROM book_decisions WHERE id = ?", (decision_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=quest_id))


# --------------------------------------------------------------------------
# Options (answers)
# --------------------------------------------------------------------------

@book.route("/decisions/<int:decision_id>/options", methods=["POST"])
@require_role("editor")
def add_option(decision_id):
    conn = get_db()
    decision = conn.execute(
        "SELECT id, quest_id, type FROM book_decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if decision is None:
        conn.close()
        abort(404)
    label = request.form.get("label", "").strip()
    bam = request.form.get("bam_direction") or None
    if bam and bam not in BAM_DIRECTIONS:
        bam = None
    if not label:
        flash("An option needs a label.", "error")
        conn.close()
        return redirect(url_for("book.quest_config", quest_id=decision["quest_id"]))

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM book_options WHERE decision_id = ?", (decision_id,)
    ).fetchone()["n"]
    if count >= MAX_OPTIONS:
        flash(f"A decision can have at most {MAX_OPTIONS} options.", "error")
        conn.close()
        return redirect(url_for("book.quest_config", quest_id=decision["quest_id"]))

    conn.execute(
        "INSERT INTO book_options (decision_id, label, bam_direction, sort_order) "
        "VALUES (?, ?, ?, ?)",
        (decision_id, label, bam, count),
    )
    touch_quest(conn, decision["quest_id"])
    conn.commit()
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=decision["quest_id"]))


@book.route("/options/<int:option_id>", methods=["POST"])
@require_role("editor")
def update_option(option_id):
    conn = get_db()
    option = conn.execute(
        """
        SELECT o.id, d.quest_id
        FROM book_options o JOIN book_decisions d ON d.id = o.decision_id
        WHERE o.id = ?
        """,
        (option_id,),
    ).fetchone()
    if option is None:
        conn.close()
        abort(404)
    label = request.form.get("label", "").strip()
    bam = request.form.get("bam_direction") or None
    if bam and bam not in BAM_DIRECTIONS:
        bam = None
    if label:
        conn.execute(
            "UPDATE book_options SET label = ?, bam_direction = ? WHERE id = ?",
            (label, bam, option_id),
        )
        touch_quest(conn, option["quest_id"])
        conn.commit()
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=option["quest_id"]))


@book.route("/options/<int:option_id>/delete", methods=["POST"])
@require_role("admin")
def delete_option(option_id):
    conn = get_db()
    option = conn.execute(
        """
        SELECT o.id, d.quest_id
        FROM book_options o JOIN book_decisions d ON d.id = o.decision_id
        WHERE o.id = ?
        """,
        (option_id,),
    ).fetchone()
    if option is None:
        conn.close()
        abort(404)
    quest_id = option["quest_id"]
    conn.execute("DELETE FROM book_options WHERE id = ?", (option_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("book.quest_config", quest_id=quest_id))
