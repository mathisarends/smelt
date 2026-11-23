"""The module defining the project base is exempt for that base."""

import external
from pydantic import BaseModel


class Options(BaseModel):
    pass


class AlsoHere(BaseModel):
    pass


class StillReported(external.Base):
    pass
