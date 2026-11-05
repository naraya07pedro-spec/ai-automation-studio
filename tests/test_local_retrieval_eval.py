"""Slice 58: reproducible offline retrieval evaluation and citation boundary tests."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from local_document_analysis import LocalDocumentError
from local_multi_document_analysis import (
    CHUNK_CHARS, CHUNK_OVERLAP, MAX_SELECTED_CHUNKS,
    analyze_multiple_documents, retrieve_passages)
from local_retrieval_eval import evaluate, evaluation_cases


class RetrievalEvidenceV2Tests(unittest.TestCase):
    def test_curated_evaluation_has_no_regressions_or_hidden_failures(self):
        report = evaluate()
        self.assertEqual("SYNTHETIC_CURATED_RETRIEVAL_AUDIT_NOT_REAL_WORLD",
                         report["evaluation_type"])
        self.assertEqual(len(evaluation_cases()), report["cases"])
        self.assertEqual(report["cases"], report["new_pass"], report)
        self.assertEqual([], report["regressed_cases"])
        self.assertIn("cross_boundary", report["improved_cases"])
        self.assertIn("plural_approval", report["improved_cases"])
        self.assertIn("indonesian_nya", report["improved_cases"])
        self.assertLess(report["legacy_pass"], report["new_pass"])

    def test_overlapping_chunk_is_source_exact_and_citable_across_old_edge(self):
        case = next(x for x in evaluation_cases() if x["id"] == "cross_boundary")
        docs = case["documents"]
        actual = retrieve_passages(docs, case["question"])
        self.assertTrue(0 < len(actual) <= MAX_SELECTED_CHUNKS)
        boundary = next(c for c in actual if
                        case["expected_quote"] in c["text"])
        self.assertEqual(1, boundary["file_index"])
        self.assertEqual("F2C2", boundary["chunk_id"])
        self.assertEqual(CHUNK_CHARS - CHUNK_OVERLAP, boundary["start_char"])
        answer = analyze_multiple_documents(
            docs, case["question"], "fixture:model",
            transport=lambda _ : {"message": {"content": json.dumps({
                "answer": "There is an entry mentioning the meeting time.",
                "insufficient_evidence": False,
                "citations": [{
                    "chunk_id": boundary["chunk_id"],
                    "quote": case["expected_quote"],
                }],
            })}})
        citation = answer["citations"][0]
        self.assertEqual("schedule.md", citation["filename"])
        self.assertEqual(case["expected_quote"],
                         docs[1]["content"][citation["start_char"]:citation["end_char"]])
        self.assertEqual("DETERMINISTIC_LEXICAL_OVERLAP_V2", answer["retrieval"])
        self.assertEqual(False, answer["interpretation_verified"])

    def test_retrieval_evidence_is_bounded_and_is_not_answer_confidence(self):
        docs = [
            {"filename": "ops.txt", "content": "Escalation manual is available."},
            {"filename": "schedule.md",
             "content": "Maintenance review meeting starts at 18:30 WIB."}
        ]
        answer = analyze_multiple_documents(
            docs, "What time does the maintenance review meeting start?",
            "fixture:model",
            transport=lambda _ : {"message": {"content": json.dumps({
                "answer": "18:30 WIB",
                "insufficient_evidence": False,
                "citations": [{
                    "chunk_id": "F2C1",
                    "quote": "meeting starts at 18:30 WIB",
                }],
            })}})
        self.assertGreater(len(answer["retrieval_evidence"]), 0)
        self.assertLessEqual(len(answer["retrieval_evidence"]), MAX_SELECTED_CHUNKS)
        for hit in answer["retrieval_evidence"]:
            self.assertEqual(
                docs[0 if hit["filename"] == "ops.txt" else 1]["content"][
                    hit["start_char"]:hit["end_char"]][:160],
                hit["source_preview"])
            self.assertEqual(hit["lexical_overlap_count"], len(hit["matched_terms"]))
            self.assertLessEqual(len(hit["source_preview"]), 160)
        self.assertIn("may miss synonyms", answer["retrieval_limitations"])

    def test_no_match_reports_uncertainty_with_evidence_empty(self):
        docs = [{"filename": "one.txt", "content": "Company meetings happen daily."},
                {"filename": "two.md", "content": "Support hours are available online."}]
        result = analyze_multiple_documents(
            docs, "Quantum zebra nebula?", "fixture:model",
            transport=lambda _: (_ for _ in ()).throw(AssertionError("not invoked")))
        self.assertTrue(result["insufficient_evidence"])
        self.assertEqual([], result["retrieval_evidence"])
        self.assertEqual(0, result["model_calls"])
        self.assertIn("DETERMINISTIC_LEXICAL_OVERLAP_V2", result["retrieval"])

    def test_source_from_wrong_file_is_still_rejected(self):
        docs = [{"filename": "one.txt", "content": "Maintenance meeting happens daily."},
                {"filename": "two.md", "content": "Maintenance meeting starts at 18:30 WIB."}]
        with self.assertRaisesRegex(LocalDocumentError,
                                    "invalid_multi_document_citation"):
            analyze_multiple_documents(
                docs, "When does maintenance meeting start?", "fixture:model",
                transport=lambda _: {"message": {"content": json.dumps({
                    "answer": "18:30",
                    "insufficient_evidence": False,
                    "citations": [{"chunk_id": "F1C1",
                                   "quote": "starts at 18:30 WIB"}],
                })}})


if __name__ == "__main__":
    unittest.main()
