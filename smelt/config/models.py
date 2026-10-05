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
    root_packages: Annotated[list[str], Field(min_length=1)]
    source_roots: list[str] = Field(default_factory=lambda: ["."])
    test_roots: list[str] = Field(default_factory=lambda: ["tests"])
    exclude: list[str] = Field(default_factory=list)

    @field_validator("root_packages")
    @classmethod
    def _root_packages_are_top_level(cls, values: list[str]) -> list[str]:
        for value in _check_dotted(values):
            if "." in value:
                msg = f'"{value}" must be a top-level package name'
                raise ValueError(msg)
        return values


class FeaturesConfig(_Model):
    root: str | None = None
    pattern: str | None = None

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
    path: str
    may_depend_on: list[str] = Field(default_factory=list)
    third_party: ThirdPartyPolicy = Field(default_factory=ThirdPartyPolicy)

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
    default: Policy = "deny"
    allow: list[str | CrossFeatureAllowance] = Field(default_factory=list)

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
    type_checking: Literal["include", "ignore"] = "include"
    transitive: bool = False
    cycles: list[CycleScope] = Field(default_factory=_all_cycle_scopes)


class ArchitectureConfig(_Model):
    features: FeaturesConfig | None = None
    shared: list[str] = Field(default_factory=list)
    composition_root: list[str] = Field(default_factory=list)
    wiring: list[str] = Field(default_factory=list)
    # packages outside the features that belong to a layer, e.g. backend.platform
    modules: dict[str, str] = Field(default_factory=dict)
    layers: dict[str, LayerConfig] = Field(default_factory=dict)
    cross_feature: CrossFeatureConfig = Field(default_factory=CrossFeatureConfig)
    imports: ImportsConfig = Field(default_factory=ImportsConfig)

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
    forbidden_names: list[str] = Field(default_factory=list)


class TestsConfig(_Model):
    layout: Literal["mirror", "none"] = "none"
    # `mirror` is relative to the test root
    mirror: str = "{path}/test_{module}.py"
    unmirrored: list[str] = Field(default_factory=list)
    mirror_suffixes: bool = False

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
    require_reason: bool = True


class SmeltConfig(_Model):
    version: Literal[1]
    project: ProjectConfig
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    tests: TestsConfig = Field(default_factory=TestsConfig)
    rules: dict[str, SeverityName] = Field(default_factory=dict)
    ignore: list[IgnoreEntry] = Field(default_factory=list)
    suppressions: SuppressionsConfig = Field(default_factory=SuppressionsConfig)
    debt: str | None = None

    @field_validator("rules")
    @classmethod
    def _rule_codes_are_well_formed(
        cls, values: dict[str, SeverityName]
    ) -> dict[str, SeverityName]:
        for code in values:
            if not _RULE_CODE.match(code):
                msg = f'"{code}" is not a rule code like "SMT101"'
                raise ValueError(msg)
        return values
