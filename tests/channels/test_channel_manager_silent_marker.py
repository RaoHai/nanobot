"""Tests for [LOCAL] silent-marker suppression in ChannelManager.

Models signal "say nothing" by emitting a marker such as [SILENT]; the
manager must drop such messages instead of delivering the marker as text.
"""

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.outbound_events import (
    ProgressEvent,
    outbound_message_for_event,
)
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.manager import ChannelManager, _is_silent_marker
from nanobot.config.schema import Config


class MockChannel(BaseChannel):
    name = "mock"
    display_name = "Mock"

    async def start(self):
        pass

    async def stop(self):
        pass

    async def send(self, msg):
        pass


@pytest.fixture
def manager():
    config = Config.model_validate({"channels": {"websocket": {"enabled": False}}})
    return ChannelManager(config, MessageBus())


class TestIsSilentMarker:
    @pytest.mark.parametrize(
        "content",
        [
            "[SILENT]",
            "SILENT",
            "  [silent]  ",
            "[Silent]",
            "**[SILENT]**",
            "*[no_response]*",
            "[NO_REPLY]",
            "[skip]",
        ],
    )
    def test_marker_variants_suppressed(self, content):
        assert _is_silent_marker(content) is True

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "I'll stay quiet [SILENT] for now",
            "[SILENT] but also some text",
            "hello",
            "[SILENT",
            "SILENT]",
            "[SILENTS]",
        ],
    )
    def test_non_marker_content_not_suppressed(self, content):
        assert _is_silent_marker(content) is False


class TestShouldDropSilent:
    def _msg(self, content: str, *, msg_type: str = "final") -> OutboundMessage:
        return OutboundMessage(
            channel="mock", chat_id="chat1", content=content, msg_type=msg_type,
        )

    def test_marker_message_dropped(self, manager):
        assert manager._should_drop_silent(self._msg("[SILENT]")) is True

    def test_bare_marker_variant_dropped(self, manager):
        assert manager._should_drop_silent(self._msg(" silent ")) is True

    def test_silent_msg_type_dropped(self, manager):
        assert manager._should_drop_silent(self._msg("anything", msg_type="silent")) is True

    def test_normal_message_kept(self, manager):
        assert manager._should_drop_silent(self._msg("hello world")) is False

    def test_marker_inside_text_kept(self, manager):
        assert manager._should_drop_silent(self._msg("said [SILENT] aloud")) is False

    def test_event_messages_kept(self, manager):
        msg = outbound_message_for_event(
            channel="mock", chat_id="chat1", event=ProgressEvent(content="[SILENT]"),
        )
        assert manager._should_drop_silent(msg) is False

    def test_empty_content_kept(self, manager):
        assert manager._should_drop_silent(self._msg("")) is False
