# Rules

| Code | Name | Category | Default |
| --- | --- | --- | --- |
| [SMT101](SMT101.md) | layer-boundary | dependencies | error |
| [SMT102](SMT102.md) | cross-feature-import | dependencies | error |
| [SMT103](SMT103.md) | third-party-denied | dependencies | error |
| [SMT104](SMT104.md) | import-cycle | dependencies | error |
| [SMT105](SMT105.md) | shared-imports-feature | dependencies | error |
| [SMT106](SMT106.md) | composition-root-leak | dependencies | error |
| [SMT107](SMT107.md) | private-module-import | dependencies | error |
| [SMT108](SMT108.md) | package-import-fanout | dependencies | hint |
| [SMT301](SMT301.md) | unknown-layer | structure | error |
| [SMT302](SMT302.md) | forbidden-package-name | structure | error |
| [SMT305](SMT305.md) | unclassified-module | structure | warning |
| [SMT306](SMT306.md) | incomplete-coverage | structure | error |
| [SMT307](SMT307.md) | missing-required-module | structure | error |
| [SMT308](SMT308.md) | import-style | structure | error |
| [SMT309](SMT309.md) | forbidden-module-docstring | structure | error |
| [SMT310](SMT310.md) | forbidden-future-annotations | structure | error |
| [SMT311](SMT311.md) | unnecessary-future-annotations | structure | hint |
| [SMT312](SMT312.md) | no-private-modules | structure | error |
| [SMT313](SMT313.md) | definition-order | structure | error |
| [SMT314](SMT314.md) | bundled-instance-assignment | structure | error |
| [SMT315](SMT315.md) | prefer-match-type-dispatch | structure | hint |
| [SMT401](SMT401.md) | test-location | tests | error |
| [SMT420](SMT420.md) | private-test-access | tests | error |
| [SMT421](SMT421.md) | forbidden-test-mock | tests | error |
| [SMT422](SMT422.md) | string-patch-target | tests | error |
| [SMT423](SMT423.md) | excessive-test-patches | tests | error |
| [SMT424](SMT424.md) | missing-test-assertion | tests | warning |
| [SMT901](SMT901.md) | unused-suppression | meta | warning |
| [SMT902](SMT902.md) | suppression-without-reason | meta | error |
| [SMT903](SMT903.md) | resolved-debt | meta | warning |

`smelt rules` prints the same table in the terminal.
