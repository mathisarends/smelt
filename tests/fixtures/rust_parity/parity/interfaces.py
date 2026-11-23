"""Abstract and Protocol members with and without a contract."""

import abc
import typing
from abc import ABC, abstractmethod, abstractproperty
from typing import Generic, Protocol, TypeVar, overload, override

T = TypeVar("T")


class Port(ABC):
    @abstractmethod
    def ellipsis(self): ...

    @abstractmethod
    def passes(self):
        pass

    @abstractmethod
    def raises_name(self):
        raise NotImplementedError

    @abstractmethod
    def raises_call(self):
        raise NotImplementedError("subclass")

    @abstractmethod
    def raises_from(self):
        raise NotImplementedError from None

    @abstractmethod
    def raises_other(self):
        raise ValueError

    @abstractmethod
    def several(self):
        pass
        ...

    @abstractmethod
    def number(self):
        1

    @abstractmethod
    def documented(self):
        """States the contract."""

    @abstractmethod
    def concatenated(self):
        "States " "the contract."

    @abstractmethod
    def bytes_first(self):
        b"not a docstring"

    @abstractmethod
    def formatted(self):
        f"not a docstring"

    @abc.abstractmethod
    async def dotted(self): ...

    @property
    @abstractmethod
    def prop(self): ...

    @prop.setter
    @abstractmethod
    def prop(self, value): ...

    @prop.deleter
    @abstractmethod
    def prop(self): ...

    @abstractproperty
    def old_property(self): ...

    @abc.abstractclassmethod
    def old_class(cls): ...

    @abc.abstractstaticmethod
    def old_static(): ...

    @abstractmethod
    @override
    def overridden(self): ...

    @abstractmethod
    def __enter__(self): ...

    @abstractmethod
    def __call__(self): ...

    @abstractmethod
    def __(self): ...

    def concrete(self): ...


class Shape(Protocol):
    size: int

    def area(self) -> float: ...

    def documented(self) -> float:
        """The area."""

    def implemented(self):
        return 1

    @overload
    def many(self, value: int) -> int: ...

    @overload
    def many(self, value: str) -> str: ...

    def many(self, value): ...

    def __len__(self): ...

    def __call__(self): ...


class Parametrized(Protocol[T]):
    def get(self) -> T: ...


class Dotted(typing.Protocol):
    def get(self): ...


class Combined(Generic[T], Protocol):
    def get(self): ...


class NotProtocol(make_base()):
    def get(self): ...


class Outer:
    class Inner(Protocol):
        def nested(self): ...

    def method(self):
        class Local(ABC):
            @abstractmethod
            def local(self): ...

        return Local
