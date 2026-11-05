"""Offline reproducible retrieval audit: v1 fixed chunks versus v2 overlap.

These are SMALL CURATED SYNTHETIC examples, not real-world relevance metrics.
No Ollama weights, network, private documents or model-generated answers.
"""
import argparse
import json
import re

from local_multi_document_analysis import (
    CHUNK_CHARS, MAX_SELECTED_CHUNKS, retrieve_passages,
    validate_multiple_documents,
)

_OLD_STOP = frozenset((
    "yang", "dan", "atau", "dari", "untuk", "pada", "dengan", "apa",
    "apakah", "bagaimana", "kapan", "dimana", "di", "ke", "ini",
    "itu", "sebuah", "adalah", "tentang", "dalam", "mengenai",
    "the", "and", "or", "for", "from", "what", "when", "where",
    "how", "does", "do", "is", "are", "a", "an", "of", "to",
    "in", "at", "on", "with", "about", "which", "please", "tell",
))
_OLD_WORD = re.compile(r"\w+", re.UNICODE)


def _legacy_terms(text):
    return {word for word in _OLD_WORD.findall(text.casefold())
            if len(word) >= 2 and word not in _OLD_STOP}


def legacy_retrieve_passages(documents, question):
    """Exact copy of historical Slice 57 lexical chunk selection semantics."""
    query = _legacy_terms(question)
    candidates = []
    for file_number, document in enumerate(documents, 1):
        content = document["content"]
        for offset in range(0, len(content), CHUNK_CHARS):
            text = content[offset:offset + CHUNK_CHARS]
            overlap = query & _legacy_terms(text)
            if overlap:
                candidates.append({
                    "chunk_id": "F" + str(file_number) + "C" +
                                str(1 + offset // CHUNK_CHARS),
                    "file_index": file_number - 1,
                    "start_char": offset,
                    "text": text,
                    "score": len(overlap),
                })
    candidates.sort(key=lambda chunk: (-chunk["score"], chunk["file_index"],
                                       chunk["start_char"]))
    return candidates[:MAX_SELECTED_CHUNKS]


def evaluation_cases():
    return [
        {
            "id": "exact_english",
            "documents": [
                {"filename": "policy.txt", "content": "Delivery service starts Monday."},
                {"filename": "schedule.md", "content":
                 "Maintenance review meeting starts at 18:30 WIB every Thursday."},
            ],
            "question": "What time does the maintenance review meeting start?",
            "expected_filename": "schedule.md",
            "expected_quote": "meeting starts at 18:30 WIB",
        },
        {
            "id": "cross_boundary",
            "documents": [
                {"filename": "policy.txt", "content": "Receipt rules are in policy."},
                {"filename": "schedule.md", "content":
                 "x" * 435 + "maintenance review meeting starts at 18:30 WIB"},
            ],
            "question": "When is the maintenance review meeting start?",
            "expected_filename": "schedule.md",
            "expected_quote": "maintenance review meeting starts at 18:30 WIB",
        },
        {
            "id": "plural_approval",
            "documents": [
                {"filename": "legal.txt", "content": "Contract remains archived."},
                {"filename": "policy.md", "content": "Approval is required."},
            ],
            "question": "Approvals?",
            "expected_filename": "policy.md",
            "expected_quote": "Approval is required.",
        },
        {
            "id": "plural_policies",
            "documents": [
                {"filename": "draft.txt", "content": "Revision is finished."},
                {"filename": "policy.md", "content": "Policy controls retention."},
            ],
            "question": "Policies?",
            "expected_filename": "policy.md",
            "expected_quote": "Policy controls retention.",
        },
        {
            "id": "indonesian_nya",
            "documents": [
                {"filename": "summary.txt", "content": "Lampiran telah tersedia."},
                {"filename": "dokumen.md", "content":
                 "Dokumen menyebutkan waktu mulai pukul tujuh."},
            ],
            "question": "Dokumennya?",
            "expected_filename": "dokumen.md",
            "expected_quote": "Dokumen menyebutkan waktu mulai pukul tujuh.",
        },
        {
            "id": "punctuation",
            "documents": [
                {"filename": "note.txt", "content": "No billing details in this note."},
                {"filename": "terms.md", "content":
                 "Payment-terms are defined in the appendix."},
            ],
            "question": "Payment terms?",
            "expected_filename": "terms.md",
            "expected_quote": "Payment-terms are defined in the appendix.",
        },
        {
            "id": "no_overlap",
            "documents": [
                {"filename": "note.txt", "content": "Shipping is done on Monday."},
                {"filename": "terms.md", "content": "The office opens every Thursday."},
            ],
            "question": "Quantum zebra nebula?",
            "expected_filename": None,
            "expected_quote": None,
        },
    ]


def _success(passages, documents, expected_filename, expected_quote):
    if expected_filename is None:
        return len(passages) == 0
    return any(
        documents[hit["file_index"]]["filename"] == expected_filename
        and expected_quote in hit["text"]
        for hit in passages
    )


def evaluate():
    results = []
    for case in evaluation_cases():
        docs, question = case["documents"], case["question"]
        validate_multiple_documents(docs, question, "fixture:model")
        before = legacy_retrieve_passages(docs, question)
        after = retrieve_passages(docs, question)
        initial = _success(before, docs, case["expected_filename"],
                           case["expected_quote"])
        current = _success(after, docs, case["expected_filename"],
                           case["expected_quote"])
        results.append({
            "case": case["id"],
            "legacy_pass": initial,
            "new_pass": current,
            "retrieved_chunk_ids": [x["chunk_id"] for x in after],
        })
    return {
        "evaluation_type": "SYNTHETIC_CURATED_RETRIEVAL_AUDIT_NOT_REAL_WORLD",
        "cases": len(results),
        "legacy_pass": sum(x["legacy_pass"] for x in results),
        "new_pass": sum(x["new_pass"] for x in results),
        "improved_cases": [x["case"] for x in results if
                           x["new_pass"] and not x["legacy_pass"]],
        "regressed_cases": [x["case"] for x in results if
                            x["legacy_pass"] and not x["new_pass"]],
        "results": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline synthetic retrieval regression audit")
    parser.parse_args(argv)
    report = evaluate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["new_pass"] == report["cases"] and not report["regressed_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
