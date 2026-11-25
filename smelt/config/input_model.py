from pydantic import BaseModel, ConfigDict


class InputModel(BaseModel):
    """Reject unknown fields in documents read by Smelt."""

    model_config = ConfigDict(extra="forbid")
