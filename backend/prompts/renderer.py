"""PromptRenderer — template rendering with strict variable validation.

Uses str.format() semantics (not Jinja2) to match all 27 existing prompts.
{{ }} escapes are handled natively by string.Formatter.
"""
from dataclasses import dataclass
from string import Formatter

from backend.prompts.registry import PromptSpec


class PromptRenderError(Exception):
    """Raised when a prompt cannot be rendered (missing required vars, contract violation)."""


@dataclass(frozen=True)
class RenderResult:
    text: str
    key: str
    version: int | None
    source: str  # "db" | "snapshot" | "default"


class PromptRenderer:
    @staticmethod
    def extract_variables(template: str) -> set[str]:
        names = set()
        for _, name, _, _ in Formatter().parse(template):
            if name is not None and name != "":
                names.add(name)
        return names

    @staticmethod
    def render(
        template: str,
        variables: dict[str, str],
        *,
        spec: PromptSpec | None = None,
        strict: bool = True,
    ) -> str:
        needed = PromptRenderer.extract_variables(template)
        provided = set(variables.keys())

        if strict:
            missing = needed - provided
            if missing:
                raise PromptRenderError(
                    f"Missing required variables: {sorted(missing)}"
                )

        extra = provided - needed
        if extra and strict and spec and spec.code_controlled:
            raise PromptRenderError(
                f"Unknown variables for code-controlled prompt: {sorted(extra)}"
            )

        safe_vars = {k: variables.get(k, "") for k in needed}
        try:
            result = template.format(**safe_vars)
        except KeyError as exc:
            raise PromptRenderError(f"Template references undefined variable: {exc}") from exc

        if spec and spec.required_substrings:
            for substr in spec.required_substrings:
                if substr not in result:
                    raise PromptRenderError(
                        f"Rendered output missing required substring: {substr!r}"
                    )

        return result

    @staticmethod
    def validate(
        template: str,
        spec: PromptSpec,
    ) -> list[str]:
        errors: list[str] = []
        needed = PromptRenderer.extract_variables(template)
        spec_vars = {v.name for v in spec.variables}

        missing_from_spec = spec_vars - needed
        if missing_from_spec:
            errors.append(
                f"Variables declared in spec but not in template: {sorted(missing_from_spec)}"
            )

        unknown_in_template = needed - spec_vars
        if unknown_in_template:
            errors.append(
                f"Variables in template but not declared in spec: {sorted(unknown_in_template)}"
            )

        return errors
