# Rules

| Code | Name | Category | Default |
| --- | --- | --- | --- |
| [SMT101](SMT101.md) | layer-boundary | dependencies | error |
| [SMT102](SMT102.md) | cross-feature-import | dependencies | error |
| [SMT103](SMT103.md) | third-party-denied | dependencies | error |
| [SMT104](SMT104.md) | import-cycle | dependencies | error |
| [SMT105](SMT105.md) | shared-imports-feature | dependencies | error |
| [SMT106](SMT106.md) | composition-root-leak | dependencies | error |
| [SMT201](SMT201.md) | concrete-construction | code | error |
| [SMT202](SMT202.md) | concrete-dependency | code | warning |
| [SMT203](SMT203.md) | forbidden-base-class | code | error |
| [SMT204](SMT204.md) | misplaced-role | code | error |
| [SMT205](SMT205.md) | container-usage | code | error |
| [SMT206](SMT206.md) | self-class-reference | code | off |
| [SMT301](SMT301.md) | unknown-layer | structure | error |
| [SMT302](SMT302.md) | forbidden-package-name | structure | error |
| [SMT303](SMT303.md) | role-file | structure | error |
| [SMT304](SMT304.md) | crowded-package | structure | hint |
| [SMT305](SMT305.md) | unclassified-module | structure | hint |
| [SMT401](SMT401.md) | test-location | tests | error |
| [SMT402](SMT402.md) | patches-internal | tests | error |
| [SMT403](SMT403.md) | private-access | tests | error |
| [SMT404](SMT404.md) | mocks-first-party | tests | warning |
| [SMT405](SMT405.md) | too-many-mocks | tests | warning |
| [SMT406](SMT406.md) | interaction-assertion | tests | warning |
| [SMT407](SMT407.md) | test-bloat | tests | hint |
| [SMT408](SMT408.md) | test-only-api | tests | off |
| [SMT901](SMT901.md) | unused-suppression | meta | warning |
| [SMT902](SMT902.md) | suppression-without-reason | meta | error |
| [SMT903](SMT903.md) | resolved-debt | meta | warning |

`smelt rules` prints the same table in the terminal.
