"""Consumers of the helpers."""
from app import helpers
from app.helpers import imported_elsewhere, Browser


def use():
    browser = Browser("p")
    return imported_elsewhere(), helpers.loaded_through_module, browser.shared, browser.used_outside
