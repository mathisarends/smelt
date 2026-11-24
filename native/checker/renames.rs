//! Retired rule names and codes, like `smelt.rules.renames`.
use super::config::errors::upgrade_guide_url;
use std::collections::HashMap;

/// Rule names renamed in 0.13.0.
const RENAMED_RULES: [(&str, &str); 97] = [
    (
        "complexity/constructor-logic",
        "complexity/excess-constructor-decisions",
    ),
    (
        "complexity/complex-comprehension",
        "complexity/excess-comprehension-loops",
    ),
    (
        "imports/package-import-fanout",
        "imports/excess-package-imports",
    ),
    (
        "complexity/deep-object-navigation",
        "complexity/excess-attribute-depth",
    ),
    (
        "complexity/nested-conditional-expression",
        "complexity/forbidden-nested-conditional",
    ),
    (
        "modeling/stateful-dataclass",
        "modeling/dataclass-field-mutation",
    ),
    ("layer-boundary", "architecture/layer-boundary"),
    ("cross-feature-import", "architecture/cross-feature-import"),
    (
        "third-party-denied",
        "architecture/forbidden-third-party-import",
    ),
    ("import-cycle", "architecture/import-cycle"),
    (
        "shared-imports-feature",
        "architecture/shared-imports-feature",
    ),
    (
        "composition-root-leak",
        "architecture/composition-root-leak",
    ),
    (
        "private-module-import",
        "architecture/non-public-module-import",
    ),
    ("package-import-fanout", "imports/excess-package-imports"),
    ("facade-export-gap", "architecture/missing-facade-export"),
    ("module-import-fanout", "imports/excess-module-imports"),
    ("unknown-layer", "architecture/unknown-layer"),
    ("forbidden-package-name", "layout/forbidden-module-name"),
    ("unclassified-module", "architecture/unclassified-module"),
    ("incomplete-coverage", "architecture/uncovered-source"),
    (
        "missing-required-module",
        "architecture/missing-required-module",
    ),
    ("import-style", "imports/forbidden-import-style"),
    (
        "forbidden-module-docstring",
        "layout/module-docstring-convention",
    ),
    (
        "forbidden-future-annotations",
        "typing/forbidden-future-annotations",
    ),
    (
        "unnecessary-future-annotations",
        "typing/redundant-future-annotations",
    ),
    ("no-private-modules", "layout/forbidden-private-module"),
    ("public-definitions-first", "layout/definition-order"),
    (
        "bundled-instance-assignment",
        "idioms/bundled-instance-assignment",
    ),
    (
        "prefer-match-type-dispatch",
        "idioms/prefer-match-type-dispatch",
    ),
    (
        "internal-public-member",
        "visibility/internal-public-member",
    ),
    ("internal-static-helper", "layout/misplaced-static-helper"),
    (
        "redundant-str-enum-value",
        "modeling/redundant-str-enum-value",
    ),
    ("prefer-package-import", "imports/prefer-package-import"),
    (
        "internal-public-module-symbol",
        "visibility/internal-public-module-symbol",
    ),
    (
        "implicit-optional-default",
        "typing/implicit-optional-default",
    ),
    (
        "deep-object-navigation",
        "complexity/excess-attribute-depth",
    ),
    (
        "no-base-class-prefix",
        "modeling/forbidden-base-class-affix",
    ),
    (
        "prefer-str-enum-discriminator",
        "modeling/prefer-str-enum-discriminator",
    ),
    (
        "missing-module-docstring",
        "layout/module-docstring-convention",
    ),
    (
        "no-base-class-suffix",
        "modeling/forbidden-base-class-affix",
    ),
    ("secret-settings-field", "frameworks/prefer-secret-type"),
    (
        "redundant-dishka-provider",
        "frameworks/redundant-dishka-provider",
    ),
    ("caller-before-callee", "layout/callee-before-caller"),
    ("package-only-dunder-all", "visibility/misplaced-dunder-all"),
    ("yaml-file-extension", "layout/file-name-convention"),
    ("compose-file-name", "layout/file-name-convention"),
    (
        "prefer-callable-instance",
        "idioms/prefer-callable-instance",
    ),
    (
        "private-class-constant",
        "layout/misplaced-private-constant",
    ),
    (
        "internal-public-attribute",
        "visibility/internal-public-attribute",
    ),
    ("redundant-module-name", "layout/redundant-module-name"),
    (
        "deprecated-async-contextmanager-return",
        "typing/deprecated-async-contextmanager-return",
    ),
    ("duplicate-literal-type", "typing/duplicate-literal-type"),
    ("private-symbol-import", "visibility/foreign-private-access"),
    ("await-in-call-argument", "complexity/inline-call-argument"),
    ("str-enum-auto-values", "modeling/str-enum-value-convention"),
    (
        "str-enum-explicit-values",
        "modeling/str-enum-value-convention",
    ),
    (
        "redundant-private-delegate",
        "visibility/redundant-private-delegate",
    ),
    ("redundant-name-alias", "idioms/redundant-name-alias"),
    (
        "excess-positional-parameters",
        "complexity/excess-positional-parameters",
    ),
    (
        "prefer-deferred-annotations",
        "typing/prefer-deferred-annotations",
    ),
    ("anonymous-tuple-type", "typing/anonymous-tuple-type"),
    (
        "missing-interface-docstring",
        "idioms/missing-interface-docstring",
    ),
    ("stateful-dataclass", "modeling/dataclass-field-mutation"),
    (
        "missing-broad-except-reason",
        "idioms/missing-broad-except-reason",
    ),
    (
        "missing-dataclass-option",
        "modeling/missing-dataclass-option",
    ),
    ("duplicate-function", "complexity/duplicate-function"),
    (
        "prefer-project-base-class",
        "modeling/prefer-project-base-class",
    ),
    (
        "prefer-domain-exception",
        "modeling/prefer-domain-exception",
    ),
    ("duplicate-class-name", "modeling/duplicate-class-name"),
    (
        "duplicate-narrowing-assert",
        "idioms/duplicate-narrowing-assert",
    ),
    ("missing-duration-unit", "modeling/implicit-duration-unit"),
    ("prefer-named-duration", "modeling/implicit-duration-unit"),
    (
        "nested-conditional-expression",
        "complexity/forbidden-nested-conditional",
    ),
    (
        "mutable-field-in-frozen-dataclass",
        "modeling/mutable-field-in-frozen-dataclass",
    ),
    ("parallel-record-fields", "modeling/parallel-record-fields"),
    (
        "loop-invariant-collection",
        "idioms/loop-invariant-collection",
    ),
    (
        "complex-comprehension",
        "complexity/excess-comprehension-loops",
    ),
    (
        "constructor-logic",
        "complexity/excess-constructor-decisions",
    ),
    (
        "cross-module-private-attribute",
        "visibility/foreign-private-access",
    ),
    (
        "comprehension-in-call-argument",
        "complexity/inline-call-argument",
    ),
    (
        "excess-private-definitions",
        "visibility/excess-private-definitions",
    ),
    ("test-location", "tests/misplaced-test"),
    ("private-test-access", "tests/private-access"),
    ("forbidden-test-mock", "tests/forbidden-mock"),
    ("string-patch-target", "tests/string-patch-target"),
    ("excessive-test-patches", "tests/excess-patches"),
    ("test-without-outcome-check", "tests/missing-outcome-check"),
    (
        "test-only-checks-possibly-empty-data",
        "tests/vacuous-assertion",
    ),
    ("unbounded-test-wait", "tests/unbounded-wait"),
    ("independent-test-case-loop", "tests/independent-case-loop"),
    (
        "async-mock-without-await-check",
        "tests/missing-await-assertion",
    ),
    (
        "test-only-public-function",
        "visibility/test-only-public-function",
    ),
    (
        "test-only-public-attribute",
        "visibility/test-only-public-attribute",
    ),
    ("nondeterministic-test-data", "tests/nondeterministic-data"),
    ("unused-suppression", "meta/unused-suppression"),
    (
        "suppression-without-reason",
        "meta/missing-suppression-reason",
    ),
    ("resolved-debt", "meta/resolved-debt"),
];

/// SMT codes merged into another rule in 0.13.0.
const MERGED_CODES: [(&str, &str); 7] = [
    ("SMT325", "LAY002"),
    ("SMT326", "MOD002"),
    ("SMT332", "LAY007"),
    ("SMT342", "MOD004"),
    ("SMT358", "MOD010"),
    ("SMT365", "VIS005"),
    ("SMT366", "CPX002"),
];

/// SMT codes and the family codes that replaced them in 0.14.0.
const LEGACY_CODES: [(&str, &str); 91] = [
    ("SMT101", "ARC001"),
    ("SMT102", "ARC002"),
    ("SMT103", "ARC003"),
    ("SMT104", "ARC004"),
    ("SMT105", "ARC005"),
    ("SMT106", "ARC006"),
    ("SMT107", "ARC007"),
    ("SMT108", "IMP001"),
    ("SMT109", "ARC008"),
    ("SMT110", "IMP002"),
    ("SMT111", "ARC009"),
    ("SMT112", "ARC010"),
    ("SMT301", "ARC011"),
    ("SMT302", "LAY001"),
    ("SMT305", "ARC012"),
    ("SMT306", "ARC013"),
    ("SMT307", "ARC014"),
    ("SMT308", "IMP003"),
    ("SMT309", "LAY002"),
    ("SMT310", "TYP001"),
    ("SMT311", "TYP002"),
    ("SMT312", "LAY003"),
    ("SMT313", "LAY004"),
    ("SMT314", "IDM001"),
    ("SMT315", "IDM002"),
    ("SMT316", "VIS001"),
    ("SMT317", "LAY005"),
    ("SMT318", "MOD001"),
    ("SMT319", "IMP004"),
    ("SMT320", "VIS002"),
    ("SMT321", "TYP003"),
    ("SMT322", "CPX001"),
    ("SMT323", "MOD002"),
    ("SMT324", "MOD003"),
    ("SMT327", "FWK001"),
    ("SMT328", "FWK002"),
    ("SMT329", "LAY006"),
    ("SMT330", "VIS003"),
    ("SMT331", "LAY007"),
    ("SMT333", "IDM003"),
    ("SMT334", "LAY008"),
    ("SMT335", "VIS004"),
    ("SMT336", "LAY009"),
    ("SMT337", "TYP004"),
    ("SMT338", "TYP005"),
    ("SMT339", "VIS005"),
    ("SMT340", "CPX002"),
    ("SMT341", "MOD004"),
    ("SMT343", "VIS006"),
    ("SMT344", "IDM004"),
    ("SMT345", "CPX003"),
    ("SMT346", "TYP006"),
    ("SMT347", "TYP007"),
    ("SMT348", "IDM005"),
    ("SMT349", "MOD005"),
    ("SMT350", "IDM006"),
    ("SMT351", "MOD006"),
    ("SMT352", "CPX004"),
    ("SMT353", "MOD007"),
    ("SMT354", "MOD008"),
    ("SMT355", "MOD009"),
    ("SMT356", "IDM007"),
    ("SMT357", "MOD010"),
    ("SMT359", "CPX005"),
    ("SMT360", "MOD011"),
    ("SMT361", "MOD012"),
    ("SMT362", "IDM008"),
    ("SMT363", "CPX006"),
    ("SMT364", "CPX007"),
    ("SMT367", "VIS007"),
    ("SMT368", "LAY010"),
    ("SMT369", "VIS008"),
    ("SMT370", "LAY011"),
    ("SMT371", "TYP008"),
    ("SMT401", "TST001"),
    ("SMT420", "TST002"),
    ("SMT421", "TST003"),
    ("SMT422", "TST004"),
    ("SMT423", "TST005"),
    ("SMT424", "TST006"),
    ("SMT425", "TST007"),
    ("SMT426", "TST008"),
    ("SMT427", "TST009"),
    ("SMT428", "TST010"),
    ("SMT429", "VIS009"),
    ("SMT430", "VIS010"),
    ("SMT431", "TST011"),
    ("SMT901", "MET001"),
    ("SMT902", "MET002"),
    ("SMT903", "MET003"),
    ("SMT904", "MET004"),
];

/// The rules the removed `information-hiding` selector stood for.
const INFORMATION_HIDING_RULES: [&str; 5] = [
    "visibility/internal-public-member",
    "visibility/internal-public-module-symbol",
    "visibility/internal-public-attribute",
    "visibility/test-only-public-function",
    "visibility/test-only-public-attribute",
];

fn lookup<'t>(entries: &'t [(&'t str, &'t str)], key: &str) -> Option<&'t str> {
    entries
        .iter()
        .find(|(retired, _)| *retired == key)
        .map(|(_, current)| *current)
}

/// The current selectors replacing a retired name or code; empty when it never existed.
pub fn replacement_selectors(key: &str) -> Vec<&'static str> {
    if key.to_lowercase() == "information-hiding" {
        return INFORMATION_HIDING_RULES.to_vec();
    }
    let upper = key.to_uppercase();
    lookup(&RENAMED_RULES, key)
        .or_else(|| lookup(&MERGED_CODES, &upper))
        .or_else(|| lookup(&LEGACY_CODES, &upper))
        .into_iter()
        .collect()
}

/// Where a retired rule name or code went, if it did.
pub fn retired_hint(key: &str, names: &HashMap<&str, &str>) -> String {
    let guide = upgrade_guide_url();
    if key.to_lowercase() == "information-hiding" {
        return format!(
            "; the information-hiding selector was removed in 0.13.0. List {} individually with \
             the same severity (see {guide})",
            INFORMATION_HIDING_RULES.join(", ")
        );
    }
    if let Some(current) = lookup(&RENAMED_RULES, key) {
        return format!("; renamed to \"{current}\" in 0.13.0 (see {guide})");
    }
    let name_of = |code: &str| {
        names
            .iter()
            .find(|(name, value)| **value == code && **name != code)
            .map(|(name, _)| *name)
    };
    if let Some(code) = lookup(&MERGED_CODES, &key.to_uppercase()) {
        let name = name_of(code).unwrap_or(code);
        return format!("; merged into {code} \"{name}\" in 0.13.0 (see {guide})");
    }
    let codes_guide = format!(
        "{}#use-the-family-rule-codes",
        guide.split('#').next().unwrap_or(&guide)
    );
    let wanted = key.trim().to_uppercase();
    if let Some(code) = lookup(&LEGACY_CODES, &wanted) {
        let label =
            name_of(code).map_or_else(|| code.to_owned(), |name| format!("{code} \"{name}\""));
        return format!(
            "; renamed to {label} in 0.14.0; run `smelt config migrate` (see {codes_guide})"
        );
    }
    let digits = wanted.strip_prefix("SMT");
    if digits.is_some_and(|rest| rest.chars().all(|character| character.is_ascii_digit())) {
        return format!(
            "; SMT codes were replaced by family codes such as ARC001 in 0.14.0 (see {codes_guide})"
        );
    }
    String::new()
}
