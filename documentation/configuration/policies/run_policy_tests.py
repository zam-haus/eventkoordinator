#!/usr/bin/env -S uv run
"""Run the test_udm.rego fixture suite (package udm_test) against the policy set.

Usage (from backend/, so the project venv with opa_bindings is used):
    uv run python ../documentation/configuration/policies/run_policy_tests.py
        -> runs every test_* rule in test_udm.rego, prints pass/fail per test
           and a summary; exit 0 only if all pass.

There is no OPA-style `opa test` runner in opa_bindings, so this plays that
role directly: every test_* rule in udm_test must evaluate to exactly `true`
(with no input — fixtures are embedded in test_udm.rego itself) to pass.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import opa_bindings

POLICY_DIR = Path(__file__).resolve().parent
TEST_FILE = POLICY_DIR / "test_udm.rego"

# test_udm.rego exercises the Proposal UDM type's own policy set (shared
# framework/aggregator/sudo modules + the udmframeworkv1.modules.* files
# Proposal actually attaches). Excluded here because they'd otherwise load
# into the SAME udmframeworkv1.modules.* namespace and contaminate the
# aggregate `allow`/`error_messages` rules udm.rego computes across all
# loaded modules regardless of which UDM type they belong to:
#   - _template.rego / _input_schema.rego: contract drafts, not a real type's
#     policy (see documentation/rego-engine-review.md).
#   - event.rego: a SEPARATE demo UDM type's policy (see its own header
#     comment) — its `allow if { input.action in {"view","browse"};
#     input.user.is_active }` is unconditional for any active user and was
#     silently making every "stranger can't view" style test pass for the
#     wrong reason when loaded alongside Proposal's modules.
_EXCLUDED = {"_template.rego", "_input_schema.rego", "event.rego", "test_udm.rego"}

_TEST_RULE_RE = re.compile(r"^(test_\w+)\b", re.MULTILINE)


def build_engine() -> opa_bindings.OpaEngine:
    eng = opa_bindings.OpaEngine()
    # Quiet by default -- policies under test print liberally; pass -v to see it.
    if "-v" not in sys.argv and "--verbose" not in sys.argv:
        eng.print_handler = lambda message, location: None
    for path in sorted(POLICY_DIR.glob("*.rego")):
        if path.name in _EXCLUDED:
            continue
        eng.add_policy(path.name, path.read_text())
    eng.add_policy(TEST_FILE.name, TEST_FILE.read_text())
    return eng


def discover_test_names() -> list[str]:
    return _TEST_RULE_RE.findall(TEST_FILE.read_text())


def main() -> int:
    eng = build_engine()
    names = discover_test_names()

    failures = []
    for name in names:
        try:
            passed = eng.eval_document(f"udm_test.{name}") is True
        except opa_bindings.OpaUndefinedError:
            passed = False
        except opa_bindings.OpaError as exc:
            passed = False
            print(f"[FAIL] {name}\n       error: {exc}")
            failures.append(name)
            continue
        print(f"[{'ok  ' if passed else 'FAIL'}] {name}")
        if not passed:
            failures.append(name)

    print(f"\n{len(names)} tests, failures={failures or 'none'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
