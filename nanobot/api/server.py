"""HTTP API server for nanobot status monitoring and message injection."""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from loguru import logger

from nanobot.api.log_watcher import LogWatcher
from nanobot.bus.events import InboundMessage

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus


class StatusServer:
    """
    HTTP server providing status API and message injection for external tools.

    Endpoints:
        GET  /api/status?cursor=xxx - Get current status and logs
        GET  /health                - Health check
        POST /api/inject            - Inject a message into the agent loop
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        log_dir: Path | None = None,
        bus: "MessageBus | None" = None,
    ):
        self.host = host
        self.port = port
        self.bus = bus
        self.watcher = LogWatcher(log_dir)
        self.app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_post("/api/inject", self._handle_inject)

    async def _handle_status(self, request: web.Request) -> web.Response:
        """Handle GET /api/status request."""
        cursor = request.query.get("cursor")
        status = self.watcher.get_status(cursor)
        return web.json_response(status)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Handle GET /health request."""
        return web.json_response({"status": "ok"})

    async def _handle_inject(self, request: web.Request) -> web.Response:
        """Handle POST /api/inject — inject a message into the agent loop.

        Request body (JSON):
            message  (str, required): The message content to inject.
            channel  (str, optional): Channel name, default "system".
            sender   (str, optional): Sender identifier, default "monitor".
            chat_id  (str, optional): Target chat/session, default "cli:direct".

        The injected message enters the same MessageBus that channels use,
        so the agent processes it exactly like a normal inbound message.
        """
        if self.bus is None:
            return web.json_response(
                {"ok": False, "error": "MessageBus not available"},
                status=503,
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "Invalid JSON body"},
                status=400,
            )

        message = body.get("message")
        if not message:
            return web.json_response(
                {"ok": False, "error": "Missing required field: message"},
                status=400,
            )

        channel = body.get("channel", "system")
        sender = body.get("sender", "monitor")
        chat_id = body.get("chat_id", "cli:direct")

        msg = InboundMessage(
            channel=channel,
            sender_id=sender,
            chat_id=chat_id,
            content=message,
            metadata={"source": "api/inject"},
        )

        await self.bus.publish_inbound(msg)
        logger.info(
            "Injected message via API: channel={} chat_id={} sender={} len={}",
            channel, chat_id, sender, len(message),
        )

        return web.json_response({"ok": True, "session_key": msg.session_key})

    async def start(self) -> None:
        """Start the HTTP server and log watcher."""
        await self.watcher.start()

        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

        logger.info(f"Status API server started on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop the HTTP server and log watcher."""
        if self._runner:
            await self._runner.cleanup()
        await self.watcher.stop()
        logger.info("Status API server stopped")

    async def run_forever(self) -> None:
        """Start server and run until interrupted."""
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
