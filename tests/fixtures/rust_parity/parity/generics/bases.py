from typing import Generic, TypeVar

T = TypeVar("T")


class AfterBase(Base, Generic[T]):
    pass


class BeforeBase(Generic[T], Base):
    pass


class WithMetaclass(Generic[T], metaclass=Meta):
    pass


class MetaclassFirst(metaclass=Meta, *Other):
    pass


class Middle(Base, Generic[T], metaclass=Meta):
    pass


class SpacedName (Generic[T]):
    pass


class   ManySpaces(Generic[T]):
    pass


class Trailing(Generic[T],):
    pass


class MultiLine(
    Generic[T],
):
    pass


class MultiLineTwo(
    Base,
    Generic[T],
):
    pass


class CommentAfter(
    Generic[T],  # comment
    Base,
):
    pass


class CommentBefore(
    Base,  # comment
    Generic[T],
):
    pass


class CommentInside(
    Generic[T]  # comment
):
    pass


class Continued\
    (Generic[T]):
    pass


class ParenthesizedBase((Generic[T])):
    pass


class ParenthesizedTwo((Generic[T]), Base):
    pass


@decorator
@another(1)
class Decorated(Generic[T]):
    pass


class TwoGenerics(Generic[T], Generic[T]):
    pass


class Duplicate(Generic[T, T]):
    pass


class Plain(Base):
    pass


class Unsubscripted(Generic):
    pass


def scope():
    class Nested(Generic[T]):
        pass

    return Nested
