"""A rebound import or wildcard makes settings and typing identities unknown."""

from typing import Literal

from pydantic_settings import BaseSettings

BaseSettings = object


class Settings(BaseSettings):
    password: str


Literal = list


class Event:
    type: Literal["shadowed"]
