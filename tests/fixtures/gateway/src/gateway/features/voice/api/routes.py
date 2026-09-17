import fastapi

from gateway.bootstrap import container
from gateway.features.billing.domain.money import Money
from gateway.features.voice.application.session import start

router = fastapi.APIRouter()


def create() -> Money:
    start(container)
    return Money(0)
