"""HTTP API server for nanobot status monitoring."""

import asyncio
from pathlib import Path

from aiohttp import web
from loguru import logger

from nanobot.api.log_watcher import LogWatcher


class StatusServer:
    """
    HTTP server providing status API for external devices.
    
    Endpoints:
        GET /api/status?cursor=xxx - Get current status and logs
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080, log_dir: Path | None = None):
        self.host = host
        self.port = port
        self.watcher = LogWatcher(log_dir)
        self.app = web.Application()
        self._runner: web.AppRunner | None = None
        self._setup_routes()
    
    def _setup_routes(self) -> None:
        """Setup HTTP routes."""
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/health", self._handle_health)
    
    async def _handle_status(self, request: web.Request) -> web.Response:
        """Handle GET /api/status request."""
        cursor = request.query.get("cursor")
        status = self.watcher.get_status(cursor)
        return web.json_response(status)
    
    async def _handle_health(self, request: web.Request) -> web.Response:
        """Handle GET /health request."""
        return web.json_response({"status": "ok"})
    
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
