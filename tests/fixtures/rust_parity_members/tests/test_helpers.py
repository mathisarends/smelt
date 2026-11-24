from app.helpers import tested_only, tested_and_internal, Browser
from app import helpers


def test_functions():
    assert tested_only() == 4
    assert tested_and_internal() == 5
    assert helpers.unused() == 1


def test_attribute():
    browser = Browser("p")
    assert browser.tested == 0
    assert helpers.Browser.limit == 3
