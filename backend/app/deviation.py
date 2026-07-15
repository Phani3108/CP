"""Symmetric clause alignment between a standard template and an incoming contract.

This is the deterministic backbone of first-party-paper deviation analysis:
given embedded template clauses and embedded contract clauses, produce a
one-to-one alignment and classify every clause on BOTH sides:

- MATCHED   — pair with cosine similarity >= DEVIATION_SIM_HIGH (essentially standard)
- MODIFIED  — pair with DEVIATION_SIM_LOW <= similarity < DEVIATION_SIM_HIGH
- DELETED   — template clause no contract clause covers (protection removed!)
- ADDED     — contract clause not present in the template (counterparty insertion)

Pure functions, no DB or LLM access — unit-testable with synthetic vectors.
"""
import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np


def get_thresholds() -> tuple[float, float]:
    high = float(os.getenv("DEVIATION_SIM_HIGH", "0.92"))
    low = float(os.getenv("DEVIATION_SIM_LOW", "0.62"))
    if low > high:
        low, high = high, low
    return high, low


def get_trust_threshold() -> float:
    """Above this similarity a pair is accepted even if clause types clash
    (extraction naming drift); below it, clashing types veto the pair."""
    return float(os.getenv("DEVIATION_SIM_TRUST", "0.85"))


@dataclass
class AlignedPair:
    template_clause: Any
    contract_clause: Any
    score: float
    type_mismatch: bool = False
    fragment: bool = False  # rescued split/merge coverage, not a 1:1 pairing


@dataclass
class AlignmentResult:
    matched: list[AlignedPair] = field(default_factory=list)
    modified: list[AlignedPair] = field(default_factory=list)
    deleted: list[Any] = field(default_factory=list)   # template clauses
    added: list[Any] = field(default_factory=list)     # contract clauses
    skipped_template: list[Any] = field(default_factory=list)  # no embedding
    skipped_contract: list[Any] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "matched": len(self.matched),
            "modified": len(self.modified),
            "deleted": len(self.deleted),
            "added": len(self.added),
        }


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def _types_mismatch(a: str | None, b: str | None) -> bool:
    ta = (a or "").lower().strip()
    tb = (b or "").lower().strip()
    if not ta or not tb:
        return False
    if ta == tb or ta in tb or tb in ta:
        return False
    # any shared significant word (e.g. "Termination for Cause" vs "Termination")
    wa = {w for w in ta.replace("&", " ").split() if len(w) > 3}
    wb = {w for w in tb.replace("&", " ").split() if len(w) > 3}
    return not (wa & wb)


def align_clauses(
    template_clauses: list[Any],
    contract_clauses: list[Any],
    sim_high: float | None = None,
    sim_low: float | None = None,
) -> AlignmentResult:
    """Greedy one-to-one mutual alignment by cosine similarity.

    Clause objects only need `.embedding` (sequence | None) and `.clause_type`.
    Greedy best-pair-first assignment prevents two contract clauses collapsing
    onto the same template clause; pairs under `sim_low` are rejected so their
    members classify as DELETED / ADDED instead.
    """
    env_high, env_low = get_thresholds()
    high = env_high if sim_high is None else sim_high
    low = env_low if sim_low is None else sim_low

    result = AlignmentResult()

    t_items = [(i, t) for i, t in enumerate(template_clauses) if getattr(t, "embedding", None) is not None]
    c_items = [(j, c) for j, c in enumerate(contract_clauses) if getattr(c, "embedding", None) is not None]
    result.skipped_template = [t for t in template_clauses if getattr(t, "embedding", None) is None]
    result.skipped_contract = [c for c in contract_clauses if getattr(c, "embedding", None) is None]

    if not t_items or not c_items:
        result.deleted = [t for _, t in t_items]
        result.added = [c for _, c in c_items]
        return result

    trust = get_trust_threshold()

    T = _normalize(np.array([np.asarray(t.embedding, dtype=float) for _, t in t_items]))
    C = _normalize(np.array([np.asarray(c.embedding, dtype=float) for _, c in c_items]))
    S = T @ C.T  # (num_template, num_contract)

    mismatch = [
        [
            _types_mismatch(getattr(t, "clause_type", None), getattr(c, "clause_type", None))
            for _, c in c_items
        ]
        for _, t in t_items
    ]

    def pair_ok(ti: int, cj: int, score: float) -> bool:
        # Mid-similarity pairs with clashing clause types are suspect — e.g. a
        # deleted Non-Solicitation clause must not pair with an inserted
        # Fee-Escalation clause just because both mention the agreement.
        if score >= trust:
            return True
        return not mismatch[ti][cj]

    # Greedy: highest-similarity pairs claim their clauses first.
    order = np.dstack(np.unravel_index(np.argsort(-S, axis=None), S.shape))[0]
    t_taken: set[int] = set()
    c_taken: set[int] = set()
    for ti, cj in order:
        ti, cj = int(ti), int(cj)
        if ti in t_taken or cj in c_taken:
            continue
        score = float(S[ti, cj])
        if score < low:
            # Everything after this is lower-similarity — no more acceptable pairs.
            break
        if not pair_ok(ti, cj, score):
            continue
        t_taken.add(ti)
        c_taken.add(cj)
        t_obj = t_items[ti][1]
        c_obj = c_items[cj][1]
        pair = AlignedPair(
            template_clause=t_obj,
            contract_clause=c_obj,
            score=round(score, 4),
            type_mismatch=mismatch[ti][cj],
        )
        if score >= high:
            result.matched.append(pair)
        else:
            result.modified.append(pair)

    # Rescue passes for extraction drift: one run may SPLIT a clause the other
    # run merged. An unmatched clause that still resembles an already-claimed
    # counterpart is fragment coverage (MODIFIED), not a true ADDED/DELETED.
    for cj, (_, c_obj) in enumerate(c_items):
        if cj in c_taken:
            continue
        sims = S[:, cj]
        ti = int(np.argmax(sims))
        score = float(sims[ti])
        if score >= low and pair_ok(ti, cj, score):
            c_taken.add(cj)
            result.modified.append(AlignedPair(
                template_clause=t_items[ti][1],
                contract_clause=c_obj,
                score=round(score, 4),
                type_mismatch=mismatch[ti][cj],
                fragment=True,
            ))

    for ti, (_, t_obj) in enumerate(t_items):
        if ti in t_taken:
            continue
        sims = S[ti, :]
        cj = int(np.argmax(sims))
        score = float(sims[cj])
        if score >= low and pair_ok(ti, cj, score):
            t_taken.add(ti)
            result.modified.append(AlignedPair(
                template_clause=t_obj,
                contract_clause=c_items[cj][1],
                score=round(score, 4),
                type_mismatch=mismatch[ti][cj],
                fragment=True,
            ))

    result.deleted = [t for k, (_, t) in enumerate(t_items) if k not in t_taken]
    result.added = [c for k, (_, c) in enumerate(c_items) if k not in c_taken]
    return result


# ---------------------------------------------------------------------------
# Cross-version "what changed" diff — reuses align_clauses on two contract
# versions (previous vs new) and adds a word-level inline redline per modified
# clause. Pure/deterministic, no LLM — the basis of the Changes tab and
# multi-turn (version-vs-version) deviation.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    # Whitespace and punctuation are their own tokens so a re-join is lossless.
    return _TOKEN_RE.findall(text or "")


def word_diff(old_text: str, new_text: str, max_chars: int = 8000) -> list[dict]:
    """Word-level inline diff between two clause texts.

    Returns a flat op list ``[{"op": "equal"|"insert"|"delete", "text": str}]``
    the frontend renders as ``<span>/<ins>/<del>`` (escaped elements, never
    ``{@html}``). For very long inputs it degrades to a single delete+insert to
    bound cost. Deterministic, no LLM.
    """
    old_text = old_text or ""
    new_text = new_text or ""
    if len(old_text) > max_chars or len(new_text) > max_chars:
        ops: list[dict] = []
        if old_text:
            ops.append({"op": "delete", "text": old_text})
        if new_text:
            ops.append({"op": "insert", "text": new_text})
        return ops

    old_tokens = _tokenize(old_text)
    new_tokens = _tokenize(new_text)
    sm = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)

    ops = []

    def _emit(op: str, text: str) -> None:
        if not text:
            return
        if ops and ops[-1]["op"] == op:
            ops[-1]["text"] += text
        else:
            ops.append({"op": op, "text": text})

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            _emit("equal", "".join(old_tokens[i1:i2]))
        elif tag == "delete":
            _emit("delete", "".join(old_tokens[i1:i2]))
        elif tag == "insert":
            _emit("insert", "".join(new_tokens[j1:j2]))
        elif tag == "replace":
            _emit("delete", "".join(old_tokens[i1:i2]))
            _emit("insert", "".join(new_tokens[j1:j2]))
    return ops


def _clause_text(c: Any) -> str:
    return getattr(c, "text_content", None) or ""


def _clause_id(c: Any) -> Any:
    cid = getattr(c, "id", None)
    return str(cid) if cid is not None else None


# Changes-first ordering so the useful rows sit on top of the Changes tab.
_CHANGE_ORDER = {"MODIFIED": 0, "ADDED": 1, "REMOVED": 2, "UNCHANGED": 3}


def build_version_diff(
    prev_clauses: list[Any],
    new_clauses: list[Any],
    sim_high: float | None = None,
    sim_low: float | None = None,
) -> dict:
    """Clause-level diff between a previous contract version and the new one.

    Clause objects need ``.embedding``, ``.clause_type``, ``.text_content`` and
    ``.id``. Reuses :func:`align_clauses`; matched+modified pairs are re-judged by
    :func:`word_diff` so a >0.92-similar pair with a small edit still surfaces as
    a real redline.

    Guards: if the previous side has no embedded clauses, returns
    ``{"available": False, "reason": "parent_not_analyzed"}`` — otherwise the
    aligner would report every new clause as ADDED. Same for an unanalyzed new
    side.
    """
    if not prev_clauses or all(getattr(c, "embedding", None) is None for c in prev_clauses):
        return {"available": False, "reason": "parent_not_analyzed"}
    if not new_clauses or all(getattr(c, "embedding", None) is None for c in new_clauses):
        return {"available": False, "reason": "contract_not_analyzed"}

    result = align_clauses(prev_clauses, new_clauses, sim_high=sim_high, sim_low=sim_low)

    clauses: list[dict] = []
    modified = 0
    unchanged = 0

    for pair in list(result.matched) + list(result.modified):
        prev_c = pair.template_clause
        new_c = pair.contract_clause
        prev_text = _clause_text(prev_c)
        new_text = _clause_text(new_c)
        wd = word_diff(prev_text, new_text)
        changed = any(op["op"] != "equal" for op in wd)
        clauses.append({
            "change_type": "MODIFIED" if changed else "UNCHANGED",
            "clause_type": getattr(new_c, "clause_type", None) or getattr(prev_c, "clause_type", None),
            "prev_clause_id": _clause_id(prev_c),
            "new_clause_id": _clause_id(new_c),
            "alignment_score": pair.score,
            "prev_text": prev_text,
            "new_text": new_text,
            "word_diff": wd if changed else None,
        })
        if changed:
            modified += 1
        else:
            unchanged += 1

    for t in result.deleted:
        clauses.append({
            "change_type": "REMOVED",
            "clause_type": getattr(t, "clause_type", None),
            "prev_clause_id": _clause_id(t),
            "new_clause_id": None,
            "alignment_score": None,
            "prev_text": _clause_text(t),
            "new_text": None,
            "word_diff": None,
        })

    for c in result.added:
        clauses.append({
            "change_type": "ADDED",
            "clause_type": getattr(c, "clause_type", None),
            "prev_clause_id": None,
            "new_clause_id": _clause_id(c),
            "alignment_score": None,
            "prev_text": None,
            "new_text": _clause_text(c),
            "word_diff": None,
        })

    clauses.sort(key=lambda r: _CHANGE_ORDER.get(r["change_type"], 9))

    return {
        "available": True,
        "summary": {
            "added": len(result.added),
            "removed": len(result.deleted),
            "modified": modified,
            "unchanged": unchanged,
        },
        "clauses": clauses,
    }
