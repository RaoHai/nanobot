"""Log watcher for parsing nanobot logs and tracking status."""

import asyncio
import os
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from loguru import logger


StatusType = Literal["thinking", "tool_call", "listening", "idle"]


@dataclass
class LogEntry:
    """A parsed log entry."""
    ts: str
    type: str  # "tool", "thinking", "listening", "llm_request", "llm_response"
    name: str | None = None
    preview: str | None = None


@dataclass
class StatusState:
    """Current status state."""
    cursor: str = ""
    status: StatusType = "idle"
    detail: str = "💤 空闲"
    logs: deque = field(default_factory=lambda: deque(maxlen=100))
    last_activity: datetime = field(default_factory=datetime.now)


class LogWatcher:
    """
    Watches nanobot log files and parses events.
    
    Maintains a ring buffer of recent log entries and current status.
    """
    
    # Log parsing patterns
    TOOL_RE = re.compile(r"Tool call: (\w+)\((.+?)\)")
    LLM_REQ_RE = re.compile(r"LLM Request: model=(.+?),")
    LLM_RESP_RE = re.compile(r"LLM Response: mode=(\w+)")
    MSG_RE = re.compile(r"Processing message from (\S+)")
    
    # Timestamp pattern in loguru logs: 2026-02-18 23:50:00.123 | INFO | ...
    TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)")
    
    # Tool name to emoji/detail mapping
    TOOL_DETAILS = {
        "read_file": ("📖", "读取"),
        "write_file": ("✏️", "写入"),
        "edit_file": ("📝", "编辑"),
        "list_dir": ("📁", "列出目录"),
        "exec": ("⚡", "执行命令"),
        "message": ("💬", "发送消息"),
        "web_search": ("🌐", "搜索中"),
        "web_fetch": ("🔗", "获取网页"),
        "spawn": ("🚀", "启动子任务"),
    }
    
    IDLE_TIMEOUT = timedelta(seconds=30)
    
    def __init__(self, log_dir: Path | None = None):
        self.log_dir = log_dir or Path.home() / ".nanobot" / "logs"
        self.state = StatusState()
        self._running = False
        self._current_file: Path | None = None
        self._file_pos = 0
        self._watch_task: asyncio.Task | None = None
    
    def _get_log_file(self, date: datetime | None = None) -> Path:
        """Get log file path for a given date."""
        date = date or datetime.now()
        return self.log_dir / f"nanobot_{date.strftime('%Y-%m-%d')}.log"
    
    def _parse_line(self, line: str) -> LogEntry | None:
        """Parse a single log line into a LogEntry."""
        # Extract timestamp
        ts_match = self.TS_RE.match(line)
        if not ts_match:
            return None
        ts = ts_match.group(1)
        
        # Check for tool call
        tool_match = self.TOOL_RE.search(line)
        if tool_match:
            tool_name = tool_match.group(1)
            tool_args = tool_match.group(2)
            return LogEntry(
                ts=ts,
                type="tool",
                name=tool_name,
                preview=tool_args[:100] if tool_args else None
            )
        
        # Check for LLM request
        if self.LLM_REQ_RE.search(line):
            return LogEntry(ts=ts, type="llm_request")
        
        # Check for LLM response
        if self.LLM_RESP_RE.search(line):
            return LogEntry(ts=ts, type="llm_response")
        
        # Check for incoming message
        msg_match = self.MSG_RE.search(line)
        if msg_match:
            sender = msg_match.group(1)
            return LogEntry(ts=ts, type="listening", preview=sender)
        
        return None
    
    def _update_status(self, entry: LogEntry) -> None:
        """Update current status based on a log entry."""
        self.state.last_activity = datetime.now()
        self.state.cursor = entry.ts
        
        if entry.type == "llm_request":
            self.state.status = "thinking"
            self.state.detail = "🤔 思考中..."
        elif entry.type == "tool":
            self.state.status = "tool_call"
            emoji, action = self.TOOL_DETAILS.get(entry.name, ("🔧", "执行"))
            if entry.name == "read_file" and entry.preview:
                # Extract filename from args
                self.state.detail = f"{emoji} {action} {entry.preview}"
            elif entry.name == "exec":
                self.state.detail = f"{emoji} {action}"
            else:
                self.state.detail = f"{emoji} {action}"
        elif entry.type == "listening":
            self.state.status = "listening"
            self.state.detail = f"👂 收到消息 ({entry.preview})"
        elif entry.type == "llm_response":
            # After response, briefly show thinking then idle
            pass
    
    def _check_idle(self) -> None:
        """Check if we should transition to idle state."""
        if datetime.now() - self.state.last_activity > self.IDLE_TIMEOUT:
            self.state.status = "idle"
            self.state.detail = "💤 空闲"
    
    async def _read_new_lines(self) -> list[str]:
        """Read new lines from the current log file."""
        log_file = self._get_log_file()
        
        # Handle day change
        if self._current_file != log_file:
            self._current_file = log_file
            self._file_pos = 0
        
        if not log_file.exists():
            return []
        
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(self._file_pos)
                lines = f.readlines()
                self._file_pos = f.tell()
                return lines
        except Exception as e:
            logger.warning(f"Error reading log file: {e}")
            return []
    
    async def _watch_loop(self) -> None:
        """Main watch loop."""
        while self._running:
            try:
                lines = await self._read_new_lines()
                for line in lines:
                    entry = self._parse_line(line.strip())
                    if entry:
                        self.state.logs.append({
                            "ts": entry.ts,
                            "type": entry.type,
                            "name": entry.name,
                            "preview": entry.preview
                        })
                        self._update_status(entry)
                
                self._check_idle()
                await asyncio.sleep(0.5)  # Poll every 500ms
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Log watcher error: {e}")
                await asyncio.sleep(1)
    
    async def start(self) -> None:
        """Start watching logs."""
        if self._running:
            return
        
        self._running = True
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Start from end of current log file
        log_file = self._get_log_file()
        if log_file.exists():
            self._file_pos = log_file.stat().st_size
        self._current_file = log_file
        
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info(f"Log watcher started, monitoring {self.log_dir}")
    
    async def stop(self) -> None:
        """Stop watching logs."""
        self._running = False
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        logger.info("Log watcher stopped")
    
    def get_status(self, cursor: str | None = None) -> dict:
        """
        Get current status and logs.
        
        Args:
            cursor: Optional cursor to get only logs after this timestamp.
        
        Returns:
            Status dict with cursor, status, detail, and logs.
        """
        self._check_idle()
        
        logs = list(self.state.logs)
        if cursor:
            logs = [log for log in logs if log["ts"] > cursor]
        
        return {
            "cursor": self.state.cursor,
            "status": self.state.status,
            "detail": self.state.detail,
            "logs": logs
        }
