from typing import Generic, ParamSpec, TypeVar, TypeVarTuple

Co = TypeVar("Co", covariant=True)
Dflt = TypeVar("Dflt", default=int)
Wrong = TypeVar("Other")
NotText = TypeVar(1)
NoArgs = TypeVar()
Spread = TypeVar("Spread", *ITEMS)
Keywords = TypeVar("Keywords", **OPTIONS)
PBound = ParamSpec("PBound", bound=int)
PExtra = ParamSpec("PExtra", int)
TsExtra = TypeVarTuple("TsExtra", int)
Tuple = TypeVarTuple("Tuple")
Plain = TypeVar("Plain")
Twice = TypeVar("Twice")
Twice = TypeVar("Twice")
Chained = Other = TypeVar("Chained")
Annotated: object = TypeVar("Annotated")
Foreign = Unrelated("Foreign")
Attr = typing.TypeVar("Attr")
NotCall = 1


class A(Generic[Co]):
    pass


class B(Generic[Dflt]):
    pass


class C(Generic[Wrong]):
    pass


class D(Generic[NotText]):
    pass


class E(Generic[NoArgs]):
    pass


class F(Generic[Spread]):
    pass


class G(Generic[Keywords]):
    pass


class H(Generic[PBound]):
    pass


class I(Generic[PExtra]):
    pass


class J(Generic[*TsExtra]):
    pass


class K(Generic[Tuple]):
    pass


class L(Generic[*Plain]):
    pass


class M(Generic[Twice]):
    pass


class N(Generic[Chained]):
    pass


class O(Generic[Annotated]):
    pass


class P(Generic[Foreign]):
    pass


class Q(Generic[Attr]):
    pass


class R(Generic[NotCall]):
    pass


class S(Generic[Missing]):
    pass


class U(Generic[int]):
    pass


class V(Generic["Plain"]):
    pass


class W[X](Generic[Plain]):
    pass
