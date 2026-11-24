from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from importlib.metadata import version
__version__ = version("pkg")
