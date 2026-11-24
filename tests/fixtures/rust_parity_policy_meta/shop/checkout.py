from typing import TYPE_CHECKING

from shop.billing.invoice import Invoice
from shop.hidden.secret import Secret
from shop.orders.order import Draft

if TYPE_CHECKING:
    from shop.catalog.items import Price


def pay(price: "Price"):
    return Invoice, Secret, Draft, price
