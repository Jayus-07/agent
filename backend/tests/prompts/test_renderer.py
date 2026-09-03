"""PromptRenderer unit tests (pure, no DB).

Covers:
  - Variable extraction (including {{ }} escapes)
  - Missing variable → PromptRenderError
  - Unknown variable rejection for code-controlled prompts
  - required_substrings validation
  - validate() spec vs template mismatch detection
"""
import pytest

from backend.prompts.registry import PromptSpec, VarSpec
from backend.prompts.renderer import PromptRenderer, PromptRenderError


class TestExtractVariables:
    def test_simple(self):
        assert PromptRenderer.extract_variables("Hello {name}") == {"name"}

    def test_multiple(self):
        assert PromptRenderer.extract_variables("{a} and {b}") == {"a", "b"}

    def test_escaped_braces_ignored(self):
        assert PromptRenderer.extract_variables("{{literal}} and {var}") == {"var"}

    def test_no_variables(self):
        assert PromptRenderer.extract_variables("No vars here") == set()

    def test_empty_string(self):
        assert PromptRenderer.extract_variables("") == set()

    def test_duplicate_vars_deduped(self):
        assert PromptRenderer.extract_variables("{x} and {x}") == {"x"}


class TestRender:
    def test_basic_render(self):
        result = PromptRenderer.render("Hello {name}!", {"name": "World"})
        assert result == "Hello World!"

    def test_missing_var_raises(self):
        with pytest.raises(PromptRenderError, match="Missing required"):
            PromptRenderer.render("Hello {name}!", {})

    def test_missing_var_non_strict(self):
        result = PromptRenderer.render("Hello {name}!", {}, strict=False)
        assert result == "Hello !"

    def test_extra_vars_ok_for_non_code_controlled(self):
        spec = PromptSpec(key="test", name="t", category="t", risk_level="low")
        result = PromptRenderer.render("{a}", {"a": "x", "b": "y"}, spec=spec)
        assert result == "x"

    def test_extra_vars_rejected_for_code_controlled(self):
        spec = PromptSpec(key="test", name="t", category="t", risk_level="critical", code_controlled=True)
        with pytest.raises(PromptRenderError, match="Unknown variables"):
            PromptRenderer.render("{a}", {"a": "x", "b": "y"}, spec=spec)

    def test_escaped_braces_rendered_correctly(self):
        result = PromptRenderer.render("{{literal}} and {var}", {"var": "val"})
        assert result == "{literal} and val"

    def test_required_substrings_pass(self):
        spec = PromptSpec(
            key="test", name="t", category="t", risk_level="low",
            variables=(VarSpec("input"),),
            required_substrings=("<!--META",),
        )
        result = PromptRenderer.render("<!--META--> Answer: {input}", {"input": "hi"}, spec=spec)
        assert "<!--META" in result

    def test_required_substrings_fail(self):
        spec = PromptSpec(
            key="test", name="t", category="t", risk_level="low",
            variables=(VarSpec("input"),),
            required_substrings=("<!--META",),
        )
        with pytest.raises(PromptRenderError, match="missing required substring"):
            PromptRenderer.render("Just {input}", {"input": "hi"}, spec=spec)


class TestValidate:
    def test_perfect_match(self):
        spec = PromptSpec(
            key="test", name="t", category="t", risk_level="low",
            variables=(VarSpec("a"), VarSpec("b")),
        )
        errors = PromptRenderer.validate("Hello {a} and {b}", spec)
        assert errors == []

    def test_missing_in_template(self):
        spec = PromptSpec(
            key="test", name="t", category="t", risk_level="low",
            variables=(VarSpec("a"), VarSpec("b")),
        )
        errors = PromptRenderer.validate("Hello {a}", spec)
        assert len(errors) == 1
        assert "b" in errors[0]

    def test_unknown_in_template(self):
        spec = PromptSpec(
            key="test", name="t", category="t", risk_level="low",
            variables=(VarSpec("a"),),
        )
        errors = PromptRenderer.validate("Hello {a} and {b}", spec)
        assert len(errors) == 1
        assert "b" in errors[0]

    def test_both_mismatch(self):
        spec = PromptSpec(
            key="test", name="t", category="t", risk_level="low",
            variables=(VarSpec("a"), VarSpec("b")),
        )
        errors = PromptRenderer.validate("{a} {c}", spec)
        assert len(errors) == 2
