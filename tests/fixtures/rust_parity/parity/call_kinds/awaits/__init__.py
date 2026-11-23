async def run(items):
    show(await items)
    show([item for item in items])
    show(sum(item for item in items))
