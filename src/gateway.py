"""Telegram gateway: long polling, admin auth, sanitization, offline queue and background jobs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import subprocess
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from src import __version__
from src.agent import Agent
from src.config import AppConfig
from src.llm import CircuitOpenError, LLMClient
from src.logging_setup import AuditLogger, setup_logging
from src.security import detect_injection, inbound_sanitize, outbound_sanitize
from src.tools import ToolRegistry, register_default_tools
from src.tools.db import AgentDB
from src.tools.executor import Executor
from src.tools.mihomo import MihomoClient
from src.tools.monitor import Monitor
from src.tools.system import backup_all

logger = logging.getLogger("tg-agent.gateway")

CONFIRM_TTL = 60.0
OFFLINE_TTL = 86400.0
OFFLINE_DEDUP_S = 3600.0


class TelegramGateway:
    def __init__(
        self,
        config: AppConfig,
        agent: Agent,
        db: AgentDB,
        executor: Executor,
        monitor: Monitor,
        registry: ToolRegistry,
    ) -> None:
        self.config = config
        self.agent = agent
        self.db = db
        self.executor = executor
        self.monitor = monitor
        self.registry = registry
        self._pending_confirm: Dict[int, Tuple[str, float]] = {}
        self._offline: Deque[Dict] = deque()
        self._app: Optional[Application] = None
        self._last_ctx: Optional[ContextTypes.DEFAULT_TYPE] = None
        self._last_prune: float = 0.0

    # ------------------------------------------------------------------
    async def _send(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> bool:
        text = outbound_sanitize(str(text))
        if len(text) > self.config.message_limit:
            text = text[: self.config.message_limit] + "\n…(已截断)"
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
            return True
        except Exception as exc:
            logger.warning("Telegram send failed, queueing offline: %s", exc)
            self._enqueue_offline(chat_id, text)
            return False

    def _enqueue_offline(self, chat_id: int, text: str) -> None:
        fp = hashlib.sha1(text[:300].encode("utf-8")).hexdigest()
        now = time.time()
        for item in self._offline:
            if item["fp"] == fp and now - item["ts"] < OFFLINE_DEDUP_S:
                return
        self._offline.append({"chat_id": chat_id, "text": text, "ts": now, "fp": fp})
        while len(self._offline) > self.config.offline_queue_max:
            self._offline.popleft()

    async def _flush_offline(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = time.time()
        kept: Deque[Dict] = deque()
        for item in self._offline:
            if now - item["ts"] > OFFLINE_TTL:
                continue
            try:
                await context.bot.send_message(chat_id=item["chat_id"], text=item["text"])
            except Exception:
                kept.append(item)
        self._offline = kept

    # ------------------------------------------------------------------
    def _is_admin(self, user_id: Optional[int]) -> bool:
        return user_id is not None and user_id in self.config.admin_ids

    def _check_confirm(self, chat_id: int, action: str) -> bool:
        entry = self._pending_confirm.get(chat_id)
        if entry and entry[0] == action and time.time() - entry[1] < CONFIRM_TTL:
            self._pending_confirm.pop(chat_id, None)
            return True
        self._pending_confirm[chat_id] = (action, time.time())
        return False

    # ------------------------------------------------------------------
    async def _pure_ai_enabled(self) -> bool:
        try:
            return (await self.db.kv_get("pure_ai_mode")) == "true"
        except Exception:
            return False

    async def _on_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            await self._send(context, update.effective_chat.id, "❌ 无权限")
            return
        message = update.effective_message
        chat_id = update.effective_chat.id
        text = inbound_sanitize(message.text or "")
        await self._record_message(chat_id, update.effective_user.id, text)

        # 纯 AI 模式：所有消息（含 / 开头）直接交给 AI 纯聊天
        if await self._pure_ai_enabled():
            if detect_injection(text):
                await self._send(context, chat_id, "❌ 请求包含疑似注入特征，已拦截")
                return
            try:
                result = await self.agent.handle(update.effective_user.id, chat_id, text, pure=True)
            except CircuitOpenError as exc:
                result = f"⚠️ {exc}"
            await self._send(context, chat_id, result)
            return

        parts = text.split(maxsplit=1)
        cmd = parts[0].lstrip("/").split("@")[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("reboot", "shutdown") and not self._check_confirm(chat_id, cmd):
            await self._send(context, chat_id, f"⚠️ 危险操作。再次发送 /{cmd} 确认执行（{int(CONFIRM_TTL)} 秒内有效）。")
            return

        if cmd == "ai":
            if detect_injection(args):
                logger.warning("inbound injection blocked from user %s", update.effective_user.id)
                await self._send(context, chat_id, "❌ 请求包含疑似注入特征，已拦截")
                return
            try:
                result = await self.agent.handle(update.effective_user.id, chat_id, args or "你好")
            except CircuitOpenError as exc:
                result = f"⚠️ {exc}"
        else:
            result = await self.agent.handle_command(cmd, args, chat_id)
        await self._send(context, chat_id, result)

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_admin(update.effective_user.id if update.effective_user else None):
            await self._send(context, update.effective_chat.id, "❌ 无权限")
            return
        chat_id = update.effective_chat.id
        text = inbound_sanitize(update.effective_message.text or "")
        if not text:
            return
        if detect_injection(text):
            await self._send(context, chat_id, "❌ 请求包含疑似注入特征，已拦截")
            return
        await self._record_message(chat_id, update.effective_user.id, text)
        try:
            result = await self.agent.handle(update.effective_user.id, chat_id, text, pure=await self._pure_ai_enabled())
        except CircuitOpenError as exc:
            result = f"⚠️ {exc}"
        await self._send(context, chat_id, result)

    async def _record_message(self, chat_id: int, user_id: int, text: str) -> None:
        await self.db.enqueue_bulk(
            "INSERT INTO messages(chat_id, user_id, text, ts) VALUES(?,?,?,?)",
            (chat_id, user_id, text, time.strftime("%Y-%m-%d %H:%M:%S")),
        )

    # ------------------------------------------------------------------
    async def _monitor_alert(self, text: str) -> None:
        if self._last_ctx and self.config.admin_chat_id:
            await self._send(self._last_ctx, self.config.admin_chat_id, text)

    async def _job_monitor(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._last_ctx = context
        self.monitor.alerts.send_cb = self._monitor_alert
        try:
            await self.monitor.tick()
        except Exception:
            logger.exception("monitor tick failed")
        # 日志最多保留 30 天，每小时检查一次
        now = time.time()
        if now - self._last_prune > 6 * 3600:
            try:
                from src.logging_setup import prune_logs

                removed = prune_logs(self.config.log_dir, days=30)
                if removed:
                    logger.info("已清理 %s 个超过 30 天的日志文件", removed)
            except Exception:
                logger.exception("log pruning failed")
            self._last_prune = now

    async def _job_control(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        control = self.config.control_dir
        for flag, handler in (
            ("reload", self._ctl_reload),
            ("soft_reset", self._ctl_soft_reset),
            ("backup", self._ctl_backup),
            ("kill_subprocesses", self._ctl_kill),
        ):
            path = control / flag
            if not path.exists():
                continue
            try:
                message = await handler(context)
            except Exception as exc:
                message = f"❌ 控制操作 {flag} 失败: {exc}"
            try:
                path.unlink()
            except OSError:
                pass
            if message and self.config.admin_chat_id:
                await self._send(context, self.config.admin_chat_id, message)

    async def _ctl_reload(self, context: ContextTypes.DEFAULT_TYPE) -> str:
        changed = self.config.reload()
        self._apply_runtime_changes()
        return "✅ 配置已热重载" if changed else "ℹ️ 配置无变化"

    def _apply_runtime_changes(self) -> None:
        # exec allowlist / rate limits are read live from config by the executor
        pass

    async def _ctl_soft_reset(self, context: ContextTypes.DEFAULT_TYPE) -> str:
        self._pending_confirm.clear()
        self._offline.clear()
        await self.db.flush_bulk()
        return "✅ 已温和重置（确认状态与任务队列已清空，数据库/WAL 未触碰）"

    async def _ctl_backup(self, context: ContextTypes.DEFAULT_TYPE) -> str:
        return await backup_all(self.config, self.db, self.executor)

    async def _ctl_kill(self, context: ContextTypes.DEFAULT_TYPE) -> str:
        killed = self.executor.kill_active()
        return f"✅ 已强杀 {killed} 个活跃子进程组"

    async def _job_watchdog(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        enabled = (await self.db.kv_get("watchdog_enabled")) or ("true" if self.config.watchdog_enabled else "false")
        if enabled != "true":
            return
        if self.monitor.healthy():
            return
        elapsed = time.time() - self.monitor._last_tick
        db_ok = True
        try:
            await self.db.query_one("SELECT 1")
        except Exception:
            db_ok = False
        if elapsed < self.config.watchdog_regular_s * 2 and db_ok:
            return
        logger.critical("watchdog triggered: event loop stale %.0fs, db_ok=%s — restarting", elapsed, db_ok)
        try:
            if self.config.admin_chat_id:
                await self._send(context, self.config.admin_chat_id, "🐶 看门狗触发：事件循环疑似卡死，正在重启服务")
        except Exception:
            pass
        try:
            subprocess.run(["systemctl", "restart", "tg-agent.service"], timeout=15, check=False)
        except Exception:
            os._exit(1)

    async def _job_offline(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._flush_offline(context)

    # ------------------------------------------------------------------
    async def _set_commands(self) -> None:
        commands = [
            ("status", "查看系统运行状态"),
            ("ports", "查看服务端口"),
            ("mihomo", "Mihomo 节点与测速"),
            ("switch", "切换代理节点"),
            ("wake", "发送 WOL 唤醒包"),
            ("backup", "数据及配置备份"),
            ("watchdog", "看门狗管理"),
            ("ai", "AI 对话模式"),
            ("help", "查看使用帮助与说明"),
        ]
        try:
            await self._app.bot.set_my_commands(commands)
        except Exception:
            logger.warning("set_my_commands failed", exc_info=True)

    async def run(self) -> None:
        if not self.config.bot_token:
            logger.error("BOT_TOKEN 未配置，退出")
            raise SystemExit(1)
        self.config.ensure_dirs()
        self.config.pid_file.write_text(str(os.getpid()), encoding="utf-8")

        self._app = (
            ApplicationBuilder()
            .token(self.config.bot_token)
            .connect_timeout(20)
            .read_timeout(20)
            .write_timeout(20)
            .get_updates_read_timeout(40)
            .build()
        )
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        self._app.add_handler(MessageHandler(filters.COMMAND, self._on_command))

        jobs = self._app.job_queue
        jobs.run_repeating(self._job_monitor, interval=self.config.metrics_interval, first=10)
        jobs.run_repeating(self._job_control, interval=5, first=15)
        jobs.run_repeating(self._job_watchdog, interval=10, first=20)
        jobs.run_repeating(self._job_offline, interval=30, first=30)

        await self._app.initialize()
        await self._set_commands()
        await self._app.start()
        await self._app.updater.start_polling(allowed_updates=[Update.MESSAGE])
        logger.info("TG-Agent v%s started (pid=%s)", __version__, os.getpid())
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            try:
                self.config.pid_file.unlink()
            except OSError:
                pass


async def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="TG-Agent Telegram gateway")
    parser.add_argument("--config", help="配置文件路径（默认 configs/.env）")
    args = parser.parse_args(argv)

    config = AppConfig.from_env(args.config)
    config.ensure_dirs()
    setup_logging(config.log_dir, config.log_level)

    db = AgentDB(config.db_path)
    await db.init()
    audit = AuditLogger(config.log_dir)
    executor = Executor(config, audit)
    monitor = Monitor(config, db)
    mihomo = MihomoClient(
        base_url=config.mihomo_api,
        test_url=config.mihomo_test_url,
        delay_max=config.mihomo_delay_max,
        delay_timeout=config.mihomo_delay_timeout,
        node_limit=config.mihomo_node_limit,
    )
    registry = ToolRegistry()
    register_default_tools(registry, config, db, executor, monitor, mihomo)
    llm = LLMClient(
        base_url=config.llm_base_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        timeout=config.llm_timeout,
    )
    agent = Agent(config, llm, registry, db, monitor)
    gateway = TelegramGateway(config, agent, db, executor, monitor, registry)

    def _shutdown(*_args) -> None:
        asyncio.get_event_loop().stop()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except (NotImplementedError, RuntimeError):
                pass
        await gateway.run()
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
