from dataclasses import dataclass

import pydantic
from sqlalchemy.orm import Mapped


@dataclass
class VoiceSession:
    id: str
    duration: Mapped[int]
    meta: pydantic.BaseModel | None = None
