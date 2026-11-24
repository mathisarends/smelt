import shop.tools.enums.kinds
import shop.tools.enums.kinds as direct
from shop.tools import enums as package
from shop.tools.enums import Color as Reexported
from shop.tools.enums import Exported

from . import kinds
from .kinds import Color, Dotted as Short

values = (
    Color.RED.value,
    Short.ONE.value,
    kinds.Color.RED.value,
    direct.Color.GREEN.value,
    package.Color.RED.value,
    package.kinds_module.Color.RED.value,
    package.Exported.ONE.value,
    Reexported.GREEN.value,
    Exported.ONE.value,
    shop.tools.enums.Color.RED.value,
    shop.tools.enums.kinds.Color.TYPED.value,
    Color.MISSING.value,
    unknown.Color.RED.value,
    call().RED.value,
    items[0].RED.value,
)
