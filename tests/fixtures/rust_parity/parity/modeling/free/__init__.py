"""An override with `bases: {}` allows direct inheritance here."""

from pydantic import BaseModel


class Allowed(BaseModel):
    pass
