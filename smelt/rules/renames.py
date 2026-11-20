"""Retired rule names and codes, for actionable config errors and migrations."""

import re

from smelt.config.errors import UPGRADE_GUIDE_URL
from smelt.diagnostics.legacy_codes import LEGACY_CODES

_RENAMED_RULES: dict[str, str] = {
    "complexity/constructor-logic": "complexity/excess-constructor-decisions",
    "complexity/complex-comprehension": "complexity/excess-comprehension-loops",
    "imports/package-import-fanout": "imports/excess-package-imports",
    "complexity/deep-object-navigation": "complexity/excess-attribute-depth",
    "complexity/nested-conditional-expression": "complexity/forbidden-nested-conditional",
    "modeling/stateful-dataclass": "modeling/dataclass-field-mutation",
    "layer-boundary": "architecture/layer-boundary",
    "cross-feature-import": "architecture/cross-feature-import",
    "third-party-denied": "architecture/forbidden-third-party-import",
    "import-cycle": "architecture/import-cycle",
    "shared-imports-feature": "architecture/shared-imports-feature",
    "composition-root-leak": "architecture/composition-root-leak",
    "private-module-import": "architecture/non-public-module-import",
    "package-import-fanout": "imports/excess-package-imports",
    "facade-export-gap": "architecture/missing-facade-export",
    "module-import-fanout": "imports/excess-module-imports",
    "unknown-layer": "architecture/unknown-layer",
    "forbidden-package-name": "layout/forbidden-module-name",
    "unclassified-module": "architecture/unclassified-module",
    "incomplete-coverage": "architecture/uncovered-source",
    "missing-required-module": "architecture/missing-required-module",
    "import-style": "imports/forbidden-import-style",
    "forbidden-module-docstring": "layout/module-docstring-convention",
    "forbidden-future-annotations": "typing/forbidden-future-annotations",
    "unnecessary-future-annotations": "typing/redundant-future-annotations",
    "no-private-modules": "layout/forbidden-private-module",
    "public-definitions-first": "layout/definition-order",
    "bundled-instance-assignment": "idioms/bundled-instance-assignment",
    "prefer-match-type-dispatch": "idioms/prefer-match-type-dispatch",
    "internal-public-member": "visibility/internal-public-member",
    "internal-static-helper": "layout/misplaced-static-helper",
    "redundant-str-enum-value": "modeling/redundant-str-enum-value",
    "prefer-package-import": "imports/prefer-package-import",
    "internal-public-module-symbol": "visibility/internal-public-module-symbol",
    "implicit-optional-default": "typing/implicit-optional-default",
    "deep-object-navigation": "complexity/excess-attribute-depth",
    "no-base-class-prefix": "modeling/forbidden-base-class-affix",
    "prefer-str-enum-discriminator": "modeling/prefer-str-enum-discriminator",
    "missing-module-docstring": "layout/module-docstring-convention",
    "no-base-class-suffix": "modeling/forbidden-base-class-affix",
    "secret-settings-field": "frameworks/prefer-secret-type",
    "redundant-dishka-provider": "frameworks/redundant-dishka-provider",
    "caller-before-callee": "layout/callee-before-caller",
    "package-only-dunder-all": "visibility/misplaced-dunder-all",
    "yaml-file-extension": "layout/file-name-convention",
    "compose-file-name": "layout/file-name-convention",
    "prefer-callable-instance": "idioms/prefer-callable-instance",
    "private-class-constant": "layout/misplaced-private-constant",
    "internal-public-attribute": "visibility/internal-public-attribute",
    "redundant-module-name": "layout/redundant-module-name",
    "deprecated-async-contextmanager-return": "typing/deprecated-async-contextmanager-return",
    "duplicate-literal-type": "typing/duplicate-literal-type",
    "private-symbol-import": "visibility/foreign-private-access",
    "await-in-call-argument": "complexity/inline-call-argument",
    "str-enum-auto-values": "modeling/str-enum-value-convention",
    "str-enum-explicit-values": "modeling/str-enum-value-convention",
    "redundant-private-delegate": "visibility/redundant-private-delegate",
    "redundant-name-alias": "idioms/redundant-name-alias",
    "excess-positional-parameters": "complexity/excess-positional-parameters",
    "prefer-deferred-annotations": "typing/prefer-deferred-annotations",
    "anonymous-tuple-type": "typing/anonymous-tuple-type",
    "missing-interface-docstring": "idioms/missing-interface-docstring",
    "stateful-dataclass": "modeling/dataclass-field-mutation",
    "missing-broad-except-reason": "idioms/missing-broad-except-reason",
    "missing-dataclass-option": "modeling/missing-dataclass-option",
    "duplicate-function": "complexity/duplicate-function",
    "prefer-project-base-class": "modeling/prefer-project-base-class",
    "prefer-domain-exception": "modeling/prefer-domain-exception",
    "duplicate-class-name": "modeling/duplicate-class-name",
    "duplicate-narrowing-assert": "idioms/duplicate-narrowing-assert",
    "missing-duration-unit": "modeling/implicit-duration-unit",
    "prefer-named-duration": "modeling/implicit-duration-unit",
    "nested-conditional-expression": "complexity/forbidden-nested-conditional",
    "mutable-field-in-frozen-dataclass": "modeling/mutable-field-in-frozen-dataclass",
    "parallel-record-fields": "modeling/parallel-record-fields",
    "loop-invariant-collection": "idioms/loop-invariant-collection",
    "complex-comprehension": "complexity/excess-comprehension-loops",
    "constructor-logic": "complexity/excess-constructor-decisions",
    "cross-module-private-attribute": "visibility/foreign-private-access",
    "comprehension-in-call-argument": "complexity/inline-call-argument",
    "excess-private-definitions": "visibility/excess-private-definitions",
    "test-location": "tests/misplaced-test",
    "private-test-access": "tests/private-access",
    "forbidden-test-mock": "tests/forbidden-mock",
    "string-patch-target": "tests/string-patch-target",
    "excessive-test-patches": "tests/excess-patches",
    "test-without-outcome-check": "tests/missing-outcome-check",
    "test-only-checks-possibly-empty-data": "tests/vacuous-assertion",
    "unbounded-test-wait": "tests/unbounded-wait",
    "independent-test-case-loop": "tests/independent-case-loop",
    "async-mock-without-await-check": "tests/missing-await-assertion",
    "test-only-public-function": "visibility/test-only-public-function",
    "test-only-public-attribute": "visibility/test-only-public-attribute",
    "nondeterministic-test-data": "tests/nondeterministic-data",
    "unused-suppression": "meta/unused-suppression",
    "suppression-without-reason": "meta/missing-suppression-reason",
    "resolved-debt": "meta/resolved-debt",
}

_MERGED_CODES: dict[str, str] = {
    "SMT325": "LAY002",
    "SMT326": "MOD002",
    "SMT332": "LAY007",
    "SMT342": "MOD004",
    "SMT358": "MOD010",
    "SMT365": "VIS005",
    "SMT366": "CPX002",
}


_CODES_GUIDE_URL = UPGRADE_GUIDE_URL.replace(
    "#012-to-013", "#use-the-family-rule-codes"
)
_SMT_PREFIX = re.compile(r"SMT\d*")

_INFORMATION_HIDING_RULES = (
    "visibility/internal-public-member",
    "visibility/internal-public-module-symbol",
    "visibility/internal-public-attribute",
    "visibility/test-only-public-function",
    "visibility/test-only-public-attribute",
)


def replacement_selectors(key: str) -> tuple[str, ...]:
    """Canonical replacements for a legacy selector; merged and SMT codes become codes."""
    if key.lower() == "information-hiding":
        return _INFORMATION_HIDING_RULES
    replacement = (
        _RENAMED_RULES.get(key)
        or _MERGED_CODES.get(key.upper())
        or LEGACY_CODES.get(key.upper())
    )
    return (replacement,) if replacement else ()


def retired_hint(key: str, codes: dict[str, str]) -> str:
    """Explain where a retired rule name or code went, if it did."""
    if key.lower() == "information-hiding":
        names = ", ".join(_INFORMATION_HIDING_RULES)
        return f"; the information-hiding selector was removed in 0.13.0. List {names} individually with the same severity (see {UPGRADE_GUIDE_URL})"
    if (new := _RENAMED_RULES.get(key)) is not None:
        return f'; renamed to "{new}" in 0.13.0 (see {UPGRADE_GUIDE_URL})'
    if (code := _MERGED_CODES.get(key.upper())) is not None:
        name = next(
            (name for name, value in codes.items() if value == code and name != code),
            code,
        )
        return f'; merged into {code} "{name}" in 0.13.0 (see {UPGRADE_GUIDE_URL})'
    wanted = key.strip().upper()
    if (code := LEGACY_CODES.get(wanted)) is not None:
        current = next(
            (name for name, value in codes.items() if value == code and name != code),
            None,
        )
        label = f'{code} "{current}"' if current else code
        return f"; renamed to {label} in 0.14.0; run `smelt config migrate` (see {_CODES_GUIDE_URL})"
    if _SMT_PREFIX.fullmatch(wanted):
        return f"; SMT codes were replaced by family codes such as ARC001 in 0.14.0 (see {_CODES_GUIDE_URL})"
    return ""
