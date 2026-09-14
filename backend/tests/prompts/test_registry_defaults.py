"""Registry + YAML defaults integrity tests (pure, no DB).

Covers:
  - Registry completeness (36 keys, 1 code_controlled, risk counts)
  - Every non-code-controlled key has a YAML default
  - Variable consistency between registry spec and YAML template
  - Smoke render all defaults with dummy variables
"""
import pytest

from backend.prompts.registry import PROMPT_REGISTRY, PromptSpec
from backend.prompts.renderer import PromptRenderer
from backend.prompts.loader import load_defaults


class TestRegistryShape:
    def test_total_count(self):
        # 2026-09-15 批次2 +1：market_research.analyzer
        assert len(PROMPT_REGISTRY) == 39

    def test_exactly_one_code_controlled(self):
        code_controlled = [s for s in PROMPT_REGISTRY.values() if s.code_controlled]
        assert len(code_controlled) == 1
        assert code_controlled[0].key == "security.input_guard"

    def test_risk_level_distribution(self):
        by_risk = {}
        for s in PROMPT_REGISTRY.values():
            by_risk.setdefault(s.risk_level, []).append(s.key)
        assert "critical" in by_risk
        assert len(by_risk["critical"]) == 1
        assert len(by_risk["high"]) >= 4
        assert len(by_risk["medium"]) >= 8
        assert len(by_risk["low"]) >= 10

    def test_all_keys_are_dotted(self):
        for key, spec in PROMPT_REGISTRY.items():
            assert spec.key == key
            assert "." in key or key == "security.input_guard", f"Key {key} should be dotted"

    def test_all_specs_have_name_and_category(self):
        for spec in PROMPT_REGISTRY.values():
            assert spec.name, f"{spec.key} missing name"
            assert spec.category, f"{spec.key} missing category"

    def test_known_keys_present(self):
        expected = {
            "planner.system", "planner.critique",
            "rag.qa", "rag.contextualize", "rag.document",
            "router.llm", "sql.generator", "sql.router",
            "memory.session.summary", "memory.trigger",
            "security.input_guard",
            "selection.panel.vote",
            "competitor.extractor",
            "business_report.polish",
        }
        missing = expected - set(PROMPT_REGISTRY.keys())
        assert not missing, f"Missing keys: {missing}"


class TestYAMLDefaultsCoverage:
    @pytest.fixture(scope="class")
    def defaults(self):
        return load_defaults()

    def test_defaults_directory_has_files(self, defaults):
        assert len(defaults) > 0

    def test_all_non_code_controlled_have_defaults(self, defaults):
        for spec in PROMPT_REGISTRY.values():
            if spec.code_controlled:
                continue
            if not spec.default_file:
                continue
            assert spec.key in defaults, f"Key {spec.key} has default_file={spec.default_file!r} but not loaded"

    def test_code_controlled_not_in_defaults(self, defaults):
        assert "security.input_guard" not in defaults


class TestYAMLVariableConsistency:
    @pytest.fixture(scope="class")
    def defaults(self):
        return load_defaults()

    def test_template_variables_match_spec(self, defaults):
        for key, template in defaults.items():
            spec = PROMPT_REGISTRY[key]
            needed = PromptRenderer.extract_variables(template)
            spec_vars = {v.name for v in spec.variables}
            assert needed == spec_vars, (
                f"{key}: template vars {sorted(needed)} != spec vars {sorted(spec_vars)}"
            )


class TestSmokeRender:
    @pytest.fixture(scope="class")
    def defaults(self):
        return load_defaults()

    def test_render_all_defaults_with_dummy_vars(self, defaults):
        for key, template in defaults.items():
            spec = PROMPT_REGISTRY[key]
            dummy_vars = {v.name: f"__{v.name}__" for v in spec.variables}
            result = PromptRenderer.render(template, dummy_vars, spec=spec)
            assert len(result) > 0, f"{key}: rendered to empty string"
            for v in spec.variables:
                assert f"__{v.name}__" in result, f"{key}: variable {{{v.name}}} not substituted"

    def test_required_substrings_preserved(self, defaults):
        for key, template in defaults.items():
            spec = PROMPT_REGISTRY[key]
            if not spec.required_substrings:
                continue
            dummy_vars = {v.name: f"__{v.name}__" for v in spec.variables}
            result = PromptRenderer.render(template, dummy_vars, spec=spec)
            for substr in spec.required_substrings:
                assert substr in result, f"{key}: missing required substring {substr!r}"
