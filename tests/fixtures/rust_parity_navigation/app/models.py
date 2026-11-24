"""Records, services and namespaces."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, List, Optional

from pydantic import BaseModel


class Service:
    def run(self): ...


@dataclass
class Inner:
    root: Path


@dataclass
class Settings:
    path: Path
    names: list[str]
    service: Service
    nested: "Inner"
    spaced: " Inner"
    maybe: Optional[Path]
    annotated: Annotated[Path, "meta"]
    union: Path | None
    reversed: None | Path
    either: Path | str
    legacy: List[str]


@dataclasses.dataclass(frozen=True)
class Other:
    values: dict


class Derived(Settings):
    extra: Path


class Model(BaseModel):
    path: Path
    child: "Model"


@dataclass
class L0:
    value: Path


class L1(L0):
    pass


class L2(L1):
    pass


class L3(L2):
    pass


class L4(L3):
    pass


class L5(L4):
    pass


class L6(L5):
    pass


class L7(L6):
    pass


class L8(L7):
    pass


class L9(L8):
    pass


class L10(L9):
    pass


Loop = L10
