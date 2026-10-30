import posixpath
import re
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

type Policy = Literal["allow", "deny"]
type SeverityName = Literal["error", "warning", "hint", "off"]

_DOTTED_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_RULE_CODE = re.compile(r"^[A-Z]+[0-9]+$")
_RULE_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_LAYER_PAIR = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*->\s*([A-Za-z_][\w.]*)\s*$")
_FEATURE_LAYER = re.compile(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_dotted(values: list[str]) -> list[str]:
    for value in values:
        if not _DOTTED_NAME.match(value):
            msg = f'"{value}" is not a dotted module name'
            raise ValueError(msg)
    return values


class SourceExclusion(_Model):
    path: str = Field(
        description="Project-relative glob of source files deliberately excluded from analysis."
    )
    reason: str = Field(
        description="Why this code is outside this architecture policy."
    )

    @field_validator("path", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class ProjectConfig(_Model):
    """Which packages and tests smelt reads."""

    root_packages: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Top-level packages to check; discovered from source_roots when omitted.",
        ),
    ]
    source_roots: list[str] = Field(
        default_factory=lambda: ["."],
        description="Directories that contain the root packages, e.g. [src].",
    )
    test_roots: list[str] = Field(
        default_factory=lambda: ["tests"],
        description="Directories with the tests; with layout: mirror they must exist.",
    )
    exclude: list[str | SourceExclusion] = Field(
        default_factory=list,
        description="Path globs or {path, reason} entries smelt skips entirely. Strict coverage requires a reason for every exclusion.",
    )
    coverage: Literal["configured", "strict"] = Field(
        default="configured",
        description="strict: fail on uncovered or unclassified production files and exclusions without reasons, including Python workspace members.",
    )
    python_version: str | None = Field(
        default=None,
        pattern=r"^3\.[0-9]+$",
        description="Explicit minimum supported Python version for version-aware conventions, e.g. 3.14. Overrides per-project requires-python metadata; never inferred from the running interpreter or .python-version.",
    )

    @property
    def exclude_patterns(self) -> list[str]:
        return [
            entry if isinstance(entry, str) else entry.path for entry in self.exclude
        ]

    @field_validator("source_roots", "test_roots")
    @classmethod
    def _normalize_roots(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(posixpath.normpath(v.replace("\\", "/")) for v in values)
        )

    @field_validator("root_packages")
    @classmethod
    def _root_packages_are_top_level(cls, values: list[str]) -> list[str]:
        for value in _check_dotted(values):
            if "." in value:
                msg = f'"{value}" must be a top-level package name'
                raise ValueError(msg)
        return values


class FeaturesConfig(_Model):
    root: str | None = Field(
        default=None,
        description="Package whose direct child packages are the features.",
    )
    pattern: str | None = Field(
        default=None,
        description='Dotted pattern ending in {feature}, e.g. "app.{feature}".',
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.root is None) == (self.pattern is None):
            msg = "set exactly one of `root` or `pattern`"
            raise ValueError(msg)
        if self.root is not None:
            _check_dotted([self.root])
        if self.pattern is not None:
            segments = self.pattern.split(".")
            if segments.count("{feature}") != 1 or segments[-1] != "{feature}":
                msg = (
                    f'pattern "{self.pattern}" must end with a "{{feature}}" segment, '
                    'e.g. "gateway.{feature}"'
                )
                raise ValueError(msg)
            if len(segments) < 2:  # noqa: PLR2004
                msg = "pattern needs a package before {feature}"
                raise ValueError(msg)
            _check_dotted(segments[:-1])
        return self


class ThirdPartyPolicy(_Model):
    default: Policy = "allow"
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _expand_short_form(cls, data: object) -> object:
        if data in ("allow", "deny"):
            return {"default": data}
        return data

    @model_validator(mode="after")
    def _entries_match_default(self) -> Self:
        if self.default == "allow" and self.allow:
            msg = "`allow` is only valid with `default: deny`"
            raise ValueError(msg)
        if self.default == "deny" and self.deny:
            msg = "`deny` is only valid with `default: allow`"
            raise ValueError(msg)
        _check_dotted([*self.allow, *self.deny])
        return self


class LayerConfig(_Model):
    path: str = Field(description="Dotted path of the layer package inside a feature.")
    may_depend_on: list[str] = Field(
        default_factory=list, description="Layers this layer may import."
    )
    third_party: ThirdPartyPolicy = Field(
        default_factory=ThirdPartyPolicy,
        description="Which third-party packages the layer may import (SMT103).",
    )

    @field_validator("path")
    @classmethod
    def _path_is_dotted(cls, value: str) -> str:
        _check_dotted([value])
        return value


class CrossFeatureAllowance(_Model):
    source: str = Field(alias="from")
    target: str = Field(alias="to")

    @model_validator(mode="after")
    def _feature_layers(self) -> Self:
        for value in (self.source, self.target):
            if not _FEATURE_LAYER.fullmatch(value):
                msg = f'"{value}" must look like "feature.layer"'
                raise ValueError(msg)
        return self

    def components(self) -> tuple[str, str, str, str]:
        source_feature, source_layer = self.source.split(".")
        target_feature, target_layer = self.target.split(".")
        return source_feature, source_layer, target_feature, target_layer


class CrossFeatureConfig(_Model):
    """Imports between features."""

    default: Policy = Field(
        default="deny", description="Whether features may import each other at all."
    )
    allow: list[str | CrossFeatureAllowance] = Field(
        default_factory=list,
        description='Allowed imports: "layer -> layer" for all features, or {from: feature.layer, to: feature.layer}.',
    )

    @field_validator("allow")
    @classmethod
    def _pairs_are_well_formed(
        cls, values: list[str | CrossFeatureAllowance]
    ) -> list[str | CrossFeatureAllowance]:
        for value in values:
            if isinstance(value, str) and not _LAYER_PAIR.match(value):
                msg = f'"{value}" must look like "layer -> layer"'
                raise ValueError(msg)
        return values

    def pairs(self) -> frozenset[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for value in self.allow:
            if not isinstance(value, str):
                continue
            match = _LAYER_PAIR.match(value)
            if match:
                result.add((match.group(1), match.group(2)))
        return frozenset(result)

    def allows(
        self,
        source_feature: str,
        source_layer: str,
        target_feature: str,
        target_layer: str,
    ) -> bool:
        if (source_layer, target_layer) in self.pairs():
            return True
        return any(
            isinstance(value, CrossFeatureAllowance)
            and value.components()
            == (source_feature, source_layer, target_feature, target_layer)
            for value in self.allow
        )

    def labels(self) -> list[str]:
        return [
            value if isinstance(value, str) else f"{value.source} -> {value.target}"
            for value in self.allow
        ]

    def entries(self) -> list[dict[str, str | None]]:
        """Each allowance as fields; a ``None`` feature stands for every feature."""
        found: list[dict[str, str | None]] = []
        for value in self.allow:
            features: tuple[str | None, str | None] = (None, None)
            if isinstance(value, CrossFeatureAllowance):
                source_feature, source_layer, target_feature, target_layer = (
                    value.components()
                )
                features = (source_feature, target_feature)
            elif match := _LAYER_PAIR.match(value):
                source_layer, target_layer = match.group(1), match.group(2)
            else:
                continue
            found.append(
                {
                    "source_feature": features[0],
                    "source_layer": source_layer,
                    "target_feature": features[1],
                    "target_layer": target_layer,
                }
            )
        return found


type CycleScope = Literal["features", "layers", "siblings"]


def _all_cycle_scopes() -> list[CycleScope]:
    return ["features", "layers", "siblings"]


class ImportsConfig(_Model):
    """Which imports the dependency rules look at."""

    type_checking: Literal["include", "ignore"] = Field(
        default="include",
        description="Whether imports under `if TYPE_CHECKING:` count.",
    )
    transitive: bool = Field(
        default=False,
        description="Also check indirect layer, feature, shared, composition-root and third-party dependencies through all reachable project modules. Reports a shortest import chain; external packages are leaves.",
    )
    cycles: list[CycleScope] = Field(
        default_factory=_all_cycle_scopes, description="Where SMT104 looks for cycles."
    )


class ArchitectureConfig(_Model):
    """Features, layers and the modules around them."""

    features: FeaturesConfig | None = Field(
        default=None, description="Where the features (vertical slices) live."
    )
    shared: list[str] = Field(
        default_factory=list,
        description="Modules every feature may import; they must not import features.",
    )
    composition_root: list[str] = Field(
        default_factory=list,
        description="Modules that wire everything; nothing may import them.",
    )
    wiring: list[str] = Field(
        default_factory=list,
        description="Provider modules (whole-segment * allowed) that may import across layers and features; they keep their feature and layer.",
    )
    modules: dict[str, str] = Field(
        default_factory=dict,
        description="Packages outside the features mapped to a layer, e.g. {backend.platform: infrastructure}.",
    )
    layers: dict[str, LayerConfig] = Field(
        default_factory=dict, description="The layers and what each may import."
    )
    cross_feature: CrossFeatureConfig = Field(
        default_factory=CrossFeatureConfig, description="Imports between features."
    )
    imports: ImportsConfig = Field(
        default_factory=ImportsConfig, description="Which imports count."
    )

    @field_validator("shared", "composition_root")
    @classmethod
    def _modules_are_dotted(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)

    @field_validator("modules")
    @classmethod
    def _module_keys_are_dotted(cls, values: dict[str, str]) -> dict[str, str]:
        _check_dotted(list(values))
        return values

    @field_validator("wiring")
    @classmethod
    def _wiring_patterns(cls, values: list[str]) -> list[str]:
        for value in values:
            if any(
                segment != "*" and not _DOTTED_NAME.fullmatch(segment)
                for segment in value.split(".")
            ):
                msg = f'"{value}" must be a dotted module pattern with whole-segment * wildcards'
                raise ValueError(msg)
        return values


class ImportStyleConfig(_Model):
    style: Literal["relative", "absolute"] = Field(
        default="relative",
        description="relative: prefer relative first-party imports up to max_relative_level, absolute beyond it; absolute: always use absolute imports.",
    )
    max_relative_level: int = Field(
        default=1,
        ge=1,
        description="Maximum dots in a relative import when style is relative. Cross-root and deeper imports stay absolute.",
    )


type ModuleOrderSlot = Literal[
    "docstring",
    "future-imports",
    "imports",
    "dunder-assignments",
    "type-vars",
    "public",
    "private",
    "main-guard",
]
type ClassOrderSlot = Literal[
    "class-attributes", "init", "dunders", "public", "private"
]


def _module_order() -> list[ModuleOrderSlot]:
    return [
        "docstring",
        "future-imports",
        "imports",
        "dunder-assignments",
        "type-vars",
        "public",
        "private",
        "main-guard",
    ]


def _class_order() -> list[ClassOrderSlot]:
    return ["class-attributes", "init", "dunders", "public", "private"]


class OrderingConfig(_Model):
    module: list[ModuleOrderSlot] = Field(default_factory=_module_order)
    class_order: list[ClassOrderSlot] = Field(
        default_factory=_class_order, alias="class"
    )
    constants_exempt: bool = Field(
        default=False,
        description="Treat private uppercase module constants like public definitions, preserving their relative order.",
    )
    fix: Literal["off", "safe-only"] = Field(
        default="safe-only",
        description="Permit bounded ordering fixes only when check --fix is explicitly requested. Plain check never writes source.",
    )

    @field_validator("module")
    @classmethod
    def _module_slots(cls, values: list[ModuleOrderSlot]) -> list[ModuleOrderSlot]:
        if len(values) != len(set(values)) or set(values) != set(_module_order()):
            raise ValueError(
                "module order must contain every module category exactly once"
            )
        if values.index("public") > values.index("private"):
            raise ValueError("public-definitions-first requires public before private")
        if (
            values[:3] != ["docstring", "future-imports", "imports"]
            or values[-1] != "main-guard"
        ):
            raise ValueError(
                "module order must start with docstring, future-imports, imports and end with main-guard"
            )
        return values

    @field_validator("class_order")
    @classmethod
    def _class_slots(cls, values: list[ClassOrderSlot]) -> list[ClassOrderSlot]:
        if len(values) != len(set(values)) or set(values) != set(_class_order()):
            raise ValueError(
                "class order must contain every class category exactly once"
            )
        if values.index("public") > values.index("private"):
            raise ValueError("public-definitions-first requires public before private")
        return values


class TypeDispatchConfig(_Model):
    style: Literal["allow", "match"] = "match"
    min_branches: int = Field(
        default=3,
        ge=2,
        description="Minimum number of type-tested if/elif branches over the same simple name before SMT315 suggests match/case.",
    )


class PackageContract(_Model):
    """Opt-in module layout and entry points for matching packages."""

    match: str = Field(
        description="Dotted package pattern; a whole-segment * matches one package segment."
    )
    required: list[str] = Field(
        default_factory=list,
        description="Relative dotted modules or packages that must exist in every matching package (SMT307).",
    )
    public: list[str] | None = Field(
        default=None,
        description="Relative public module patterns (SMT107). '.' exposes the package facade; * matches one segment, ** any depth. Omitted: no interface restriction; []: private package. Each pattern must match analyzed code.",
    )
    imports: ImportStyleConfig | None = Field(
        default=None,
        description="Optional import style for code in this package. The most specific enclosing package contract wins.",
    )
    bundle_imports_at: int | None = Field(
        default=None,
        ge=2,
        description="Suggest a package facade when one outside source module imports this many distinct implementation modules of the package (SMT108, hint).",
    )
    module_docstrings: Literal["allow", "forbid"] | None = Field(
        default=None,
        description="Optional module-docstring policy (SMT309). Function and class documentation is unaffected. The closest explicit package policy wins.",
    )
    future_annotations: Literal["allow", "forbid", "when-needed"] | None = Field(
        default=None,
        description="allow: preserve; forbid: reject the annotations future import (SMT310); when-needed: reject it for a declared Python 3.14+ minimum, otherwise conservatively suggest review when no postponement need is found (SMT311). No automatic rewriting.",
    )
    private_modules: Literal["allow", "forbid"] | None = Field(
        default=None,
        description="Optional naming convention (SMT312): forbid module and package names starting with _, except the special __init__.py and __main__.py files. Private functions and methods are unaffected. The closest explicit package policy wins.",
    )
    ordering: OrderingConfig | None = Field(
        default=None,
        description="Definition order for modules and classes. Dependencies and execution barriers take precedence over category priority; the closest explicit policy wins.",
    )
    type_dispatch: TypeDispatchConfig | None = Field(
        default=None,
        description="Optional SMT315 match/case preference for same-subject isinstance chains. Closest package policy wins; no automatic rewrite.",
    )
    rules: dict[str, SeverityName] = Field(
        default_factory=dict,
        description="Rule severities for production findings in this package. Use the same problem names or SMT codes as top-level rules; the closest package override wins. Options alone do not enable public-definitions-first.",
    )

    @field_validator("match")
    @classmethod
    def _package_pattern(cls, value: str) -> str:
        if any(
            segment != "*" and not _DOTTED_NAME.fullmatch(segment)
            for segment in value.split(".")
        ):
            raise ValueError(
                "must be a dotted package pattern with whole-segment * wildcards"
            )
        return value

    @field_validator("required")
    @classmethod
    def _required_modules(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_check_dotted(values)))

    @field_validator("public")
    @classmethod
    def _public_modules(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        for value in values:
            if value == ".":
                continue
            if any(
                segment not in ("*", "**") and not _DOTTED_NAME.fullmatch(segment)
                for segment in value.split(".")
            ):
                raise ValueError(
                    "public entries must be relative dotted module patterns or '.'"
                )
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _has_contract(self) -> Self:
        if (
            not self.required
            and self.public is None
            and self.imports is None
            and self.bundle_imports_at is None
            and self.module_docstrings is None
            and self.future_annotations is None
            and self.private_modules is None
            and self.ordering is None
            and self.type_dispatch is None
            and not self.rules
        ):
            raise ValueError("set at least one package contract policy")
        return self


class StructureConfig(_Model):
    """Naming rules for packages and modules."""

    forbidden_names: list[str] = Field(
        default_factory=list, description="Package and module names to avoid (SMT302)."
    )
    contracts: dict[str, PackageContract] = Field(
        default_factory=dict,
        description="Named package contracts: required modules and optional public entry points.",
    )

    @field_validator("contracts")
    @classmethod
    def _contract_names(
        cls, values: dict[str, PackageContract]
    ) -> dict[str, PackageContract]:
        if any(not name.strip() for name in values):
            raise ValueError("contract names must not be empty")
        return values


class TestsConfig(_Model):
    """Test placement and genuine parameters for test analyses."""

    layout: Literal["mirror", "none"] = Field(
        default="none",
        description="mirror: every test file must mirror a source module.",
    )
    mirror: str = Field(
        default="{path}/test_{module}.py",
        description="Test path relative to the test root; placeholders {root}, {path}, {module}.",
    )
    unmirrored: list[str] = Field(
        default_factory=list,
        description='Test path globs exempt from mirroring, e.g. ["tests/e2e/**"].',
    )
    mirror_suffixes: bool = Field(
        default=False, description="Also allow test_<module>_<topic>.py."
    )
    quality_exclude: list[str] = Field(
        default_factory=list,
        description="Project-relative globs excluded from test-quality checks, independently of test mirroring.",
    )
    forbid_mocks_in: list[str] = Field(
        default_factory=list,
        description="Project-relative test globs forbidding recognized unittest.mock, pytest-mock and MonkeyPatch attribute replacement, including fixtures (SMT421). Environment/mapping setup is not counted as MonkeyPatch mocking.",
    )
    max_patches_per_test: int | None = Field(
        default=None,
        ge=0,
        description="Maximum recognized patch calls per collected test, including decorators (SMT423). Nested helpers and fixtures are not attributed to callers.",
    )
    assertion_helpers: list[str] = Field(
        default_factory=list,
        description="Qualified callable names accepted as assertion evidence, e.g. tests.assertions.assert_user.",
    )
    timeout_helpers: list[str] = Field(
        default_factory=list,
        description="Qualified context/wrapper helpers known to bound async waits, e.g. tests.timing.deadline. They do not supply assertion evidence or bound synchronous subprocess calls.",
    )

    @field_validator("quality_exclude", "forbid_mocks_in")
    @classmethod
    def _quality_patterns(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            pattern = value.strip().replace("\\", "/")
            if (
                not pattern
                or pattern.startswith("/")
                or re.match(r"^[A-Za-z]:", pattern)
                or ".." in pattern.split("/")
            ):
                raise ValueError(
                    "test quality globs must be nonempty project-relative paths without '..'"
                )
            result.append(posixpath.normpath(pattern))
        return list(dict.fromkeys(result))

    @field_validator("assertion_helpers", "timeout_helpers")
    @classmethod
    def _assertion_helpers(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)

    @field_validator("mirror")
    @classmethod
    def _mirror_is_a_file_pattern(cls, value: str) -> str:
        unknown = set(re.findall(r"\{[^}]*\}", value)) - {
            "{path}",
            "{module}",
            "{root}",
        }
        if unknown:
            msg = f'mirror "{value}" has unknown placeholders: {", ".join(sorted(unknown))}'
            raise ValueError(msg)
        if value.count("{module}") != 1 or not value.endswith(".py"):
            msg = f'mirror "{value}" needs one "{{module}}" and must end with ".py"'
            raise ValueError(msg)
        if (
            value.startswith("/")
            or value.count("{path}") > 1
            or value.count("{root}") > 1
        ):
            msg = f'mirror "{value}" must be relative, with {{path}} and {{root}} at most once'
            raise ValueError(msg)
        return value


class IgnoreEntry(_Model):
    rule: str
    modules: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    reason: str

    @model_validator(mode="after")
    def _has_scope(self) -> Self:
        if not self.modules and not self.paths:
            msg = "an ignore entry needs `modules` or `paths`"
            raise ValueError(msg)
        if not self.reason.strip():
            msg = "`reason` must not be empty"
            raise ValueError(msg)
        return self


class SuppressionsConfig(_Model):
    """Inline `# smelt: ignore[CODE] -- reason` comments."""

    require_reason: bool = True


class SmeltConfig(_Model):
    version: Literal[1]
    project: ProjectConfig
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    tests: TestsConfig = Field(default_factory=TestsConfig)
    rules: dict[str, SeverityName] = Field(
        default_factory=dict,
        description="Explicit checks by problem name or stable SMT code. A severity enables the check; off disables it. No additional boolean switch is required for fixed-pattern rules.",
    )
    ignore: list[IgnoreEntry] = Field(
        default_factory=list, description="Rules to skip for modules or paths."
    )
    suppressions: SuppressionsConfig = Field(
        default_factory=SuppressionsConfig,
        description="Inline `# smelt: ignore` rules.",
    )
    debt: str | None = Field(
        default=None,
        description="Debt file with known violations, e.g. .smelt/debt.json.",
    )

    @field_validator("rules")
    @classmethod
    def _rule_codes_are_well_formed(
        cls, values: dict[str, SeverityName]
    ) -> dict[str, SeverityName]:
        for key in values:
            if not (_RULE_CODE.match(key) or _RULE_NAME.match(key)):
                msg = (
                    f'"{key}" is not a rule code like "SMT101" '
                    'or a rule name like "layer-boundary"'
                )
                raise ValueError(msg)
        return values
