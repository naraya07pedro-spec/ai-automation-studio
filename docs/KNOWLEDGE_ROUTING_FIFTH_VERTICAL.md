# Fifth Executable Mock Vertical: Knowledge & Document Routing · Slice 33

Date: 2026-11-04. **A local metadata-only simulator**. No customer documents or provider writes.

## Delivered

The fifth template, `Knowledge & Document Routing`, is now `EXECUTABLE_MOCK`. The gallery truthfully shows **5 executable mock templates, 0 preview entries** among these five cards. This completes the count of five *sandbox workflows*, not the originally envisioned 45-scenario gallery or a production automation product.

The homepage offers a metadata-only form: synthetic event ID, predefined document kind, classification label, mock destination, and caller-claimed review flag. `POST /api/mock-knowledge-routing` invokes `MockKnowledgeRouting.process` and returns a redacted route preview, policy verdict, duplicate status, and six-step timeline. The response always indicates `document_uploaded: false`, `document_transferred: false`, `external_dispatch_count: 0`.

## Fixed policy

- `policy` and `technical_guide` route only to `mock_knowledge_base`; `invoice` only to `mock_finance_queue`; `contract` only to `mock_legal_queue`.
- Classification `restricted` is **always blocked** and creates no routing preview, irrespective of the caller's claimed review.
- `internal` classification requires a mock review acknowledgement to prepare a preview. **This acknowledgement is an unverified caller claim, not authorization or operator authentication.**
- `public` metadata may be mapped to a fixed mock destination without a review flag, but does not cause a transfer.
- Arbitrary document bodies, file bytes, URLs, credential fields, unknown categories or destinations, and non-boolean review values are refused. These inputs are never reflected in the response.
- Per-process duplicate state is capped at 1000 events and resets on restart. Identical event ID/content returns a duplicate without preview; reused ID with different data returns a conflict.
- Mismatched destination or blocked classification does not reserve the event key. A successful preview is **not** evidence that a document was copied or reviewed.

## How to run

At the repository root run `python app/local_server.py` and browse to `http://127.0.0.1:8765` on the same computer. Select the Knowledge & Document Routing card. Try matching document kind/destination, then restricted classification, and repeat the same synthetic ID to view the dedup state.

## Remaining gaps after five sandbox executables

No public hosting, verified browser visual QA, persistent run-history UI, real file upload or extraction, search/indexing or RAG pipeline, AI inference, credentials/connectors, durable operator approval or identity, safe real-provider dispatch, custom workflow builder, or full 45-template catalog. This is a narrow local mock exhibition, not a document processing application.

**Next priority:** Product integration audit across all five journeys, real-browser QA and safe public demonstration design before expanding mock functionality.
