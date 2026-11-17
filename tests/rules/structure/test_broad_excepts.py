from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT350",))


def _project(tmp_path: Path, body: str, *, config: str = CONFIG) -> Path:
    source = "def load(plugin: object) -> object:\n    try:\n        return plugin\n"
    return write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/plugin.py": source + body,
        },
    )


def _lines(root: Path) -> list[int | None]:
    return [item.line for item in violations(root, SELECT)]


def test_reports_silenced_handler_from_the_issue(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        "    except Exception:  # pylint: disable=broad-exception-caught\n"
        "        return None\n",
    )

    [found] = violations(root, SELECT)

    assert (found.code, found.path, found.line, found.column) == (
        "SMT350",
        "app/plugin.py",
        4,
        5,
    )
    assert found.expected == {"caught": "Exception", "automatic_rewrite": False}


@pytest.mark.parametrize(
    ("clause", "caught"),
    [
        ("except:", "bare"),
        ("except BaseException:", "BaseException"),
        ("except (ValueError, Exception) as error:", "Exception"),
        ("except builtins.Exception:", "Exception"),
        ("except* Exception:", "Exception"),
    ],
)
def test_reports_every_broad_handler_form(
    tmp_path: Path, clause: str, caught: str
) -> None:
    # except* forbids return, so the body only assigns.
    root = _project(tmp_path, f"    {clause}\n        value = None\n")

    [found] = violations(root, SELECT)

    assert found.expected == {"caught": caught, "automatic_rewrite": False}


@pytest.mark.parametrize(
    "body",
    [
        "    except Exception:  # A broken plugin must not stop the host.\n"
        "        return None\n",
        "    # A broken plugin must not stop the host.\n"
        "    except Exception:\n"
        "        return None\n",
        "    except Exception:\n"
        "        # A broken plugin must not stop the host.\n"
        "        return None\n",
        "    except Exception:\n"
        "        return None  # A broken plugin must not stop the host.\n",
        "    except Exception:  # noqa: BLE001 - a broken plugin is skipped\n"
        "        return None\n",
        "    except Exception:  # pylint: disable=broad-exception-caught  # skipped\n"
        "        return None\n",
        "    except (\n"
        "        Exception  # third-party plugins fail in any way\n"
        "    ):\n"
        "        return None\n",
    ],
    ids=[
        "inline",
        "above",
        "body",
        "trailing",
        "noqa-reason",
        "second-comment",
        "multiline",
    ],
)
def test_accepts_explained_handler(tmp_path: Path, body: str) -> None:
    assert _lines(_project(tmp_path, body)) == []


@pytest.mark.parametrize(
    "comment",
    [
        "# noqa: BLE001",
        "# noqa",
        "# pylint: disable=broad-exception-caught,bare-except",
        "# type: ignore[misc]",
        "# pragma: no cover",
        "# noqa: BLE001  # pragma: no cover",
        "# -",
    ],
)
def test_directives_alone_are_not_a_reason(tmp_path: Path, comment: str) -> None:
    body = f"    except Exception:  {comment}\n        return None\n"

    assert _lines(_project(tmp_path, body)) == [4]


def test_ignores_comments_outside_the_handler(tmp_path: Path) -> None:
    body = (
        "    except Exception:\n"
        "        value = None\n"
        "        # Explains the next statement, not the handler.\n"
        "        return value\n"
    )
    source = (
        "def load(plugin: object) -> object:\n"
        "    try:\n"
        "        return plugin  # trailing note on the try body\n"
    )
    root = write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/plugin.py": source + body},
    )

    assert _lines(root) == [4]


@pytest.mark.parametrize(
    "body",
    [
        "    except Exception:\n        raise\n",
        "    except Exception as error:\n"
        "        cleanup = plugin\n"
        "        raise RuntimeError(cleanup) from error\n",
        "    except Exception:\n"
        "        def report() -> None:\n"
        "            return None\n"
        "        report()\n"
        "        raise\n",
    ],
    ids=["reraise", "translate", "nested-return"],
)
def test_accepts_handler_that_always_propagates(tmp_path: Path, body: str) -> None:
    assert _lines(_project(tmp_path, body)) == []


def test_reports_handler_that_may_return_before_raising(tmp_path: Path) -> None:
    body = (
        "    except Exception:\n"
        "        if plugin:\n"
        "            return None\n"
        "        raise\n"
    )

    assert _lines(_project(tmp_path, body)) == [4]


def test_ignores_specific_handlers_and_tests(tmp_path: Path) -> None:
    test_source = (
        "def test_load() -> None:\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
    )
    root = write_project(
        tmp_path,
        {
            "smelt.yml": CONFIG,
            "app/__init__.py": "",
            "app/plugin.py": (
                "def load(plugin: object) -> object:\n"
                "    try:\n"
                "        return plugin\n"
                "    except (OSError, ValueError):\n"
                "        return None\n"
            ),
            "tests/test_plugin.py": test_source,
        },
    )

    assert _lines(root) == []


def test_suppression_reason_explains_the_handler(tmp_path: Path) -> None:
    body = (
        "    except Exception:  # smelt: ignore[SMT350] -- legacy loader\n"
        "        return None\n"
    )

    assert _lines(_project(tmp_path, body)) == []


def test_rule_is_opt_in_and_uses_configured_severity(tmp_path: Path) -> None:
    body = "    except Exception:\n        return None\n"
    assert violations(_project(tmp_path / "default", body)) == []
    config = CONFIG + "rules: {idioms/missing-broad-except-reason: warning}\n"
    [finding] = violations(_project(tmp_path / "enabled", body, config=config))
    assert (finding.code, finding.severity) == ("SMT350", Severity.WARNING)
