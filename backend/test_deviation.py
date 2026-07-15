"""Unit tests for the symmetric clause aligner (app.deviation)."""
import math

from app.deviation import align_clauses, build_version_diff, get_thresholds, word_diff


class FakeClause:
    def __init__(self, clause_type, embedding, text=None, id=None):
        self.clause_type = clause_type
        self.embedding = embedding
        self.text_content = text if text is not None else clause_type
        self.id = id


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


# 4-dim toy space: axes are "topics"
TERMINATION = unit([1, 0, 0, 0])
TERMINATION_TWEAKED = unit([1, 0.18, 0, 0])   # cos ≈ 0.984 → MATCHED at 0.92
TERMINATION_REWRITTEN = unit([1, 1, 0, 0])    # cos ≈ 0.707 → MODIFIED between 0.62 and 0.92
LIABILITY = unit([0, 0, 1, 0])
BRAND_NEW = unit([0, 0, 0, 1])                # orthogonal → no pair


def test_matched_modified_deleted_added():
    template = [
        FakeClause("Termination", TERMINATION),
        FakeClause("Limitation of Liability", LIABILITY),
    ]
    contract = [
        FakeClause("Termination", TERMINATION_REWRITTEN),
        FakeClause("Payment Terms", BRAND_NEW),
    ]
    res = align_clauses(template, contract, sim_high=0.92, sim_low=0.62)
    assert len(res.modified) == 1
    assert res.modified[0].template_clause.clause_type == "Termination"
    assert 0.62 <= res.modified[0].score < 0.92
    # Liability clause has no counterpart → DELETED (the killer feature)
    assert [t.clause_type for t in res.deleted] == ["Limitation of Liability"]
    # Brand-new contract clause → ADDED
    assert [c.clause_type for c in res.added] == ["Payment Terms"]
    assert res.summary() == {"matched": 0, "modified": 1, "deleted": 1, "added": 1}


def test_near_identical_is_matched():
    template = [FakeClause("Termination", TERMINATION)]
    contract = [FakeClause("Termination", TERMINATION_TWEAKED)]
    res = align_clauses(template, contract, sim_high=0.92, sim_low=0.62)
    assert len(res.matched) == 1 and not res.modified and not res.deleted and not res.added


def test_greedy_one_to_one_with_fragment_rescue():
    # Two contract clauses both similar to one template clause: the best one
    # pairs 1:1; the second is rescued as FRAGMENT coverage (split clause),
    # not raised as a scary ADDED insertion.
    template = [FakeClause("Termination", TERMINATION)]
    contract = [
        FakeClause("Termination", TERMINATION_TWEAKED),
        FakeClause("Termination Copy", TERMINATION_REWRITTEN),
    ]
    res = align_clauses(template, contract, sim_high=0.92, sim_low=0.62)
    assert len(res.matched) == 1
    assert res.matched[0].contract_clause.clause_type == "Termination"
    assert len(res.modified) == 1 and res.modified[0].fragment is True
    assert res.added == []


def test_missing_embeddings_are_skipped_not_misclassified():
    template = [FakeClause("Termination", None)]
    contract = [FakeClause("Termination", TERMINATION)]
    res = align_clauses(template, contract, sim_high=0.92, sim_low=0.62)
    assert res.skipped_template and not res.deleted
    assert [c.clause_type for c in res.added] == ["Termination"]


def test_type_mismatch_vetoes_mid_similarity_pair():
    # A deleted protection must NOT pair with an unrelated inserted clause at
    # mid similarity — clashing clause types veto the pair below the trust
    # threshold, so both sides classify DELETED / ADDED.
    template = [FakeClause("Non Solicitation", TERMINATION_REWRITTEN)]
    contract = [FakeClause("Fee Escalation", TERMINATION)]
    res = align_clauses(template, contract, sim_high=0.99, sim_low=0.5)
    assert res.modified == [] and res.matched == []
    assert [t.clause_type for t in res.deleted] == ["Non Solicitation"]
    assert [c.clause_type for c in res.added] == ["Fee Escalation"]


def test_type_mismatch_tolerated_above_trust():
    # Near-identical text keeps its pairing even when the extractor named the
    # clause types differently across runs.
    template = [FakeClause("Confidentiality", TERMINATION)]
    contract = [FakeClause("Payment Terms", TERMINATION_TWEAKED)]
    res = align_clauses(template, contract, sim_high=0.99, sim_low=0.5)
    assert len(res.modified) == 1
    assert res.modified[0].type_mismatch is True


def test_env_thresholds_parse():
    high, low = get_thresholds()
    assert 0 < low < high <= 1


# --- word_diff ---------------------------------------------------------------

def _join(ops, op):
    return "".join(o["text"] for o in ops if o["op"] == op)


def test_word_diff_equal_only():
    ops = word_diff("the cap is 12 months", "the cap is 12 months")
    assert all(o["op"] == "equal" for o in ops)
    assert _join(ops, "equal") == "the cap is 12 months"


def test_word_diff_replace_surfaces_delete_and_insert():
    ops = word_diff("liability capped at 12 months", "liability capped at 24 months")
    # "12" deleted, "24" inserted; the rest stays equal and losslessly re-joins.
    assert _join(ops, "delete") == "12"
    assert _join(ops, "insert") == "24"
    old = "".join(o["text"] for o in ops if o["op"] in ("equal", "delete"))
    new = "".join(o["text"] for o in ops if o["op"] in ("equal", "insert"))
    assert old == "liability capped at 12 months"
    assert new == "liability capped at 24 months"


def test_word_diff_pure_insert_and_delete():
    assert _join(word_diff("", "brand new clause"), "insert") == "brand new clause"
    assert _join(word_diff("was here", ""), "delete") == "was here"


def test_word_diff_long_input_degrades():
    old = "a " * 5000
    new = "b " * 5000
    ops = word_diff(old, new, max_chars=100)
    assert [o["op"] for o in ops] == ["delete", "insert"]


# --- build_version_diff ------------------------------------------------------

def test_build_version_diff_guards_unanalyzed_parent():
    prev = [FakeClause("Termination", None, text="x")]
    new = [FakeClause("Termination", TERMINATION, text="y")]
    res = build_version_diff(prev, new, sim_high=0.92, sim_low=0.62)
    assert res == {"available": False, "reason": "parent_not_analyzed"}


def test_build_version_diff_classifies_all_change_types():
    prev = [
        FakeClause("Termination", TERMINATION, text="Either party may terminate on 30 days notice.", id="p1"),
        FakeClause("Limitation of Liability", LIABILITY, text="Liability capped at fees paid.", id="p2"),
        FakeClause("Confidentiality", TERMINATION_TWEAKED, text="Keep it secret.", id="p3"),
    ]
    new = [
        # Same topic axis as p1 but rewritten text → MODIFIED (word_diff finds edits)
        FakeClause("Termination", TERMINATION, text="Either party may terminate on 60 days notice.", id="n1"),
        # p2 Liability has no counterpart → REMOVED
        # p3 Confidentiality nearly identical embedding + identical text → UNCHANGED
        FakeClause("Confidentiality", TERMINATION_TWEAKED, text="Keep it secret.", id="n3"),
        # Brand-new clause → ADDED
        FakeClause("Payment Terms", BRAND_NEW, text="Net 45 payment.", id="n4"),
    ]
    res = build_version_diff(prev, new, sim_high=0.92, sim_low=0.62)
    assert res["available"] is True
    assert res["summary"] == {"added": 1, "removed": 1, "modified": 1, "unchanged": 1}

    by_type = {c["change_type"]: c for c in res["clauses"]}
    # MODIFIED carries a word diff and the cross-version identity edge.
    mod = by_type["MODIFIED"]
    assert mod["prev_clause_id"] == "p1" and mod["new_clause_id"] == "n1"
    assert any(o["op"] == "delete" for o in mod["word_diff"])
    assert any(o["op"] == "insert" for o in mod["word_diff"])
    # REMOVED / ADDED carry the right single-sided ids.
    assert by_type["REMOVED"]["prev_clause_id"] == "p2"
    assert by_type["REMOVED"]["new_clause_id"] is None
    assert by_type["ADDED"]["new_clause_id"] == "n4"
    assert by_type["ADDED"]["prev_clause_id"] is None
    # UNCHANGED has no word diff.
    assert by_type["UNCHANGED"]["word_diff"] is None
    # Changes sort ahead of unchanged.
    assert res["clauses"][-1]["change_type"] == "UNCHANGED"
