"""Tests for group sender labeling in the inbound message bus."""

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


def _msg(
    content: str,
    *,
    sender_id: str = "1366880037",
    is_group: bool = True,
    media: list[str] | None = None,
) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        sender_id=sender_id,
        chat_id="-1004440198285",
        content=content,
        media=media or [],
        metadata={"is_group": is_group},
    )


@pytest.mark.asyncio
async def test_group_message_gets_sender_label_on_publish() -> None:
    bus = MessageBus()
    await bus.publish_inbound(_msg("哼"))
    out = await bus.consume_inbound()
    assert out.content == "[1366880037] 哼"


@pytest.mark.asyncio
async def test_dm_message_is_not_labeled() -> None:
    bus = MessageBus()
    await bus.publish_inbound(_msg("哼", is_group=False))
    out = await bus.consume_inbound()
    assert out.content == "哼"


@pytest.mark.asyncio
async def test_slash_command_is_not_labeled() -> None:
    bus = MessageBus()
    await bus.publish_inbound(_msg("/model status"))
    out = await bus.consume_inbound()
    assert out.content == "/model status"


@pytest.mark.asyncio
async def test_labeling_is_idempotent() -> None:
    bus = MessageBus()
    msg = _msg("[1366880037] 哼")
    await bus.publish_inbound(msg)
    out = await bus.consume_inbound()
    assert out.content == "[1366880037] 哼"


@pytest.mark.asyncio
async def test_media_only_group_message_is_labeled() -> None:
    bus = MessageBus()
    await bus.publish_inbound(_msg("", media=["/tmp/x.jpg"]))
    out = await bus.consume_inbound()
    assert out.content == "[1366880037]"


@pytest.mark.asyncio
async def test_empty_message_without_media_is_not_labeled() -> None:
    bus = MessageBus()
    await bus.publish_inbound(_msg(""))
    out = await bus.consume_inbound()
    assert out.content == ""


@pytest.mark.asyncio
async def test_merged_group_messages_not_double_labeled() -> None:
    bus = MessageBus()
    await bus.publish_inbound(_msg("第一条", sender_id="1366880037"))
    await bus.publish_inbound(_msg("第二条", sender_id="317794218|ElChiang"))
    out = await bus.consume_inbound()
    assert out.content == "[1366880037] 第一条\n\n[317794218|ElChiang] 第二条"


def test_merge_still_labels_unlabeled_messages() -> None:
    """Messages that bypass publish_inbound (e.g. direct merge) keep old behavior."""
    merged = MessageBus._merge_buffered_messages(
        [_msg("a", sender_id="1", is_group=False), _msg("b", sender_id="2", is_group=False)]
    )
    assert merged.content == "[1] a\n\n[2] b"
