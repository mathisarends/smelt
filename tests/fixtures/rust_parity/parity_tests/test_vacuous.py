from parity.test_support.service import normalize


def test_loop_unknown(values):
    for value in values:
        assert value


def test_loop_nonempty_literal():
    for value in [1, 2]:
        assert value


def test_loop_empty_literal():
    for value in []:
        assert value


def test_loop_starred(values):
    for value in [*values]:
        assert value


def test_loop_named_literal():
    values = [1, 2]
    for value in values:
        assert value


def test_loop_mutated_literal(values):
    collected = [1]
    collected.clear()
    for value in collected:
        assert value


def test_loop_tuple_name():
    values = (1, 2)
    for value in values:
        assert value


def test_loop_string():
    for value in "ab":
        assert value


def test_loop_empty_string():
    for value in "":
        assert value


def test_ranges():
    for value in range(2):
        assert value is not None
    for value in range(1, 3):
        assert value
    for value in range(3, 1, -1):
        assert value
    for value in range(1_000_000_000_000_000_000_000, 1_000_000_000_000_000_000_001):
        assert value


def test_empty_range():
    for value in range(0):
        assert value


def test_negative_range():
    for value in range(-1):
        assert value


def test_zero_step():
    for value in range(0, 1, 0):
        assert value


def test_backwards_range():
    for value in range(1, 3, -1):
        assert value


def test_range_keyword(stop):
    for value in range(stop):
        assert value


def test_all_unknown(values):
    assert all(values)


def test_all_generator(values):
    assert all(value for value in values)


def test_all_filtered():
    assert all(value for value in [1, 2] if value)


def test_all_nonempty():
    assert all([1, 2])


def test_all_generator_nonempty():
    assert all(value for value in [1, 2])


def test_all_nested_generators(values):
    assert all(left for right in [1] for left in values)


def test_loop_and_direct(values):
    assert normalize("A")
    for value in values:
        assert value


def test_two_loops(left, right):
    for value in left:
        assert value
    for value in right:
        assert value


def test_nested_loop(left):
    for value in [1]:
        for other in left:
            assert value == other


def test_loop_else(values):
    for value in values:
        pass
    else:
        assert values


def check_each(values):
    for value in values:
        assert value


def check_fixed():
    for value in [1, 2]:
        assert value


def test_helper_loop(values):
    check_each(values)


def test_helper_fixed():
    check_fixed()


def test_helper_in_loop(values):
    for value in values:
        check_fixed()
