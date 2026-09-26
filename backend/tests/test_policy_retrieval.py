# backend/tests/test_policy_retrieval.py

"""Days 89-91: keyword-overlap retrieval, pure and dependency-free."""

from ai.policy_retrieval import retrieve_relevant_chunks, tokenize
from database.repositories import PolicyChunk


def _chunk(document_id, chunk_index, text) -> PolicyChunk:
    return PolicyChunk(document_id=document_id, chunk_index=chunk_index, text=text)


class TestTokenize:

    def test_lowercases_and_splits_on_punctuation(self):
        assert tokenize("Escalations Time-Out After 30 Minutes.") == {"escalations", "time", "out", "after", "30", "minutes"}

    def test_stopwords_are_removed(self):
        assert tokenize("What is the policy on this") == {"policy"}

    def test_empty_string_yields_no_tokens(self):
        assert tokenize("") == set()

    def test_a_string_of_only_stopwords_yields_nothing(self):
        assert tokenize("the is a of") == set()


class TestRetrieveRelevantChunks:

    def test_returns_chunks_containing_a_query_term(self):
        chunks = [
            _chunk(1, 0, "Escalation timeout is 30 minutes."),
            _chunk(1, 1, "Vacation requests are handled separately."),
        ]

        results = retrieve_relevant_chunks(chunks, "What is the escalation timeout?")

        assert [r.chunk.chunk_index for r in results] == [0]

    def test_ranks_more_overlapping_chunks_higher(self):
        chunks = [
            _chunk(1, 0, "Escalation is routed to a manager."),
            _chunk(1, 1, "Escalation timeout is thirty minutes for restricted resources."),
        ]

        results = retrieve_relevant_chunks(chunks, "escalation timeout restricted resources")

        assert [r.chunk.chunk_index for r in results] == [1, 0]

    def test_chunks_with_zero_overlap_are_excluded_not_padded_in(self):
        chunks = [_chunk(1, 0, "Escalation policy details."), _chunk(1, 1, "Completely unrelated content about lunch menus.")]

        results = retrieve_relevant_chunks(chunks, "escalation", top_k=5)

        assert len(results) == 1

    def test_top_k_limits_the_result_count(self):
        chunks = [_chunk(1, i, f"Escalation rule number {i}.") for i in range(10)]

        results = retrieve_relevant_chunks(chunks, "escalation", top_k=3)

        assert len(results) == 3

    def test_ties_break_by_document_id_then_chunk_index(self):
        chunks = [
            _chunk(2, 0, "Escalation rule."),
            _chunk(1, 1, "Escalation rule."),
            _chunk(1, 0, "Escalation rule."),
        ]

        results = retrieve_relevant_chunks(chunks, "escalation", top_k=10)

        assert [(r.chunk.document_id, r.chunk.chunk_index) for r in results] == [(1, 0), (1, 1), (2, 0)]

    def test_a_query_of_only_stopwords_returns_nothing(self):
        chunks = [_chunk(1, 0, "Some real content here.")]

        assert retrieve_relevant_chunks(chunks, "what is the") == []

    def test_an_empty_chunk_list_returns_nothing(self):
        assert retrieve_relevant_chunks([], "escalation policy") == []

    def test_matching_is_case_insensitive(self):
        chunks = [_chunk(1, 0, "ESCALATION policy details.")]

        results = retrieve_relevant_chunks(chunks, "escalation")

        assert len(results) == 1

    def test_matched_terms_are_reported_on_the_result(self):
        chunks = [_chunk(1, 0, "Escalation timeout applies to restricted resources.")]

        results = retrieve_relevant_chunks(chunks, "escalation timeout")

        assert results[0].matched_terms == {"escalation", "timeout"}
        assert results[0].score == 2

    def test_repeating_one_matching_word_does_not_outrank_more_distinct_matches(self):
        chunks = [
            _chunk(1, 0, "Escalation escalation escalation escalation."),
            _chunk(1, 1, "Escalation timeout restricted."),
        ]

        results = retrieve_relevant_chunks(chunks, "escalation timeout restricted", top_k=10)

        assert [r.chunk.chunk_index for r in results] == [1, 0]
