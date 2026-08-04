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
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from assembly import render_location
from auth import require_role
from db import (
    BAM_DIRECTIONS,
    DECISION_TYPES,
    MAX_MINOR,
    MAX_OPTIONS,
    MIN_OPTIONS,
    PARTY_STATES,
    QUEST_TIERS,
    WEIGHT_TIERS,
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


# --------------------------------------------------------------------------
# Fill sentences (fragments)
# --------------------------------------------------------------------------

def _load_decisions(conn, quest_id):
    """Decisions with their options, ordered base -> advanced -> minor."""
    rows = conn.execute(
        """
        SELECT * FROM book_decisions WHERE quest_id = ?
        ORDER BY CASE type WHEN 'base' THEN 0 WHEN 'advanced' THEN 1 ELSE 2 END,
                 sort_order, id
        """,
        (quest_id,),
    ).fetchall()
    result = []
    for d in rows:
        options = conn.execute(
            "SELECT * FROM book_options WHERE decision_id = ? ORDER BY sort_order, id",
            (d["id"],),
        ).fetchall()
        result.append({**dict(d), "options": [dict(o) for o in options]})
    return result


@book.route("/quests/<int:quest_id>/fill")
def fill(quest_id):
    conn = get_db()
    quest = get_quest_or_404(conn, quest_id)
    decisions = _load_decisions(conn, quest_id)

    base_options = []
    other_decisions = []
    for d in decisions:
        if d["type"] == "base":
            base_options = d["options"]

    # Prefill base option fragments keyed by (option_id, weight_tier).
    for d in decisions:
        if d["type"] == "base":
            for o in d["options"]:
                o["light"] = ""
                o["heavy"] = ""
                for f in conn.execute(
                    "SELECT weight_tier, text FROM book_fragments "
                    "WHERE option_id = ? AND weight_tier IS NOT NULL "
                    "AND party_state IS NULL AND modifies_base_option_id IS NULL",
                    (o["id"],),
                ).fetchall():
                    o[f["weight_tier"]] = f["text"]
        else:
            for o in d["options"]:
                frag = conn.execute(
                    "SELECT text, modifies_base_option_id, party_state "
                    "FROM book_fragments WHERE option_id = ? AND weight_tier IS NULL "
                    "ORDER BY id LIMIT 1",
                    (o["id"],),
                ).fetchone()
                o["text"] = frag["text"] if frag else ""
                o["modifies_base_option_id"] = (
                    frag["modifies_base_option_id"] if frag else None
                )
                o["party_state"] = frag["party_state"] if frag else None
            other_decisions.append(d)

    conn.close()
    has_base = any(d["type"] == "base" for d in decisions)
    return render_template(
        "book/fill.html",
        quest=quest,
        decisions=decisions,
        base_options=base_options,
        other_decisions=other_decisions,
        has_base=has_base,
        weight_tiers=WEIGHT_TIERS,
        party_states=PARTY_STATES,
    )


def _upsert_primary_fragment(conn, option_id, text, weight_tier,
                             modifies_base, party_state):
    """Create/update/delete the single Fill-managed fragment for a slot."""
    if weight_tier is not None:
        existing = conn.execute(
            "SELECT id FROM book_fragments WHERE option_id = ? AND weight_tier = ? "
            "AND party_state IS NULL AND modifies_base_option_id IS NULL "
            "ORDER BY id LIMIT 1",
            (option_id, weight_tier),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT id FROM book_fragments WHERE option_id = ? AND weight_tier IS NULL "
            "ORDER BY id LIMIT 1",
            (option_id,),
        ).fetchone()

    if text:
        if existing:
            conn.execute(
                "UPDATE book_fragments SET text = ?, modifies_base_option_id = ?, "
                "party_state = ? WHERE id = ?",
                (text, modifies_base, party_state, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO book_fragments "
                "(option_id, text, weight_tier, modifies_base_option_id, party_state) "
                "VALUES (?, ?, ?, ?, ?)",
                (option_id, text, weight_tier, modifies_base, party_state),
            )
    elif existing:
        conn.execute("DELETE FROM book_fragments WHERE id = ?", (existing["id"],))


@book.route("/quests/<int:quest_id>/fill", methods=["POST"])
@require_role("editor")
def save_fill(quest_id):
    conn = get_db()
    get_quest_or_404(conn, quest_id)
    decisions = _load_decisions(conn, quest_id)

    for d in decisions:
        for o in d["options"]:
            oid = o["id"]
            if d["type"] == "base":
                for weight in WEIGHT_TIERS:
                    text = request.form.get(f"base_{oid}_{weight}", "").strip()
                    _upsert_primary_fragment(conn, oid, text, weight, None, None)
            else:
                text = request.form.get(f"mod_{oid}_text", "").strip()
                base = request.form.get(f"mod_{oid}_base", type=int) or None
                party = request.form.get(f"mod_{oid}_party") or None
                if party not in PARTY_STATES:
                    party = None
                _upsert_primary_fragment(conn, oid, text, None, base, party)

    touch_quest(conn, quest_id)
    conn.commit()
    conn.close()
    flash("Fragments saved.", "success")
    return redirect(url_for("book.fill", quest_id=quest_id))


# --------------------------------------------------------------------------
# Preview (live single-location assembly)
# --------------------------------------------------------------------------

@book.route("/quests/<int:quest_id>/preview")
def preview(quest_id):
    conn = get_db()
    quest = get_quest_or_404(conn, quest_id)
    decisions = _load_decisions(conn, quest_id)
    base_decision = next((d for d in decisions if d["type"] == "base"), None)
    advanced_decision = next((d for d in decisions if d["type"] == "advanced"), None)
    minor_decisions = [d for d in decisions if d["type"] == "minor"]
    conn.close()
    return render_template(
        "book/preview.html",
        quest=quest,
        base_decision=base_decision,
        advanced_decision=advanced_decision,
        minor_decisions=minor_decisions,
        weight_tiers=WEIGHT_TIERS,
        party_states=PARTY_STATES,
    )


@book.route("/quests/<int:quest_id>/preview", methods=["POST"])
def preview_assemble(quest_id):
    conn = get_db()
    quest = get_quest_or_404(conn, quest_id)
    loc_state = {
        "base_option_id": request.form.get("base_option_id", type=int),
        "base_weight": request.form.get("base_weight") or "light",
        "advanced_option_id": request.form.get("advanced_option_id", type=int),
        "minor_option_ids": [
            int(v) for v in request.form.getlist("minor_option_ids") if v
        ],
        "party_state": request.form.get("party_state") or None,
    }
    text, meta = render_location(conn, quest, loc_state)
    conn.close()
    return jsonify(text=text, slots=meta["slots"], unknown_vars=meta["unknown_vars"])
