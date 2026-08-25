#!/usr/bin/env -S uv run
"""Validate the generated example input documents of _input_schema.rego.

Usage (from backend/, so the project venv with opa_bindings is used):
    uv run python ../documentation/configuration/policies/check_input_schema.py
        -> validates every example against the Rego contract (valid_input)
           AND the Pydantic contract (userdefinedmodel.policy_input), listing them
    uv run python ../documentation/configuration/policies/check_input_schema.py <index>
        -> prints example <index> as JSON to stdout

Indexes are stable: examples are sorted by (action, canonical JSON).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import opa_bindings

POLICY_DIR = Path(__file__).resolve().parent
BACKEND_DIR = POLICY_DIR.parent.parent.parent / "backend"
PKG = "udm.udmframeworkv1.input_schema"

sys.path.insert(0, str(BACKEND_DIR))
from userdefinedmodel.policy_input import validate_policy_input  # noqa: E402


def build_engine() -> opa_bindings.OpaEngine:
    eng = opa_bindings.OpaEngine()
    for name in ("_input_schema.rego", "_template.rego"):
        eng.add_policy(name, (POLICY_DIR / name).read_text())
    return eng


def load_examples(eng: opa_bindings.OpaEngine) -> list[dict]:
    examples = eng.eval_document(f"{PKG}.example_inputs")
    return sorted(examples, key=lambda d: (d["action"], json.dumps(d, sort_keys=True)))


def eval_bool(eng: opa_bindings.OpaEngine, path: str, doc: dict | None = None) -> bool:
    """valid_input/examples_valid have no `default false`, so a failing check
    leaves the rule undefined rather than false."""
    try:
        return eng.eval_document(path, doc) is True
    except opa_bindings.OpaUndefinedError:
        return False


def describe(doc: dict) -> str:
    entity = doc.get("entity") or {}
    fields = entity.get("fields") or {}
    title = fields.get("title") or {}
    parts = [
        f"action={doc['action']}",
        f"children={sorted((entity.get('children') or {}).keys()) or '-'}",
        f"title={'localized' if title.get('localized') else ('unset' if title.get('value') is None else 'plain')}" if title else "title=-",
    ]
    if "transition_descriptor" in doc:
        parts.append(f"descriptor.from_state={doc['transition_descriptor']['from_state']}")
    if "candidate_transitions" in doc:
        d = doc["candidate_transitions"]["root-1"]["status"]["transitions"]["submit"]
        parts.append(f"descriptor.from_state={d['from_state']}")
    return " ".join(parts)


def main() -> int:
    eng = build_engine()
    examples = load_examples(eng)

    if len(sys.argv) > 1:
        index = int(sys.argv[1])
        if not 0 <= index < len(examples):
            print(f"index {index} out of range 0..{len(examples) - 1}", file=sys.stderr)
            return 2
        json.dump(examples[index], sys.stdout, indent=2, sort_keys=True)
        print()
        return 0

    # Aggregate self-check inside Rego...
    aggregate_ok = eval_bool(eng, f"{PKG}.examples_valid")

    # ...and each example individually against BOTH contracts, so a failure
    # names the document and the side (rego / pydantic) that rejected it.
    failures = []
    for i, doc in enumerate(examples):
        problems = []
        if not eval_bool(eng, f"{PKG}.valid_input", doc):
            problems.append("rego")
        try:
            validate_policy_input(doc)
        except Exception as exc:
            problems.append(f"pydantic: {exc}")
        if problems:
            failures.append(i)
        status = "ok  " if not problems else "FAIL"
        print(f"[{i:3}] {status} {describe(doc)}" + ("".join(f"\n      {p}" for p in problems)))

    print(f"\n{len(examples)} examples, examples_valid={aggregate_ok}, failures={failures or 'none'}")
    return 0 if aggregate_ok and not failures else 1


if __name__ == "__main__":
    sys.exit(main())
