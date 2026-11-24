"""Callers outside the facade packages."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.sub import Wide
from app.sub.impl import Thing
from app.sub.impl import Thing, Second  # keep
from app.sub.impl import Third
from app.sub.impl import Thing as Alias, Second
from app.sub.impl import Private
from app.sub.impl import Thing, Private
from app.sub.impl import _hidden
from app.sub.impl import *
from app.sub.deeper.mod import Deep
from app.models import Record, Other
from app.models import Unlisted
from app.twice import Twice
from app.defined import Defined
from app.dynamic.inner import Hidden
from app.star.inner import Kept
from app.listed.inner import Kept as Listed
from .sub.impl import Thing as Relative
from .sub.impl import (
    Second as Wrapped,
)
x = 1; from app.sub.impl import Thing as Inline
from app.sub.impl \
    import Thing as Continued

from pydantic.main import BaseModel
from pydantic.fields import Field, FieldInfo
from pydantic.networks import AnyUrl
from pydantic.main import create_model as make
from yaml.loader import SafeLoader
from yaml.error import YAMLError
from yaml.nodes import Node
from missingpkg.sub import Missing
from annotated_types.base import Unlisted3
from pydantic_core.core_schema import CoreSchema

if TYPE_CHECKING:
    from app.sub import Wide
    from app.sub.impl import Thing as Guarded


def function():
    from app.sub import Wide
    from app.sub.impl import Second as Local
    return Local, Wide


try:
    from app.sub.impl import Thing as Tried
except ImportError:
    pass
