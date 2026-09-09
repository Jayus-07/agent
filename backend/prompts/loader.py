"""Prompt defaults loader — reads YAML files, validates, seeds DB.

load_defaults()  → dict[key, template_text]  (for in-process fallback)
seed()           → upserts into DB via PromptService.seed_defaults()
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from backend.prompts.registry import PROMPT_REGISTRY, PromptSpec
from backend.prompts.renderer import PromptRenderer
from backend.shared.logger import logger

_DEFAULTS_DIR = Path(__file__).parent / "defaults"


def _parse_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML structure in {path.name}")
    return data


def _validate_entry(data: dict, spec: PromptSpec) -> list[str]:
    errors: list[str] = []
    key = data.get("key", "<missing>")
    if key != spec.key:
        errors.append(f"{key}: file key '{key}' != registry key '{spec.key}'")

    template = data.get("template")
    if not template or not isinstance(template, str):
        errors.append(f"{key}: missing or empty 'template' field")
        return errors

    needed = PromptRenderer.extract_variables(template)
    spec_vars = {v.name for v in spec.variables}

    unknown = needed - spec_vars
    if unknown:
        errors.append(f"{key}: variables in template but not spec: {sorted(unknown)}")

    missing = spec_vars - needed
    if missing:
        errors.append(f"{key}: variables in spec but not template: {sorted(missing)}")

    return errors


def load_defaults() -> dict[str, str]:
    """Parse all YAML defaults → {key: template_text}. Validates against registry."""
    defaults: dict[str, str] = {}
    warnings: list[str] = []

    if not _DEFAULTS_DIR.is_dir():
        logger.warning(f"[PromptLoader] Defaults directory not found: {_DEFAULTS_DIR}")
        return defaults

    for path in sorted(_DEFAULTS_DIR.glob("*.yaml")):
        try:
            data = _parse_yaml(path)
        except Exception as exc:
            warnings.append(f"{path.name}: parse error: {exc}")
            continue

        key = data.get("key")
        if not key:
            warnings.append(f"{path.name}: missing 'key' field, skipping")
            continue

        spec = PROMPT_REGISTRY.get(key)
        if not spec:
            warnings.append(f"{path.name}: key '{key}' not in registry, skipping")
            continue

        errs = _validate_entry(data, spec)
        if errs:
            warnings.extend(errs)
            continue

        defaults[key] = data["template"]

    if warnings:
        for w in warnings:
            logger.warning(f"[PromptLoader] {w}")

    logger.info(f"[PromptLoader] Loaded {len(defaults)} defaults from {_DEFAULTS_DIR}")
    return defaults


def load_defaults_raw() -> dict[str, dict[str, Any]]:
    """Parse all YAML defaults → {key: full_dict} (includes metadata, not just template)."""
    result: dict[str, dict[str, Any]] = {}
    if not _DEFAULTS_DIR.is_dir():
        return result
    for path in sorted(_DEFAULTS_DIR.glob("*.yaml")):
        try:
            data = _parse_yaml(path)
            key = data.get("key")
            if key:
                result[key] = data
        except Exception:
            continue
    return result


async def seed() -> dict:
    """Seed DB with YAML defaults via PromptService. Idempotent."""
    from backend.prompts.service import prompt_service

    defaults = load_defaults()
    prompt_service.load_defaults_into_memory(defaults)
    result = await prompt_service.seed_defaults(defaults)
    logger.info(f"[PromptLoader] Seed complete: {result}")
    return result
