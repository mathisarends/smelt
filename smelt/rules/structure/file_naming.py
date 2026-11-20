from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import Rule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class FileNameConvention(Rule):
    code = "LAY007"
    name = "layout/file-name-convention"
    category = Category.LAYOUT
    scope = "project"
    activation = POLICIES["LAY007"]
    requires = frozenset({Index.FILES})
    doc = RuleDoc(
        summary="A YAML or Compose filename differs from the project's canonical spelling.",
        rationale=(
            "Choose spellings once in style.file_naming to keep configuration "
            "filenames consistent. yaml_extension (yaml or yml) checks lowercase "
            ".yaml and .yml filenames below the directory containing smelt.yml, "
            "including the Smelt configuration itself, Compose files and hidden "
            "directories such as .github. compose_basename (compose or "
            "docker-compose) recognizes compose.yaml, docker-compose.yml and dotted "
            "variants such as docker-compose.override.yaml anywhere below the "
            "project root; variant suffixes are preserved. A file violating both "
            "options gets one finding with the fully corrected path. Custom "
            "filenames such as stack.yaml are not classified by content. Project "
            "exclusions and tool/cache directories are skipped. No YAML contents "
            "are parsed, and an omitted option permits either spelling even with "
            "--select ALL."
        ),
        bad="# With yaml_extension: yaml, compose_basename: compose\n# settings.yml\n# docker-compose.override.yaml",
        good="# With yaml_extension: yaml, compose_basename: compose\n# settings.yaml\n# compose.override.yaml",
        fix="Rename the file to the expected path and update references to its old name, including CI, scripts, docker compose -f arguments and extends entries. Check for existing destination files before renaming. No file or reference is changed automatically. Use a reasoned project.exclude or ignore.paths entry for filenames required by another tool.",
        config=(
            "style.file_naming.yaml_extension",
            "style.file_naming.compose_basename",
            "project.exclude",
            "ignore",
        ),
        example_language="yaml",
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        policy = ctx.config.structure.file_naming
        extension, basename = policy.yaml_extension, policy.compose_basename
        if extension is None and basename is None:
            return
        for path in ctx.files.yaml_paths:
            expected = PurePosixPath(path)
            reasons: dict[str, str] = {}
            if extension is not None and expected.suffix != f".{extension}":
                expected = expected.with_suffix(f".{extension}")
                reasons["yaml_extension"] = extension
            current, separator, variant = expected.stem.partition(".")
            if (
                basename is not None
                and current in {"compose", "docker-compose"}
                and current != basename
            ):
                name = basename + (separator + variant if separator else "")
                expected = expected.with_name(name + expected.suffix)
                reasons["compose_basename"] = basename
            if not reasons:
                continue
            spelled = " and ".join(
                f".{value}" if key == "yaml_extension" else value
                for key, value in reasons.items()
            )
            yield self.violation(
                f"filename must use {spelled}: {path}",
                path=path,
                expected={**reasons, "path": expected.as_posix()},
                hint=f'Rename to "{expected.as_posix()}" and update filename references; resolve any existing destination manually.',
            )
