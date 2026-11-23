"""MOD007: resolving bases through relative imports from a package."""

from pydantic import BaseModel

from .base import Options as Options
from .models import Manifest as Manifest


class FromPackage(BaseModel):
    pass
