"""Unit tests for the symmetric clause aligner (app.deviation)."""
import math

from app.deviation import align_clauses, get_thresholds


class FakeClause:
    def __init__(self, clause_type, embedding):
        self.clause_type = clause_type
        self.embedding = embedding
        self.text_content = clause_type


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
