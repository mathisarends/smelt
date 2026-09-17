from dishka import FromDishka

from gateway.features.billing.application.invoices import charge
from gateway.features.voice.domain.session import VoiceSession
from gateway.features.voice.infra.sql import SqlVoiceSessionRepository


def start(repo: FromDishka[SqlVoiceSessionRepository]) -> VoiceSession:
    session = VoiceSession("x", 0)
    repo.save(session)
    charge(session.id)
    return session
