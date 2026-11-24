from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.errors import AnalysisError
from smelt.diagnostics.render.machine import render_json
from smelt.engine.check import CheckOptions
from tests.helpers import check, write_project

if TYPE_CHECKING:
    from pathlib import Path

PROJECT = {
    "smelt.yml": """
        version: 2
        project:
          root_packages: [shop]
        architecture:
          layers:
            domain: {path: domain}
            infrastructure: {path: infra, may_depend_on: [domain]}
          packages:
            orders:
              match: shop.domain
              required: [orders, payments]
              public: [orders]
        rules:
          architecture/unclassified-module: warning
          layout/definition-order: warning
    """,
    "shop/__init__.py": '"""The shop."""\n',
    "shop/domain/__init__.py": "",
    "shop/domain/orders.py": (
        "from shop.infra.store import Store\n\n\nclass Order:\n    store: Store\n"
    ),
    "shop/infra/__init__.py": "",
    "shop/infra/store.py": "class Store:\n    pass  # smelt: ignore[no-such-rule]\n",
    "shop/helpers.py": (
        "def _helper() -> None:\n    pass\n\n\ndef helper() -> None:\n    _helper()\n"
    ),
}
BASE = CheckOptions(use_cache=False, show_coverage=True)


@pytest.mark.parametrize(
    "options",
    [
        BASE,
        replace(BASE, select=("ALL",), strict_coverage=True),
        replace(BASE, select=("ALL",), ignore=("ARC",)),
        replace(BASE, paths=("shop/domain",)),
    ],
    ids=["configured", "strict", "ignored", "scoped"],
)
def test_reports_like_the_python_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: CheckOptions
) -> None:
    write_project(tmp_path, PROJECT)
    expected = render_json(check(tmp_path, options).report, show_coverage=True)

    monkeypatch.setenv("SMELT_CORE", "native")
    actual = render_json(check(tmp_path, options).report, show_coverage=True)

    assert json.loads(actual) == json.loads(expected)


def test_source_that_does_not_parse_fails_only_rules_reading_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_project(
        tmp_path,
        {
            "smelt.yml": "version: 2\nproject: {root_packages: [shop]}\n",
            "shop/__init__.py": "",
            "shop/broken.py": "total = = 0\n",
        },
    )
    monkeypatch.setenv("SMELT_CORE", "native")

    names_only = check(tmp_path, replace(BASE, select=("LAY001",)))

    assert names_only.report.violations == []
    with pytest.raises(AnalysisError, match=r"^shop/broken\.py:1: syntax error: "):
        check(tmp_path, replace(BASE, select=("LAY004",)))
