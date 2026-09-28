# backend/tests/test_policy_conflict.py

"""Days 94-95: the pure cross-document candidate-pair pre-filter."""

from ai.policy_conflict import find_candidate_pairs
from database.repositories import PolicyChunk


def _chunk(document_id, chunk_index, text) -> PolicyChunk:
    return PolicyChunk(document_id=document_id, chunk_index=chunk_index, text=text)


class TestFindCandidatePairs:

    def test_a_pair_sharing_enough_terms_across_documents_is_a_candidate(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout is thirty minutes for restricted resources."),
            _chunk(2, 0, "Escalation timeout is forty five minutes for restricted resources."),
        ]

        candidates = find_candidate_pairs(chunks, min_shared_terms=3)

        assert len(candidates) == 1
        assert candidates[0].chunk_a.document_id == 1
        assert candidates[0].chunk_b.document_id == 2

    def test_pairs_within_the_same_document_are_never_candidates(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout is thirty minutes for restricted resources."),
            _chunk(1, 1, "Escalation timeout is forty five minutes for restricted resources."),
        ]

        assert find_candidate_pairs(chunks, min_shared_terms=3) == []

    def test_pairs_below_the_shared_term_threshold_are_excluded(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout applies here."),
            _chunk(2, 0, "Vacation policy is unrelated content."),
        ]

        assert find_candidate_pairs(chunks, min_shared_terms=2) == []

    def test_the_threshold_is_inclusive_at_the_boundary(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout resources apply."),
            _chunk(2, 0, "Escalation timeout resources differ."),
        ]

        assert len(find_candidate_pairs(chunks, min_shared_terms=3)) == 1
        assert len(find_candidate_pairs(chunks, min_shared_terms=4)) == 0

    def test_shared_terms_are_reported_on_the_candidate(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout resources apply."),
            _chunk(2, 0, "Escalation timeout resources differ."),
        ]

        candidates = find_candidate_pairs(chunks, min_shared_terms=3)

        assert candidates[0].shared_terms == {"escalation", "timeout", "resources"}

    def test_results_are_ordered_deterministically(self):
        chunks = [
            _chunk(2, 0, "Escalation timeout resources apply here now."),
            _chunk(1, 1, "Escalation timeout resources differ here too."),
            _chunk(1, 0, "Escalation timeout resources vary here today."),
        ]

        candidates = find_candidate_pairs(chunks, min_shared_terms=3)

        keys = [(c.chunk_a.document_id, c.chunk_a.chunk_index, c.chunk_b.document_id, c.chunk_b.chunk_index) for c in candidates]
        assert keys == sorted(keys)

    def test_three_documents_produce_pairs_across_every_combination(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout resources."),
            _chunk(2, 0, "Escalation timeout resources."),
            _chunk(3, 0, "Escalation timeout resources."),
        ]

        candidates = find_candidate_pairs(chunks, min_shared_terms=3)

        pairs = {(c.chunk_a.document_id, c.chunk_b.document_id) for c in candidates}
        assert pairs == {(1, 2), (1, 3), (2, 3)}

    def test_a_chunk_with_only_stopwords_matches_nothing(self):
        chunks = [_chunk(1, 0, "the is a of"), _chunk(2, 0, "the is a of and more")]

        assert find_candidate_pairs(chunks, min_shared_terms=1) == []

    def test_empty_or_single_chunk_list_produces_no_candidates(self):
        assert find_candidate_pairs([]) == []
        assert find_candidate_pairs([_chunk(1, 0, "Escalation timeout resources.")]) == []

    def test_default_threshold_is_three(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout resources here."),
            _chunk(2, 0, "Escalation timeout resources there."),
        ]

        assert len(find_candidate_pairs(chunks)) == 1

    def test_genuinely_unrelated_chunks_produce_no_false_candidates(self):
        # Each written independently, no shared boilerplate phrasing -
        # a template shared across "unrelated" text (e.g. "topic number
        # N about X" repeated with only N varying) would itself share
        # enough terms to become a candidate, which is correct pre-filter
        # behavior (it is cheap and imprecise by design; Ollama is what
        # actually rules out a false positive), not a bug to test for here.
        chunks = [
            _chunk(1, 0, "Vacation requests are approved by HR within five business days."),
            _chunk(2, 0, "Server backups run nightly at 2am UTC."),
            _chunk(3, 0, "New hires complete onboarding training in their first week."),
            _chunk(4, 0, "The cafeteria menu rotates every Monday."),
            _chunk(5, 0, "Parking permits are issued annually each January."),
        ]

        assert find_candidate_pairs(chunks, min_shared_terms=3) == []
