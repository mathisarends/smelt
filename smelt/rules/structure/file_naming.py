from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class YamlFileExtension(Rule):
    code = "SMT331"
    name = "yaml-file-extension"
    category = Category.STRUCTURE
    scope = "project"
    activation = POLICIES["SMT331"]
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A YAML filename uses a different extension from the project's canonical spelling.",
        rationale="Choose yaml or yml once in structure.file_naming.yaml_extension to keep configuration filenames consistent. The policy checks lowercase .yaml and .yml filenames below the directory containing smelt.yml, including the Smelt configuration itself, Compose files and hidden directories such as .github. Project exclusions and tool/cache directories are skipped. No YAML contents are parsed, and an omitted policy permits either spelling even with --select ALL.",
        bad="# With yaml_extension: yaml\n# settings.yml",
        good="# With yaml_extension: yaml\n# settings.yaml",
        fix="Rename the file to the expected path and update references to its old name, including CI, scripts and extends entries. No file or reference is changed automatically. Use a reasoned project.exclude or ignore.paths entry for filenames required by another tool.",
        config=("structure.file_naming.yaml_extension", "project.exclude", "ignore"),
        example_language="yaml",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        extension = ctx.config.structure.file_naming.yaml_extension
        if extension is None:
            return
        for path in ctx.files.yaml_paths:
            filename = PurePosixPath(path)
            if filename.suffix == f".{extension}":
                continue
            expected = filename.with_suffix(f".{extension}").as_posix()
            yield self.violation(
                f"YAML filename must use .{extension}: {path}",
                path=path,
                expected={"yaml_extension": extension, "path": expected},
                hint=f'Rename to "{expected}" and update filename references; resolve any existing destination manually.',
            )


class ComposeFileName(Rule):
    code = "SMT332"
    name = "compose-file-name"
    category = Category.STRUCTURE
    scope = "project"
    activation = POLICIES["SMT332"]
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A Compose filename uses a different basename from the project's canonical spelling.",
        rationale="Choose compose or docker-compose once in structure.file_naming.compose_basename. The policy recognizes compose.yaml, docker-compose.yml and dotted variants such as docker-compose.override.yaml or compose.prod.yml anywhere below the project root. Variant suffixes are preserved. Custom filenames such as stack.yaml and docker-compose-backup.yml are not classified by content. Extension consistency belongs solely to SMT331; either extension is valid when yaml_extension is omitted. No policy means no finding, including with --select ALL.",
        bad="# With compose_basename: compose\n# docker-compose.override.yaml",
        good="# With compose_basename: compose\n# compose.override.yaml",
        fix="Rename to the expected path and update references such as docker compose -f arguments, scripts and CI. Check for existing destination files before renaming. Files and references are never changed automatically. Use a reasoned ignore.paths entry for an intentional exception.",
        config=("structure.file_naming.compose_basename", "project.exclude", "ignore"),
        example_language="yaml",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        basename = ctx.config.structure.file_naming.compose_basename
        if basename is None:
            return
        for path in ctx.files.yaml_paths:
            filename = PurePosixPath(path)
            current, separator, variant = filename.stem.partition(".")
            if current not in {"compose", "docker-compose"} or current == basename:
                continue
            name = basename + (separator + variant if separator else "")
            expected = filename.with_name(name + filename.suffix).as_posix()
            yield self.violation(
                f"Compose filename must use {basename}: {path}",
                path=path,
                expected={"compose_basename": basename, "path": expected},
                hint=f'Rename to "{expected}" and update filename references; resolve any existing destination manually.',
            )
