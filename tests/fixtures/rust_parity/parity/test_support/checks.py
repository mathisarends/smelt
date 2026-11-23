def check_result(value: object) -> None:
    if value is None:
        raise ValueError


async def guard(awaitable: object) -> object:
    return await awaitable
