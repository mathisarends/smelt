import sqlalchemy

from gateway.features.voice.application.ports import VoiceSessionRepository
from gateway.features.voice.domain.session import VoiceSession


class SqlVoiceSessionRepository(VoiceSessionRepository):
    def save(self, session: VoiceSession) -> None:
        sqlalchemy.text("insert")
