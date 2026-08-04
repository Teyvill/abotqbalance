"""Book of Tales assembly engine.

Pure-ish functions that stitch a location's final text from its fragments,
given a run state. Kept separate from the blueprint so it can be tested and
reused (e.g. by an export that mirrors the game engine's assembly).

Slot order is fixed: first_sentence, base, advanced, minors, last_sentence.
"""

import re

_VAR_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def substitute_vars(text, variables):
    """Replace {key} tokens using the variables mapping; unknown ones stay."""
    def repl(match):
        key = match.group(1)
        return variables.get(key, match.group(0))

    return _VAR_RE.sub(repl, text or "")


def unknown_vars(text, variables):
    """Return {key} tokens present in text but not defined in variables."""
    return sorted({k for k in _VAR_RE.findall(text or "") if k not in variables})


def get_default_last_sentence(conn):
    row = conn.execute(
        "SELECT value FROM book_globals WHERE key = 'default_last_sentence'"
    ).fetchone()
    return row["value"] if row else ""


def pick_base(conn, option_id, weight, party_state):
    """Best base fragment for an option: exact weight/party beats NULL fallback."""
    rows = conn.execute(
        """
        SELECT text, weight_tier, party_state
        FROM book_fragments
        WHERE option_id = ? AND modifies_base_option_id IS NULL
          AND (weight_tier = ? OR weight_tier IS NULL)
          AND (party_state = ? OR party_state IS NULL)
        ORDER BY sort_order, id
        """,
        (option_id, weight, party_state),
    ).fetchall()
    if not rows:
        return None
    best = max(
        rows,
        key=lambda r: (r["weight_tier"] == weight) * 2 + (r["party_state"] == party_state),
    )
    return best["text"]


def pick_mod(conn, option_id, base_option_id, party_state):
    """Best advanced/minor fragment: exact base/party match beats NULL fallback."""
    rows = conn.execute(
        """
        SELECT text, modifies_base_option_id, party_state
        FROM book_fragments
        WHERE option_id = ? AND weight_tier IS NULL
          AND (modifies_base_option_id = ? OR modifies_base_option_id IS NULL)
          AND (party_state = ? OR party_state IS NULL)
        ORDER BY sort_order, id
        """,
        (option_id, base_option_id, party_state),
    ).fetchall()
    if not rows:
        return None
    best = max(
        rows,
        key=lambda r: (r["modifies_base_option_id"] == base_option_id) * 2
        + (r["party_state"] == party_state),
    )
    return best["text"]


def render_location(conn, quest, loc_state):
    """Assemble one location's text. Returns (text, slots).

    `slots` lists each slot with whether it produced text, for the preview to
    highlight gaps. loc_state keys: base_option_id, base_weight,
    advanced_option_id, minor_option_ids, party_state.
    """
    base_option_id = loc_state.get("base_option_id")
    base_weight = loc_state.get("base_weight") or "light"
    advanced_option_id = loc_state.get("advanced_option_id")
    minor_option_ids = loc_state.get("minor_option_ids") or []
    party_state = loc_state.get("party_state")

    parts = []
    slots = []

    first = (quest["first_sentence"] or "").strip()
    if first:
        parts.append(first)
    slots.append({"slot": "first_sentence", "filled": bool(first)})

    if base_option_id:
        base_text = pick_base(conn, base_option_id, base_weight, party_state)
        if base_text:
            parts.append(base_text)
        slots.append({"slot": "base", "filled": bool(base_text)})
    else:
        slots.append({"slot": "base", "filled": False})

    if advanced_option_id:
        adv_text = pick_mod(conn, advanced_option_id, base_option_id, party_state)
        if adv_text:
            parts.append(adv_text)
        slots.append({"slot": "advanced", "filled": bool(adv_text)})

    for m in minor_option_ids:
        mod_text = pick_mod(conn, m, base_option_id, party_state)
        if mod_text:
            parts.append(mod_text)
        slots.append({"slot": "minor:%s" % m, "filled": bool(mod_text)})

    last = (quest["last_sentence_override"] or get_default_last_sentence(conn)).strip()
    if last:
        parts.append(last)
    slots.append({"slot": "last_sentence", "filled": bool(last)})

    variables = {
        r["key"]: r["value"]
        for r in conn.execute(
            "SELECT key, value FROM book_quest_vars WHERE quest_id = ?",
            (quest["id"],),
        ).fetchall()
    }
    text = substitute_vars(" ".join(parts), variables)
    missing = unknown_vars(" ".join(parts), variables)
    return text, {"slots": slots, "unknown_vars": missing}
