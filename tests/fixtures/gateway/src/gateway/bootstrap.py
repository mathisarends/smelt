from dishka import make_container

from gateway.features.voice.infra.sql import SqlVoiceSessionRepository

container = make_container(SqlVoiceSessionRepository)
