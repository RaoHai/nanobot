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
        async with self._inbound_collect_lock:
            if self._active_inbound_session and msg.session_key == self._active_inbound_session:
                # Same session is active, buffer this message
                self._inbound_collect_buffer.setdefault(msg.session_key, []).append(msg)
                logger.debug(f"Buffered message for active session {msg.session_key}")
                return
        await self.inbound.put(msg)
    
    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        msg = await self.inbound.get()
        async with self._inbound_collect_lock:
            self._active_inbound_session = msg.session_key
        return msg
    
    async def complete_inbound_turn(self, msg: InboundMessage) -> None:
        """Called when a turn is complete. Flushes buffered messages if any."""
        async with self._inbound_collect_lock:
            buffered = self._inbound_collect_buffer.pop(msg.session_key, [])
            if buffered:
                merged = self._merge_buffered_messages(buffered)
                await self.inbound.put(merged)
                logger.info(f"Merged {len(buffered)} buffered messages for {msg.session_key}")
            self._active_inbound_session = None
    
    @classmethod
    def _merge_buffered_messages(cls, messages: list[InboundMessage]) -> InboundMessage:
        """Merge multiple buffered messages into one."""
        if len(messages) == 1:
            return messages[0]
        
        # Multiple messages: add [sender_id] prefix, join with \n\n
        parts = [f"[{m.sender_id}] {m.content}" for m in messages]
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
    
    def subscribe_outbound(
        self, 
        channel: str, 
        callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)
    
    async def dispatch_outbound(self) -> None:
        """
        Dispatch outbound messages to subscribed channels.
        Run this as a background task.
        """
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(self.outbound.get(), timeout=1.0)
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                for callback in subscribers:
                    try:
                        await callback(msg)
                    except Exception as e:
                        logger.error(f"Error dispatching to {msg.channel}: {e}")
            except asyncio.TimeoutError:
                continue
    
    def stop(self) -> None:
        """Stop the dispatcher loop."""
        self._running = False
    
    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()
    
    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
