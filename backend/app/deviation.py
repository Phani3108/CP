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
import os
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
