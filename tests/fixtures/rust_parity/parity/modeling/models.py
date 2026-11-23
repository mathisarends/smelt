"""MOD007: every way to name an outside base."""

import external
import external.nested
import pydantic
import pydantic as pd
from external import Base as Foundation
from pydantic import BaseModel
from pydantic import BaseModel as Model

from . import vendor
from .vendor import Local
from ..modeling import vendor as climbing


def wrap(target):
    return target


class Manifest(BaseModel):
    pass


class Aliased(Model):
    pass


class ThroughModule(pydantic.BaseModel):
    pass


class ThroughAlias(pd.BaseModel):
    pass


class Generic(BaseModel[int]):
    pass


class Several(Manifest, BaseModel, external.Base):
    pass


class Twice(BaseModel, Model):
    pass


class Other(Foundation):
    pass


@wrap
@wrap
class DecoratedModel(BaseModel):
    pass


class Keyworded(metaclass=BaseModel):
    pass


class Unpacked(*[BaseModel]):
    pass


class Called(wrap(BaseModel)):
    pass


class Relative(Local, vendor.Thing, climbing.Thing):
    pass


class Deeper(external.nested.Base):
    pass


def factory():
    from pydantic import BaseModel as LocalModel

    class Nested(LocalModel):
        pass

    return Nested
