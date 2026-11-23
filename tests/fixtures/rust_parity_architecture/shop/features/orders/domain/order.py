import os
import pydantic
import requests
from attrs import validators
from attrs import define, validators as checks
from typing import TYPE_CHECKING
from . import events

if TYPE_CHECKING:
    import boto3
    from shop.features.orders.application.service import place
