from typing import ParamSpec, TypeVar
import typing_extensions as extensions


def _private():
    return 1


T = TypeVar("T", bound="int")
P = ParamSpec("P")


def public():
    return 1


U = extensions.TypeVar("U", bound=int)
