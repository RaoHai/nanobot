"""Async message queue for decoupled channel-agent communication."""

import asyncio
from typing import Callable, Awaitable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        self._outbound_subscribers: dict[str, list[Callable[[OutboundMessage], Awaitable[None]]]] = {}
        self._running = False

        # Buffer for collecting messages while a session is being processed
        self._active_inbound_session: str | None = None
        self._inbound_collect_buffer: dict[str, list[InboundMessage]] = {}
        self._inbound_collect_lock = asyncio.Lock()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent.

        If the same session is currently being processed, buffer the message
        instead of triggering a new turn.
        """
        self._apply_group_sender_label(msg)
        async with self._inbound_collect_lock:
            if self._active_inbound_session and msg.session_key == self._active_inbound_session:
                # Same session is active, buffer this message
                self._inbound_collect_buffer.setdefault(msg.session_key, []).append(msg)
                logger.debug(f"Buffered message for active session {msg.session_key}")
                return
        await self.inbound.put(msg)

    @staticmethod
    def _is_command(msg: InboundMessage) -> bool:
        """Slash commands must keep their leading '/' so the command router
        can recognize them; never merge them with other messages."""
        return msg.content.strip().startswith("/")

    @classmethod
    def _apply_group_sender_label(cls, msg: InboundMessage) -> None:
        """Prefix group-chat messages with ``[sender_id]`` in place.

        Multi-user group sessions need per-message attribution even when a
        turn contains a single message (merge-time labeling only covers
        bursts). DMs, slash commands, and system turns are left untouched.
        Idempotent: already-labeled content is not labeled again.
        """
        if cls._is_command(msg):
            return
        if not (msg.metadata or {}).get("is_group"):
            return
        label = f"[{msg.sender_id}]"
        content = msg.content or ""
        if content.startswith(label):
            return
        if not content.strip() and not msg.media:
            return
        msg.content = f"{label} {content}" if content else label

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available).

        Also drains any same-session messages already sitting in the queue
        (accumulated between turns) and merges them into one. Messages that
        look like slash commands are requeued and processed individually.
        """
        msg = await self.inbound.get()

        # Drain queue: collect same-session messages, put back others
        same_session = [msg]
        others: list[InboundMessage] = []
        while True:
            try:
                queued = self.inbound.get_nowait()
                if queued.session_key == msg.session_key:
                    same_session.append(queued)
                else:
                    others.append(queued)
            except asyncio.QueueEmpty:
                break
        for m in others:
            await self.inbound.put(m)

        if len(same_session) > 1:
            if any(self._is_command(m) for m in same_session):
                # Commands must be dispatched individually; requeue the rest
                # in original order and handle one message per turn.
                for m in same_session[1:]:
                    await self.inbound.put(m)
            else:
                logger.info("Merging {} queued messages for session {}", len(same_session), msg.session_key)
                msg = self._merge_buffered_messages(same_session)

        async with self._inbound_collect_lock:
            self._active_inbound_session = msg.session_key
        return msg

    async def complete_inbound_turn(self, msg: InboundMessage) -> None:
        """Called when a turn is complete. Flushes buffered messages if any."""
        async with self._inbound_collect_lock:
            buffered = self._inbound_collect_buffer.pop(msg.session_key, [])
            self._active_inbound_session = None
        if not buffered:
            return
        if any(self._is_command(m) for m in buffered):
            # Keep commands intact: flush each buffered message individually.
            for m in buffered:
                await self.inbound.put(m)
            logger.info(f"Flushed {len(buffered)} buffered messages individually for {msg.session_key}")
            return
        merged = self._merge_buffered_messages(buffered)
        await self.inbound.put(merged)
        logger.info(f"Merged {len(buffered)} buffered messages for {msg.session_key}")

    @classmethod
    def _merge_buffered_messages(cls, messages: list[InboundMessage]) -> InboundMessage:
        """Merge multiple buffered messages into one."""
        if len(messages) == 1:
            return messages[0]

        # Multiple messages: add [sender_id] prefix, join with \n\n.
        # Messages labeled at publish time (group chats) keep their label.
        parts = [
            m.content
            if (m.content or "").startswith(f"[{m.sender_id}]")
            else f"[{m.sender_id}] {m.content}"
            for m in messages
        ]
        merged_content = "\n\n".join(parts)
        merged_media = [item for m in messages for item in m.media]

        # Store original messages in metadata for context building
        collected = [
            {
                "sender_id": m.sender_id,
                "content": m.content,
                "media": m.media,
                "timestamp": m.timestamp.isoformat() if hasattr(m.timestamp, 'isoformat') else str(m.timestamp),
                "metadata": m.metadata,
            }
            for m in messages
        ]
        merged_metadata = {**messages[-1].metadata, "collected_messages": collected}

        return InboundMessage(
            channel=messages[-1].channel,
            sender_id=messages[-1].sender_id,
            chat_id=messages[-1].chat_id,
            content=merged_content,
            media=merged_media,
            metadata=merged_metadata,
            timestamp=messages[-1].timestamp,
        )

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels."""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
