from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.analysis.installed_exports import InstalledPackages
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path


def _installed(site: Path, files: dict[str, str]) -> InstalledPackages:
    write_project(site, files)
    return InstalledPackages(search_path=[str(site)])


def test_direct_reexports_map_to_their_implementation(tmp_path: Path) -> None:
    installed = _installed(
        tmp_path,
        {
            "web/__init__.py": "from .routing import Router as Router\n"
            "from .app import App\n",
            "web/routing.py": "class Router: ...\n",
        },
    )

    assert installed.exports("web") == {
        "Router": "web.routing.Router",
        "App": "web.app.App",
    }


@pytest.mark.parametrize(
    ("initializer", "expected"),
    [
        ("from .main import *\n", {"Model": "lib.main.Model"}),
        (
            "__all__ = ['Model']\nfrom .main import *\n"
            "def __dir__(): return list(__all__)\n",
            {"Model": "lib.main.Model"},
        ),
        (
            "__all__ = ['Model']\n__all__ += ['Extra']\nfrom .main import *\n",
            {"Model": "lib.main.Model"},
        ),
        ("__all__ = ['Other']\nfrom .main import *\n", {}),
        ("from .main import *\nfrom .other import Model\n", {}),
        ("from .untyped import *\n", {}),
    ],
)
def test_star_imports_count_through_a_static_all(
    tmp_path: Path, initializer: str, expected: dict[str, str]
) -> None:
    installed = _installed(
        tmp_path,
        {
            "lib/__init__.py": initializer,
            "lib/main.py": "__all__ = ('Model', '_hidden')\nclass Model: ...\n",
            "lib/untyped.py": "class Model: ...\n",
        },
    )

    assert installed.exports("lib") == expected


LAZY = (
    "from typing import TYPE_CHECKING\n"
    "if TYPE_CHECKING:\n"
    "    from .main import Model\n"
    "    from .main import *\n"
)


@pytest.mark.parametrize(
    ("initializer", "expected"),
    [
        (
            LAZY + "__all__ = ('Model', 'Field')\ndef __getattr__(name): ...\n",
            {"Model": "lib.main.Model", "Field": "lib.main.Field"},
        ),
        (
            "import typing\nif typing.TYPE_CHECKING:\n    from .main import Field\n"
            "__all__ = ['Field']\ndef __getattr__(name): ...\n",
            {"Field": "lib.main.Field"},
        ),
        (LAZY + "__all__ = ('Field',)\n", {}),
        (LAZY + "def __getattr__(name): ...\n", {}),
    ],
)
def test_type_checking_imports_need_a_lazy_getattr_and_all(
    tmp_path: Path, initializer: str, expected: dict[str, str]
) -> None:
    installed = _installed(
        tmp_path,
        {
            "lib/__init__.py": initializer,
            "lib/main.py": "__all__ = ['Field']\nclass Model: ...\nclass Field: ...\n",
        },
    )

    assert installed.exports("lib") == expected


def test_stubs_take_precedence_and_need_explicit_reexports(tmp_path: Path) -> None:
    installed = _installed(
        tmp_path,
        {
            "lib/__init__.py": "from .core import Hidden, Shown\n",
            "lib/__init__.pyi": "from .core import Hidden\nfrom .core import Shown as Shown\n",
        },
    )

    assert installed.exports("lib") == {"Shown": "lib.core.Shown"}


@pytest.mark.parametrize(
    "initializer",
    [
        "from .core import Model\nModel = Model\n",
        "from .core import Model\nfrom .other import Model\n",
        "from .core import _Model\n",
        "from .core import Model\n__all__ = [name for name in ()]\n",
        "from .core import Model\n__all__ = ['Model']\n__all__.append('Other')\n",
        "from .core import Model\n__all__ = ['Model']\ndel __all__\n",
        "from .core import (\n",
    ],
)
def test_ambiguous_or_unreadable_initializers_export_nothing(
    tmp_path: Path, initializer: str
) -> None:
    installed = _installed(tmp_path, {"lib/__init__.py": initializer})

    assert installed.exports("lib") == {}


def test_subpackages_resolve_relative_imports(tmp_path: Path) -> None:
    installed = _installed(
        tmp_path,
        {
            "lib/__init__.py": "",
            "lib/orm/__init__.py": "from .session import Session\n",
        },
    )

    assert installed.exports("lib.orm") == {"Session": "lib.orm.session.Session"}
    assert installed.exports("lib.missing") == {}
    assert installed.exports("absent") == {}


def test_the_first_regular_package_on_the_search_path_wins(tmp_path: Path) -> None:
    write_project(
        tmp_path,
        {
            "first/lib/__init__.py": "from .a import Model\n",
            "second/lib/__init__.py": "from .b import Model\n",
            "namespace/other/core.py": "",
        },
    )
    installed = InstalledPackages(
        search_path=["", str(tmp_path / "first"), str(tmp_path / "second")]
    )

    assert installed.exports("lib") == {"Model": "lib.a.Model"}
    assert installed.exports("other") == {}


def test_identity_follows_installed_sources(tmp_path: Path) -> None:
    installed = _installed(tmp_path, {"lib/__init__.py": "", "lib/core.py": ""})
    before = installed.identity(["lib", "absent"])

    write_project(tmp_path, {"lib/core.py": "class Model: ...\n"})

    after = installed.identity(["absent", "lib"])
    assert after["absent"] is None
    assert after["lib"] is not None
    assert before["lib"] != after["lib"]
    assert installed.identity(["lib"]) == {"lib": after["lib"]}
