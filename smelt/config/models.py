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


class ProjectConfig(_Model):
    """Which packages and tests smelt reads."""

    root_packages: Annotated[
        list[str],
        Field(
            min_length=1,
            description="Top-level packages to check, e.g. [backend, agent].",
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
    exclude: list[str] = Field(
        default_factory=list, description="Path globs smelt skips entirely."
    )

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
        default=False, description="Also report layer violations through other modules."
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


class StructureConfig(_Model):
    """Naming rules for packages and modules."""

    forbidden_names: list[str] = Field(
        default_factory=list, description="Package and module names to avoid (SMT302)."
    )


class TestsConfig(_Model):
    """Where test files must live."""

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
        default_factory=dict, description="Severity per rule code, or off."
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
