"""Message tool for sending messages to users."""

from pathlib import Path
from typing import Any, Callable, Awaitable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""
    
    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_metadata: dict[str, Any] | None = None,
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._default_metadata: dict[str, Any] = default_metadata or {}

    def set_context(self, channel: str, chat_id: str, metadata: dict[str, Any] | None = None) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id
        self._default_metadata = metadata or {}
    
    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback
    
    @property
    def name(self) -> str:
        return "message"
    
    @property
    def description(self) -> str:
        return (
            "Send a message to the user. Use this when you want to communicate something. "
            "You can optionally attach images by providing local file paths in the media parameter. "
            "For Telegram, you can send a sticker by setting send_sticker to a file_id string, or true to use the last received sticker.\n\n"
            "IMPORTANT USAGE RULES:\n"
            "1. For simple text replies, just output text directly - DO NOT use this tool! Only use this tool when you need sticker/reaction/media/specific chat_id.\n"
            "2. When sending sticker or reaction, content MUST be empty string ''! Do not write '[SILENT]' or any text."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send"
                },
                "channel": {
                    "type": "string",
                    "description": "Optional: target channel (telegram, discord, etc.)"
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional: target chat/user ID"
                },
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: list of local file paths to images to send"
                },
                "send_sticker": {
                    "anyOf": [
                        {"type": "boolean"},
                        {"type": "string"}
                    ],
                    "description": "Optional: if true, send the last received sticker; if a string, send the sticker with that file_id (Telegram only)"
                },
                "reaction": {
                    "type": "string",
                    "description": "Optional: emoji reaction to add to a message (e.g., '👍', '❤️', '🔥'). Telegram only."
                },
                "message_id": {
                    "type": "integer",
                    "description": "Optional: target message ID for reaction. If not provided, reacts to the last received message."
                }
            },
            "required": ["content"]
        }

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        media: list[str] | None = None,
        send_sticker: bool | str = False,
        reaction: str | None = None,
        message_id: int | None = None,
        **kwargs: Any
    ) -> str:
        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        # Prepare metadata - include sticker_file_id if send_sticker is set
        metadata = dict(self._default_metadata)
        
        # If reaction is specified, determine target message_id
        if reaction:
            if message_id:
                # Use explicitly provided message_id
                metadata["reaction_to_message_id"] = message_id
            elif "message_id" in metadata:
                # Fall back to last received message
                metadata["reaction_to_message_id"] = metadata["message_id"]
        
        if send_sticker:
            if isinstance(send_sticker, str):
                # Use the provided file_id
                metadata["sticker_file_id"] = send_sticker
            elif "sticker_file_id" in self._default_metadata:
                # Use the last received sticker
                pass
            else:
                return "Error: No sticker available to send"
        else:
            # Remove sticker_file_id if not sending sticker
            metadata.pop("sticker_file_id", None)

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=[str(Path(p).expanduser()) for p in media] if media else [],
            metadata=metadata,
            reaction=reaction,
        )

        try:
            await self._send_callback(msg)
            parts = [f"Message sent to {channel}:{chat_id}"]
            if reaction:
                parts.append(f" (reacted with {reaction})")
            elif send_sticker:
                parts.append(" (sticker)")
            elif media:
                parts.append(f" with {len(media)} image(s)")
            return "".join(parts)
        except Exception as e:
            return f"Error sending message: {str(e)}"
