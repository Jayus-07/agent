# RAG fixture simulation and delete feedback

## Goal

Use every fixture under the user's `rag_eval_kb/files` directory to exercise the
current offline parser/chunker and quality metadata path, identify concrete
format-specific gaps, and fix the highest-impact reproducible defects. Also fix
the RAG document deletion failure path so backend state and the UI clearly
distinguish success, degraded cleanup, and unconfirmed remote outcomes.

## Scope

- Simulate parse → clean → structure → route → chunk → coverage/quality checks;
  do not write to the shared knowledge base, vector index, or registry.
- Inspect generated leaf/parent chunks, identifiers, links, source locations,
  page metadata, table metadata, token budgets, and parse/quality anomalies.
- Fix relevant parser/chunker defects demonstrated by the supplied fixture set.
- Fix backend delete result/error contract and frontend single/batch delete
  feedback in the RAG management UI.
- Make the knowledge-base catalog manageable from the workbench. A KB can own
  several active departments; an uploaded document still has one department.
  Switching the selected KB must refresh the upload department choices, and
  backend authorization must use the same persisted catalog and active-department
  source as the UI.
- Update the current RAG/domain and task documentation where contracts change.

## Stop conditions

- All 28 actual document fixtures are simulated; the two Office `~$` lock files
  are assessed separately as invalid upload inputs.
- Targeted deletion behavior checks pass for success, degraded, explicit
  failure, and remote response loss.
- The user receives a reviewable diff and offline evidence before any commit.
- No real upload, shared index mutation, database mutation, or service rebuild
  occurs without explicit authorization.
