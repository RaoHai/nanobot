"""Tests for [LOCAL] silent-marker stream holdback in TurnDelivery.

A turn whose whole reply is a stay-silent marker must never publish stream
deltas, so the marker can't leak to a channel mid-stream. Deltas are
buffered while the accumulated text is still a possible marker prefix and
flushed as soon as it diverges.
"""

import asyncio

import pytest

from nanobot.agent.turn_delivery import TurnDeliveryFactory
from nanobot.bus.events import InboundMessage
from nanobot.bus.outbound_events import StreamDeltaEvent, StreamEndEvent
from nanobot.bus.queue import MessageBus
from nanobot.bus.runtime_events import RuntimeEventBus
from nanobot.utils.silent_markers import is_silent_marker_prefix


def _make_delivery(bus: MessageBus):
    factory = TurnDeliveryFactory(bus, RuntimeEventBus())
    msg = InboundMessage(
        channel="telegram",
        sender_id="user",
        chat_id="chat1",
        content="hi",
        metadata={"_wants_stream": True},
    )
    return factory.create(msg, msg.session_key, enable_stream=True)


async def _drain(bus: MessageBus):
    msgs = []
    while True:
        try:
            msgs.append(await asyncio.wait_for(bus.consume_outbound(), timeout=0.05))
        except asyncio.TimeoutError:
            return msgs


class TestIsSilentMarkerPrefix:
    @pytest.mark.parametrize(
        "text",
        ["", "[", "[sil", "[silent", "SILENT", "[silent]", "**", "**[silent]", "no", "sk"],
    )
    def test_prefixes_held(self, text):
        assert is_silent_marker_prefix(text) is True

    @pytest.mark.parametrize(
        "text",
        ["hello", "[silent] extra", "skipped", "nope", "SILENT]"],
    )
    def test_diverged_not_held(self, text):
        assert is_silent_marker_prefix(text) is False


@pytest.mark.asyncio
async def test_marker_stream_publishes_nothing():
    bus = MessageBus()
    delivery = _make_delivery(bus)

    for delta in ("[", "SIL", "ENT", "]"):
        await delivery.on_stream(delta)
    await delivery.on_stream_end()

    assert await _drain(bus) == []
    assert delivery._stream_open is False


@pytest.mark.asyncio
async def test_bare_marker_stream_publishes_nothing():
    bus = MessageBus()
    delivery = _make_delivery(bus)

    await delivery.on_stream("silent")
    await delivery.on_stream_end()

    assert await _drain(bus) == []


@pytest.mark.asyncio
async def test_normal_text_flushes_immediately():
    bus = MessageBus()
    delivery = _make_delivery(bus)

    await delivery.on_stream("He")
    await delivery.on_stream("llo")
    await delivery.on_stream_end()

    msgs = await _drain(bus)
    kinds = [type(m.event) for m in msgs]
    assert kinds == [StreamDeltaEvent, StreamDeltaEvent, StreamEndEvent]
    assert [m.content for m in msgs[:2]] == ["He", "llo"]


@pytest.mark.asyncio
async def test_marker_like_text_flushed_once_diverged():
    bus = MessageBus()
    delivery = _make_delivery(bus)

    await delivery.on_stream("sk")
    assert await _drain(bus) == []  # still a possible "skip" marker

    await delivery.on_stream("ip this task")
    await delivery.on_stream_end()

    msgs = await _drain(bus)
    assert [type(m.event) for m in msgs] == [StreamDeltaEvent, StreamEndEvent]
    assert msgs[0].content == "skip this task"


@pytest.mark.asyncio
async def test_partial_marker_flushed_at_stream_end():
    bus = MessageBus()
    delivery = _make_delivery(bus)

    await delivery.on_stream("no")  # prefix of no_response/no_reply
    assert await _drain(bus) == []

    await delivery.on_stream_end()

    msgs = await _drain(bus)
    assert [type(m.event) for m in msgs] == [StreamDeltaEvent, StreamEndEvent]
    assert msgs[0].content == "no"


@pytest.mark.asyncio
async def test_holdback_rearms_for_next_segment():
    bus = MessageBus()
    delivery = _make_delivery(bus)

    await delivery.on_stream("done with tools")
    await delivery.on_stream_end()
    first = await _drain(bus)
    assert [type(m.event) for m in first] == [StreamDeltaEvent, StreamEndEvent]

    # Next segment is a pure marker: nothing may leak.
    await delivery.on_stream("[SILENT]")
    await delivery.on_stream_end()
    assert await _drain(bus) == []
