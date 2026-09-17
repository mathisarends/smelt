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

PATCH_CATEGORIES = frozenset(
    {"external", "environment", "stdlib", "private", "first_party", "shared"}
)


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
    forbid_bases: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _path_is_dotted(cls, value: str) -> str:
        _check_dotted([value])
        return value

    @field_validator("forbid_bases")
    @classmethod
    def _bases_are_dotted(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)


class CrossFeatureConfig(_Model):
    default: Policy = "deny"
    allow: list[str] = Field(default_factory=list)

    @field_validator("allow")
    @classmethod
    def _pairs_are_well_formed(cls, values: list[str]) -> list[str]:
        for value in values:
            if not _LAYER_PAIR.match(value):
                msg = f'"{value}" must look like "layer -> layer"'
                raise ValueError(msg)
        return values

    def pairs(self) -> frozenset[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for value in self.allow:
            match = _LAYER_PAIR.match(value)
            if match:
                result.add((match.group(1), match.group(2)))
        return frozenset(result)


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
    layers: dict[str, LayerConfig] = Field(default_factory=dict)
    cross_feature: CrossFeatureConfig = Field(default_factory=CrossFeatureConfig)
    di_frameworks: list[str] = Field(default_factory=list)
    imports: ImportsConfig = Field(default_factory=ImportsConfig)

    @field_validator("shared", "composition_root", "di_frameworks")
    @classmethod
    def _modules_are_dotted(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)


class RoleDetect(_Model):
    base: str | None = None
    implements: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.base is None) == (self.implements is None):
            msg = "`detect` needs exactly one condition: `base` or `implements`"
            raise ValueError(msg)
        if self.base is not None:
            _check_dotted([self.base])
        return self


class RoleConfig(_Model):
    detect: RoleDetect
    layers: list[str] = Field(default_factory=list)
    file: str | None = None

    @field_validator("file")
    @classmethod
    def _file_is_module_name(cls, value: str | None) -> str | None:
        if value is not None and not re.match(r"^[A-Za-z_]\w*(\.py)?$", value):
            msg = f'"{value}" must be a module file name like "ports.py"'
            raise ValueError(msg)
        return value

    @property
    def module_name(self) -> str | None:
        return self.file.removesuffix(".py") if self.file else None


class AnalysisConfig(_Model):
    types: Literal["none", "pyright"] = "none"
    pyright_command: list[str] = Field(default_factory=lambda: ["pyright"])


class StructureConfig(_Model):
    forbidden_names: list[str] = Field(default_factory=list)
    crowded_threshold: Annotated[int, Field(ge=1)] = 10


class PatchingConfig(_Model):
    allow: list[str] = Field(
        default_factory=lambda: ["external", "environment", "stdlib"]
    )
    forbid: list[str] = Field(default_factory=lambda: ["private"])


class MocksConfig(_Model):
    max_per_test: Annotated[int, Field(ge=0)] = 3
    forbid_first_party: list[str] = Field(default_factory=list)


class BloatConfig(_Model):
    ratio: Annotated[float, Field(gt=0)] = 5
    min_test_loc: Annotated[int, Field(ge=0)] = 100


class TestsConfig(_Model):
    layout: Literal["mirror", "feature", "none"] = "none"
    pattern: str = "tests/{feature}"
    patching: PatchingConfig = Field(default_factory=PatchingConfig)
    private_access: Literal["allow", "forbid"] = "forbid"
    mocks: MocksConfig = Field(default_factory=MocksConfig)
    interaction_assertions: SeverityName = "warning"
    bloat: BloatConfig = Field(default_factory=BloatConfig)

    @field_validator("pattern")
    @classmethod
    def _pattern_has_placeholder(cls, value: str) -> str:
        if "{feature}" not in value:
            msg = f'pattern "{value}" needs a "{{feature}}" placeholder'
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


class VerifyStep(_Model):
    name: str
    run: str


class SmeltConfig(_Model):
    version: Literal[1]
    project: ProjectConfig
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    structure: StructureConfig = Field(default_factory=StructureConfig)
    tests: TestsConfig = Field(default_factory=TestsConfig)
    rules: dict[str, SeverityName] = Field(default_factory=dict)
    ignore: list[IgnoreEntry] = Field(default_factory=list)
    suppressions: SuppressionsConfig = Field(default_factory=SuppressionsConfig)
    baseline: str | None = None
    plugins: list[str] = Field(default_factory=list)
    verify: list[VerifyStep] = Field(default_factory=list)

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

    @field_validator("plugins")
    @classmethod
    def _plugins_are_dotted(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)
