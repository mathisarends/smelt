"""Settings and Dishka provider shapes for FWK001 and FWK002."""

import typing as t
from typing import Annotated, Optional, Union

import dishka as di
import pydantic.v1
import pydantic_settings
from dishka import Provider, Scope, provide
from dishka import provide as registered
from pydantic import BaseSettings as LegacySettings
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_key: str
    database_password: str | None = None
    refresh_token: "str | bytes"
    private_key: Optional[bytes] = None
    SECRET: Annotated[str, "not bytes", bytes]
    union_token: Union[str, int]
    quoted_token: "Annotated['str', bytes]"
    broken_token: "str |"
    indented_token: " str"
    public_key: str
    token_count: int
    tokenizer: str
    passwd: t.Optional[str]
    typed_secret: t.Annotated[str, 1]
    nested: dict[str, str]


class Child(Settings):
    access_token: str


class Grandchild(Child):
    client_secret: bytes


class Aliased(LegacySettings):
    password: str


class Dotted(pydantic_settings.BaseSettings):
    token: str


class DottedLegacy(pydantic.v1.BaseSettings):
    token: str


@staticmethod
class Decorated(BaseSettings):
    password: str


class Unrelated:
    password: str


class Forwarding(Provider):
    scope = Scope.REQUEST

    @provide
    def query(self, session: Session) -> Query:
        return SqlQuery(session)

    @provide(scope=Scope.APP, cache=False)
    def options(self, first: A, *, second: B) -> Interface:
        return module.Implementation(first, second=second)

    @registered(provides=Other)
    def renamed(self, value: A) -> Interface:
        return Impl(value=value)

    @di.provide
    def dotted(self, one: A, two: B) -> pkg.Port:
        return pkg.sub.Adapter(one, two=two)

    @provide
    def no_dependencies(self) -> Clock:
        return SystemClock()

    @provide
    def lowercase(self, value: A) -> Query:
        return factory(value)

    @provide
    def transformed(self, value: A) -> Query:
        return Impl(value.inner)

    @provide
    def reordered(self, one: A, two: B) -> Query:
        return Impl(two, one)

    @provide
    def duplicated(self, one: A) -> Query:
        return Impl(one, one=one)

    @provide
    def defaulted(self, one: A = None) -> Query:
        return Impl(one)

    @provide
    def untyped(self, one) -> Query:
        return Impl(one)

    @provide
    def generic(self, one: A) -> list[Query]:
        return Impl(one)

    @provide
    async def asynchronous(self, one: A) -> Query:
        return Impl(one)

    @provide(unknown=True)
    def unknown_option(self, one: A) -> Query:
        return Impl(one)

    @provide(Query)
    def positional_option(self, one: A) -> Query:
        return Impl(one)

    @provide
    @other
    def stacked(self, one: A) -> Query:
        return Impl(one)

    @provide
    def body(self, one: A) -> Query:
        one.prepare()
        return Impl(one)


class Mixed(Provider, Other):
    @provide
    def query(self, session: Session) -> Query:
        return SqlQuery(session)
