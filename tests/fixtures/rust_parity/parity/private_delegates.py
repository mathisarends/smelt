"""Methods that only forward to a private method."""

from abc import ABC
from typing import Generic, Protocol, TypeVar

import other

T = TypeVar("T")


class Frames:
    def events(self, *, run_id):
        return self._events(run_id=run_id)

    async def _events(self, *, run_id):
        yield run_id

    def documented(self, a, b):
        """Docs stay."""
        return self._documented(a, b)

    def _documented(self, a, b):
        return a, b

    def varargs(self, a, *rest, key, **options):
        return self._varargs(a, *rest, key=key, **options)

    def _varargs(self, a, *rest, key, **options):
        return a, rest, key, options

    def positional_only(self, a, /, b):
        return self._positional_only(a, b)

    def _positional_only(self, a, /, b):
        return a, b

    def keyword_passed(self, a):
        return self._keyword_passed(a=a)

    def _keyword_passed(self, a):
        return a

    def no_parameters(self):
        return self._no_parameters()

    def _no_parameters(self):
        return 1

    def extra_statement(self):
        print("validate")
        return self._extra_statement()

    def _extra_statement(self):
        return 1

    def changed(self, a):
        return self._changed(a + 1)

    def _changed(self, a):
        return a

    def swapped(self, a, b):
        return self._swapped(b, a)

    def _swapped(self, a, b):
        return a, b

    def missing(self, a, b):
        return self._missing(a)

    def _missing(self, a, b):
        return a, b

    def duplicated(self, a):
        return self._duplicated(a, a)

    def _duplicated(self, a):
        return a

    def renamed_parameter(self, a):
        return self._renamed_parameter(a)

    def _renamed_parameter(self, b):
        return b

    def other_kind(self, a):
        return self._other_kind(a)

    def _other_kind(self, *, a):
        return a

    def default_differs(self, a=1):
        return self._default_differs(a)

    def _default_differs(self, a=2):
        return a

    def wrong_star(self, *rest):
        return self._wrong_star(*other)

    def _wrong_star(self, *rest):
        return rest

    def wrong_double_star(self, **options):
        return self._wrong_double_star(**other)

    def _wrong_double_star(self, **options):
        return options

    def keyword_value(self, a):
        return self._keyword_value(a=1)

    def _keyword_value(self, a):
        return a

    def keyword_other(self, a, b):
        return self._keyword_other(a=b, b=a)

    def _keyword_other(self, a, b):
        return a, b

    def calls_public(self):
        return self.public()

    def public(self):
        return 1

    def calls_dunder(self):
        return self.__helper__()

    def __helper__(self):
        return 1

    def __call__(self):
        return self._from_dunder()

    def _from_dunder(self):
        return 1

    @property
    def decorated_caller(self):
        return self._decorated_caller()

    def _decorated_caller(self):
        return 1

    def decorated_callee(self):
        return self._decorated_callee()

    @staticmethod
    def _decorated_callee(self):
        return 1

    def not_a_method(self):
        return self._absent()

    def on_other_object(self, target):
        return target._on_other_object(target)

    def _on_other_object(self, target):
        return target

    def itself(self):
        return self._itself()

    def _itself(self):
        return self._itself()

    def no_receiver():
        return self._no_receiver()

    def _no_receiver():
        return 1

    def star_receiver(*args):
        return args[0]._star_receiver()

    def _star_receiver(*args):
        return 1

    def returns_nothing(self):
        return

    def not_returned(self):
        self._not_returned()

    def _not_returned(self):
        return 1


class References:
    def by_name(self):
        return self._by_name()

    def _by_name(self):
        return 1

    def by_string(self):
        return self._by_string()

    def _by_string(self):
        return 1

    def by_fstring(self):
        return self._by_fstring()

    def _by_fstring(self):
        return 1

    def by_format_spec(self):
        return self._by_format_spec()

    def _by_format_spec(self):
        return 1

    def by_concatenation(self):
        return self._by_concatenation()

    def _by_concatenation(self):
        return 1

    def by_template(self):
        return self._by_template()

    def _by_template(self):
        return 1

    def by_attribute(self):
        return self._by_attribute()

    def _by_attribute(self):
        return 1

    def not_by_fragment(self):
        return self._not_by_fragment()

    def _not_by_fragment(self):
        return 1

    def not_by_interpolated(self):
        return self._not_by_interpolated()

    def _not_by_interpolated(self):
        return 1

    def not_by_bytes(self):
        return self._not_by_bytes()

    def _not_by_bytes(self):
        return 1

    def not_by_keyword(self):
        return self._not_by_keyword()

    def _not_by_keyword(self):
        return 1

    def defined_twice(self):
        return self._defined_twice()

    def _defined_twice(self):
        return 1

    def _defined_twice(self):
        return 2


def _by_name():
    return "reference to the method name"


USES = (
    _by_name,
    "_by_string",
    f"_by_fstring{1}",
    f"{1:_by_format_spec}",
    "_by_conc" "atenation",
    t"_by_template{1}",
    other._by_attribute,
    f"_not_by_fragment_{1}",
    f"{'_not_by_interpolated'!r:>10}" if False else 0,
    b"_not_by_bytes",
    dict(_not_by_keyword=1),
)


def _defined_twice():
    return 3


class Neutral(ABC):
    def forward(self):
        return self._neutral_forward()

    def _neutral_forward(self):
        return 1


class WithProtocol(Protocol):
    def forward(self):
        return self._protocol_forward()

    def _protocol_forward(self):
        return 1


class WithGeneric(Generic[T]):
    def forward(self):
        return self._generic_forward()

    def _generic_forward(self):
        return 1


class WithObject(object):
    def forward(self):
        return self._object_forward()

    def _object_forward(self):
        return 1


class DottedNeutral(other.ABC):
    def forward(self):
        return self._dotted_forward()

    def _dotted_forward(self):
        return 1


class SubscriptedNeutral(other.Generic[T]):
    def forward(self):
        return self._subscripted_forward()

    def _subscripted_forward(self):
        return 1


class External(other.Base):
    def forward(self):
        return self._external_forward()

    def _external_forward(self):
        return 1


class UnknownBase(Imported):
    def forward(self):
        return self._unknown_forward()

    def _unknown_forward(self):
        return 1


class CallBase(make()):
    def forward(self):
        return self._call_forward()

    def _call_forward(self):
        return 1


class SubscriptedCall(make()[T]):
    def forward(self):
        return self._subscripted_call_forward()

    def _subscripted_call_forward(self):
        return 1


class LocalParent:
    pass


class LocalChild(LocalParent):
    def forward(self):
        return self._local_forward()

    def _local_forward(self):
        return 1


class ExternalParent(other.Base):
    pass


class ExternalChild(ExternalParent):
    def forward(self):
        return self._external_child_forward()

    def _external_child_forward(self):
        return 1


class SelfBase(SelfBase):
    def forward(self):
        return self._self_base_forward()

    def _self_base_forward(self):
        return 1


class SubscriptedLocal(LocalParent[T]):
    def forward(self):
        return self._subscripted_local_forward()

    def _subscripted_local_forward(self):
        return 1


class Keywords(metaclass=other.Meta):
    def forward(self):
        return self._metaclass_forward()

    def _metaclass_forward(self):
        return 1


class Shadowed:
    class Duplicate:
        pass


class Duplicate(other.Base):
    pass


class UsesDuplicate(Duplicate):
    def forward(self):
        return self._duplicate_forward()

    def _duplicate_forward(self):
        return 1


class Replaced:
    def forward(self):
        return self._replaced_forward()

    def forward(self, value):
        return value

    def _replaced_forward(self):
        return 1
