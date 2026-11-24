from shop.billing.invoice import Invoice
from shop.catalog.items import Item
from shop.hidden.secret import Secret
from shop.orders.order import Draft, Order


def total():
    return Invoice, Item, Secret, Order, Draft
