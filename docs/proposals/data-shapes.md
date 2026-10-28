# Proposal: data shapes at architectural boundaries

Status: design only; no new rules or configuration keys implemented.

## Decision

Distinguish runtime validation, representation preferences and maintainability
hints. A TypedDict is a legitimate representation, including at a boundary when
paired with validation. Pydantic BaseModel, TypeAdapter, dataclasses, Literal and
Enum solve different parts of the problem. None should become a universal default
based solely on the spelling of an annotation or the name of a layer.

The useful question is whether incoming data has been checked against the intended
contract. A cast is not validation. Conversely, seeing a dictionary does not prove
that validation is missing, and seeing a BaseModel does not prove that validation
occurred: model_construct() deliberately bypasses validation.

## Existing coverage

SMT103 already supports a layer's third-party dependency policy. A team that wants
Pydantic outside its domain can use the following fragment inside its existing
architecture.layers configuration:

```yaml
domain:
  path: domain
  may_depend_on: []
  third_party:
    default: allow
    deny: [pydantic, pydantic.*]
```

This checks dependency direction, not validation. It does not prohibit importing
an internal DTO that depends on Pydantic elsewhere, or prove a domain is independent
of every framework. Do not introduce a second forbid-pydantic-in setting.

## Candidate catalog entries

Codes are intentionally unallocated until implementation; consult the live catalog
and concurrent PRs. All new policies should be opt-in package contracts in the
existing YAML configuration. No parallel TOML namespace is proposed.

| Candidate | Meaning | Initial diagnostic |
| --- | --- | --- |
| typed-dict-policy | A package forbids TypedDict definitions as a representation convention | Error under an explicit forbid policy; allow override |
| unchecked-payload-cast | A recognized raw decoder feeds a cast to a resolved TypedDict | Warning to review validation; not proof of an exploitable input |
| public-unstructured-mapping | A selected public parameter/return annotation uses dict[str, Any] or an explicitly supported equivalent | Opt-in convention; identify the exact annotation |
| repeated-string-discriminator | One resolved binding is compared with a configurable number of distinct string values | Hint to review a closed value set; no automatic Enum conversion |

TypedDict and stringly typed code are separate concepts. A TypedDict field can use
a closed Literal/Enum, while a BaseModel field can remain an unrestricted str.

## Validation example and counterexample

Potentially unchecked input:

```python
from json import loads
from typing import cast
from typing_extensions import TypedDict

class UserPayload(TypedDict):
    email: str

def decode(raw: str) -> UserPayload:
    return cast(UserPayload, loads(raw))
```

Valid dictionary representation with runtime validation:

```python
from pydantic import TypeAdapter
from typing_extensions import TypedDict

class UserPayload(TypedDict):
    email: str

USER_PAYLOAD = TypeAdapter(UserPayload)

def decode(raw: str) -> UserPayload:
    return USER_PAYLOAD.validate_json(raw)
```

A validated result must not trigger unchecked-payload-cast. A separate explicit
TypedDict representation ban can still reject the definition. The diagnostic must
explain that distinction and suggest a model only as the team's chosen convention.

## Analysis requirements before implementation

Use a reusable analysis component for binding provenance, supported type shapes
and source locations. Rules compare those facts with package policy. The current
SyntaxIndex.bindings collects imports across an entire AST; it is not a sufficient
resolver for these rules because lexical shadowing and rebinding matter.

The first reliable payload-cast implementation should be narrow: direct calls to
resolved json.loads/json.load followed by typing.cast or typing_extensions.cast
to a resolved TypedDict. Support class and functional TypedDict declarations,
aliases and inherited declarations deliberately, with cycle protection. Unresolved
types should not be guessed from a class name ending in Payload.

Do not recognize arbitrary .json() methods as external input by name alone. A local
method can already validate. Framework/SDK adapters need resolved receiver evidence
or explicit project configuration. Each additional input source needs its own
valid counterexample and documented confidence limits.

Do not claim whole-program validation or taint tracking. Multi-statement flows,
custom validators and validation in wrappers require provenance analysis beyond a
direct syntax match. Initially skip unsupported flows and document that coverage.
No source execution, model instantiation or automatic schema/DTO rewriting.

## Convention boundaries

A TypedDict definition ban should resolve typing/typing_extensions imports, aliases
and supported re-exports. Detect both class inheritance and the functional factory;
exclude unrelated user classes/functions named TypedDict. Definition and public API
usage are distinct scopes: a definition-only rule does not prohibit an imported
TypedDict appearing in an exported signature. Name that scope in config and docs.

For public mappings, define public through package API contracts and symbol naming,
not merely every function in application/. Cover methods and async functions only
when explicitly supported. A free-form JSON object, passthrough integration,
logging context or extension dictionary may intentionally have no closed schema.
Use the existing package-policy override mechanism and reasoned suppressions.

For string comparisons, count distinct values per resolved binding and lexical
scope, never all occurrences of a variable spelling across a file. self.status on
unrelated classes must not be merged. Initially support explicit str parameters;
Literal/Enum annotations are counterexamples. match/case, membership tests,
attribute ownership and reassignment need explicit coverage before being counted.
Repeated protocol strings do not establish that the value set is closed. Choosing
Literal versus Enum can change runtime/API behavior and remains a human decision.

## Required regressions

- Raw decode plus cast; validated TypeAdapter result; an already validated value
  cast for a type checker's benefit.
- Qualified imports, aliases, local shadowing, rebindings and same-named classes
  from unrelated modules; functional and inherited TypedDict forms.
- Closest package overrides, no-policy behavior, CLI selection, suppressions and
  debt. Agent context exposes effective policy for a planned file.
- Public/private functions, explicit facade re-exports, intentional free-form maps
  and method scope. Never treat a name convention alone as proof of a boundary.
- Distinct string values versus repeated comparisons to one value; unrelated
  bindings with the same spelling; Literal/Enum and open protocol counterexamples.
- Version-sensitive typing syntax, workspace roots and namespace layouts as they
  become supported. Real repositories remain read-only evaluation targets.

Implement one candidate per behavioral PR. Start with the explicit TypedDict
representation convention if desired, then a narrowly evidenced payload-cast hint.
The string-discriminator heuristic should follow once binding provenance is tested.

## Primary references

- [Typing specification: cast](https://typing.python.org/en/latest/spec/directives.html#cast):
  a cast returns its argument unchanged at runtime.
- [Pydantic TypeAdapter](https://docs.pydantic.dev/latest/concepts/type_adapter/):
  validation without requiring a BaseModel, including a TypedDict example.
- [Pydantic model construction](https://docs.pydantic.dev/latest/concepts/models/#creating-models-without-validation):
  BaseModel construction can deliberately bypass validation.
