"""A configured ignore hides the finding before the suppression sees it."""


def ignored(first, second, third):  # smelt: ignore[CPX003] -- already ignored in smelt.yml
    return first, second, third
