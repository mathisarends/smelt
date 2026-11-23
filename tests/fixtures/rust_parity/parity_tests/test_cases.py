from parity.test_support.service import Service, normalize


def test_literal_cases():
    for value, expected in [("A", "a"), ("B", "b")]:
        result = normalize(value)
        assert result == expected


def test_tuple_cases():
    for value in ("A", "B", "C"):
        assert normalize(value)


def test_dict_cases():
    for case in [{"a": 1}, {"b": 2, **{}}]:
        assert normalize(case)


def test_one_case():
    for value in ["A"]:
        assert normalize(value)


def test_thirteen_cases():
    for value in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]:
        assert normalize(value)


def test_twelve_cases():
    for value in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]:
        assert normalize(value)


def test_non_literal(name):
    for value in [name, "B"]:
        assert normalize(value)


def test_stateful_receiver():
    service = Service()
    for value in ["A", "B"]:
        assert service.plain(value)


def test_local_receiver():
    for value in ["A", "B"]:
        service = Service()
        assert service.plain(value)


def test_carried_state():
    for value in ["A", "B"]:
        assert previous == value
        previous = normalize(value)


def test_no_call():
    for value in ["A", "B"]:
        assert value


def test_no_assertion_in_loop():
    for value in ["A", "B"]:
        result = normalize(value)


def test_control_flow():
    for value in ["A", "B"]:
        if value:
            assert normalize(value)


def test_annotated_assignment():
    for value in ["A", "B"]:
        result: str = normalize(value)
        assert result


def test_bare_annotation():
    for value in ["A", "B"]:
        result: str
        assert normalize(value)


def test_attribute_target(holder):
    for value in ["A", "B"]:
        holder.value = normalize(value)
        assert holder


def test_loop_else():
    for value in ["A", "B"]:
        assert normalize(value)
    else:
        assert True


async def test_async_loop(source):
    async for value in source:
        assert normalize(value)


def test_nested_literal_case():
    for value in [[1, (2, 3)], [4, (5, 6)]]:
        assert normalize(value)
