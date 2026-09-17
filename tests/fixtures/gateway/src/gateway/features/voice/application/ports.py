from typing import Protocol

from gateway.features.voice.domain.session import VoiceSession


class VoiceSessionRepository(Protocol):
    def save(self, session: VoiceSession) -> None: ...
