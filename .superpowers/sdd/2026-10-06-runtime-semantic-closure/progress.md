# SDD ledger — plan: docs/superpowers/plans/2026-10-06-runtime-semantic-closure.md

Setup: 当前分支 feat/domain-isolation-closure；工作区已有用户未提交报告、probe 文件和截图，均不触碰。

Pre-flight: Task 1 produces V2 contracts consumed by Tasks 2–7; Task 3 produces Registry descriptor fields consumed by Tasks 4、6、7；Task 4 produces RuntimeResult consumed by Tasks 5、7；Task 5 produces canonical State consumed by Task 7。

Baseline: `backend/tests/orchestration/test_domain_registry.py backend/tests/orchestration/test_domain_semantic_consistency.py backend/tests/orchestration/router/test_router_consolidation_adapters.py backend/tests/orchestration/graph/test_router_prefilter_order.py backend/tests/orchestration/graph/test_entry_mode_handoff.py backend/tests/test_state_key_guard.py backend/tests/orchestration/graph/test_direct_flow_e2e.py backend/tests/runtime/test_runtime_trace.py -q --no-cov` → 120 passed in 54.14s。

Task 1: Ruling: RuntimeType moved to backend/orchestration/runtime_types.py and re-exported by router/types.py — direct DomainGraph→router.types import caused a package-init circular import; neutral enum module preserves one type and avoids the cycle — cost if wrong: one extra contract module to maintain.

Task 1: Ruling: Stage gate separates PRODUCTION_BEHAVIOR_CHANGED (negative assertion) from positive gates and does not require AGENT_RUNTIME_ARCH_V2_READY before final — otherwise stage A exits nonzero by design — cost if wrong: an incorrect aggregator could report a false failure or false final readiness.

Task 1: complete (commit 9ed8f37, tests: contract/domain/gate tests → 17 passed; stage A aggregator → 127 passed, CONTRACT_V2_PASS=true, PRODUCTION_BEHAVIOR_CHANGED=false)

Task 2: Ruling: all main-router, prefilter, continuation, and pending-resume state exits now call route_update_for_mode/project_route_decision_to_legacy_state; the old route_mode value remains a compatibility projection and route_decision_v2 is emitted alongside it — cost if wrong: a missed exit would reintroduce a second legacy writer and make B10 non-mechanical.

Task 2: Ruling: engine-backed direct/workflow/plan updates suppress projection-generated flat domain fields when no hierarchical metadata exists, allowing the existing adapter to derive its domain without changing tool-selector behavior — cost if wrong: retaining the compatibility defaults would overwrite the legacy domain snapshot and change direct-path parameter resolution.

Task 2: complete (stage B aggregator: 142 passed, ROUTING_SEMANTIC_SPLIT_PASS=true, ROUTE_DECISION_V2_PASS=true, ROUTE_MODE_BACKWARD_COMPAT_PASS=true, ROUTE_SINGLE_WRITER_PASS=true, PRODUCTION_BEHAVIOR_CHANGED=false)

Task 3: Ruling: RuntimeTarget moved beside RuntimeType in runtime_types.py and router/types.py re-exports it; importing router.types from DomainGraphRegistry would otherwise execute the router package and recreate a registry/domain-router circular import — cost if wrong: one additional neutral contract module dependency.

Task 3: Ruling: registry replacement by the same canonical name remains idempotent for existing registration behavior, while runtime_id collisions across different names, alias collisions, missing parents, and self-parenting fail fast — cost if wrong: stricter startup validation can reject an invalid registration instead of silently routing to the wrong runtime.

Task 3: complete (stage C aggregator: 128 passed, RUNTIME_REGISTRY_PASS=true, DOMAIN_REGISTRATION_SINGLE_SOURCE_PASS=true, PRODUCTION_BEHAVIOR_CHANGED=false)

Task 4: Ruling: RuntimeResult is attached once at each domain adapter boundary; existing final_answer/domain context fields remain unchanged, while the original domain result is stored as ui_payload so Travel itinerary, CS evidence/action metadata, and Selection report context are not lost — cost if wrong: exposing only final_answer would silently discard structured domain output.

Task 4: Ruling: legacy domain status values are mapped to the closed RuntimeResult status set inside the adapter (needs_clarification→clarification, needs_handoff→handoff, failed→error, no_data→partial); this keeps the public result contract strict without changing domain-local status values.

Task 4: complete (stage D aggregator: 178 passed, RUNTIME_RESULT_CONTRACT_PASS=true, RUNTIME_RESULT_PASS=true, DOMAIN_OUTPUT_REGRESSION_PASS=true, NO_DOUBLE_GENERATION_PASS=true, PRODUCTION_BEHAVIOR_CHANGED=false)

Task 5: Ruling: State canonical readers prefer route_decision_v2, resolved_params, and clarification_request; route_decision, tool_arguments, and _clarify remain read-only compatibility fallbacks so存量 checkpoint and SSE consumers continue to work — cost if wrong: removing fallback reads would break old checkpoint resumes.

Task 5: Ruling: tool_selector no longer emits tool_arguments on new selection updates; resolved_params is the new execution-parameter writer, while direct_executor reads the compatibility fallback only when resuming older state.

Task 5: complete (stage E aggregator: 226 passed, STATE_CANONICALIZATION_PASS=true, STATE_CANONICAL_SOURCE_PASS=true, LEGACY_PROJECTION_PASS=true, STATE_UNKNOWN_KEY_TOTAL=0, PRODUCTION_BEHAVIOR_CHANGED=false)

Task 6: Ruling: registered domains now declare entry_mode_key and the registry derives domain family, entry-mode switch, runtime target, and aliases; prefilter guide/handoff logic consumes those live views instead of maintaining route-mode maps — cost if wrong: a missing descriptor field fails closed to execute rather than silently selecting a wrong guide target.

Task 6: Ruling: projection.py resolves domain runtime type/id/subflow through DomainGraphRegistry; a static AST guard rejects registered-domain mapping literals in projection, domain_router, execution_mode, and prefilter_chain. A fake_domain test proves metadata registration does not require edits to Router or builder — cost if wrong: a new domain may still require its own prefilter/intent evidence, but cannot require a second routing map.

Task 6: complete (stage F aggregator: 122 passed, DOMAIN_REGISTRATION_GOVERNANCE_PASS=true, NEW_DOMAIN_REGISTRATION_SURFACE_PASS=true, ROUTER_DOMAIN_LITERAL_GUARD_PASS=true, PRODUCTION_BEHAVIOR_CHANGED=false)
