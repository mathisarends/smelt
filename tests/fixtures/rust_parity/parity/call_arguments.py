"""Awaited results and collection transformations passed as arguments."""


async def awaited(browser, value):
    show(await browser.read())
    show(first=await browser.read())
    show(1, await browser.read(), key=await browser.read())
    show(await browser.read(await browser.other()))
    show(**await browser.options())
    show(*await browser.options())
    show((await browser.read()))
    show(await browser.read() + 1)
    await show(await value)
    browser.pages[0].load(await value)
    (lambda: show)()(await value)
    this.is_.a.very.long.chain.of.attributes.that.goes.on.plus.on.beyond.sixty.characters(await value)
    exactly.sixty.characters.long.callee.name.fur.preview.limitx(await value)
    exactly.sixty.characters.long.callee.name.fur.preview.limit_x(await value)
    return show(await value)


async def conditional_awaits(value, flag):
    a = show(await value) if flag else None
    b = flag and show(await value)
    c = flag or show(await value)
    d = lambda: show(value)
    e = [show(await item) for item in value]
    f = {show(await item) for item in value}
    g = {item: show(await item) for item in value}
    h = list(show(await item) for item in value)
    while show(await value):
        show(await value)
    else:
        show(await value)
    if show(await value):
        show(await value)
    elif show(await value):
        pass
    for item in show(await value):
        show(await item)
    with show(await value) as held:
        pass
    assert show(await value), show(await value)
    i = 1 < show(await value) < 3
    try:
        show(await value)
    except show(await value):
        show(await value)
    match show(await value):
        case 1 if show(await value):
            show(await value)
    return a, b, c, d, e, f, g, h, held, i


def transformations(items, flag):
    show([item for item in items])
    show({item for item in items})
    show({item: 1 for item in items})
    show(item for item in items)
    show(first=[item for item in items])
    show(1, [item for item in items], key={item for item in items})
    show(*[item for item in items])
    show(**{item: 1 for item in items})
    show([item for item in items] + [])
    show(([item for item in items]))
    show(sum(item for item in items))
    show(total=sum(item for item in items))
    show(all(item for item in items))
    show(any(item for item in items))
    show(max(item for item in items))
    show(min((item for item in items), default=0))
    show(sorted((item for item in items), key=len))
    show(sum(item for item in items) + 1)
    show(sum([item for item in items]))
    show(sum(items))
    show(sum())
    show(sum((item for item in items), *flag))
    show(sum((item for item in items), **flag))
    show(sum(*items))
    show(len(item for item in items))
    show(builtins.sum(item for item in items))
    outer(inner([item for item in items]))
    sum(item for item in items)


async def asynchronous_generators(items):
    show(sum([item async for item in items]))
    show(sum(item async for item in items))
    show([item async for item in items])


def skipped_scopes(items, flag):
    a = show([item for item in items]) if flag else None
    b = flag and show([item for item in items])
    c = lambda: show([item for item in items])
    d = [show([x for x in item]) for item in items]
    e = {show([x for x in item]) for item in items}
    f = {item: show([x for x in item]) for item in items}
    g = list(show([x for x in item]) for item in items)
    h = 1 < show([item for item in items]) < 3
    i = 1 < show([item for item in items])
    while show([item for item in items]):
        show([item for item in items])
    else:
        show({item for item in items})
    assert show([item for item in items]), show({item for item in items})
    return a, b, c, d, e, f, g, h, i


@decorator([item for item in ()])
def decorated(value=show([item for item in ()])) -> show([item for item in ()]):
    pass


class Configured(Base, key=show([item for item in ()])):
    field = show([item for item in ()])
