"""Scheduler cog - executes autonomous tasks on a cron-like schedule with reliability."""
import asyncio
import logging
import time
import traceback
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from cron_parser import next_cron_time

log = logging.getLogger("scheduler")

CHECK_INTERVAL_SECONDS = 30
TASK_TIMEOUT_SECONDS = 300  # 5-minute hard timeout per task

# Errors that are worth retrying (transient / API issues)
RETRYABLE_KEYWORDS = [
    "timeout", "rate limit", "429", "502", "503", "504",
    "connection", "timed out", "server error", "unavailable",
    "APIConnectionError", "RateLimitError", "ServiceUnavailable",
]


def _is_retryable(error: Exception) -> bool:
    """Decide whether an error is transient and worth retrying."""
    err_str = str(error).lower()
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True
    return any(kw.lower() in err_str for kw in RETRYABLE_KEYWORDS)


def _backoff_delay(retry_count: int) -> float:
    """Exponential backoff: 60s, 240s, 960s capped at 3600s."""
    return min(60 * (4 ** retry_count), 3600)


class SchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.scheduler_loop.start()
        log.info("Task scheduler started (interval=%ds, timeout=%ds)",
                 CHECK_INTERVAL_SECONDS, TASK_TIMEOUT_SECONDS)

    async def cog_unload(self):
        self.scheduler_loop.cancel()

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def scheduler_loop(self):
        """Check for due tasks and execute them."""
        now = time.time()
        due_tasks = await self.bot.db.get_due_tasks(now)

        for task in due_tasks:
            # Atomic claim — prevents double-run if the loop fires again
            claimed = await self.bot.db.claim_task(task["id"])
            if not claimed:
                log.debug("Task '%s' (#%d) already claimed, skipping",
                          task["task_name"], task["id"])
                continue

            # Start execution tracking
            exec_id = await self.bot.db.start_task_execution(task["id"])

            try:
                # Hard timeout wrapper
                response = await asyncio.wait_for(
                    self._execute_task(task),
                    timeout=TASK_TIMEOUT_SECONDS,
                )

                # Success path
                summary = (response[:200] + "…") if response and len(response) > 200 else response
                await self.bot.db.complete_task_execution(
                    exec_id, status="success", result_summary=summary,
                )
                await self.bot.db.reset_task_retry(task["id"])

                # Schedule next normal run
                await self._schedule_next_run(task)

            except asyncio.TimeoutError:
                err_msg = f"Task timed out after {TASK_TIMEOUT_SECONDS}s"
                log.error("Task '%s' (#%d): %s", task["task_name"], task["id"], err_msg)
                await self.bot.db.complete_task_execution(
                    exec_id, status="timeout", error_message=err_msg,
                )
                await self._handle_retryable_failure(task, err_msg)

            except Exception as e:
                err_msg = f"{type(e).__name__}: {e}"
                tb = traceback.format_exc()
                log.error("Task '%s' (#%d) failed:\n%s", task["task_name"], task["id"], tb)

                if _is_retryable(e):
                    await self.bot.db.complete_task_execution(
                        exec_id, status="error_retryable", error_message=err_msg,
                    )
                    await self._handle_retryable_failure(task, err_msg)
                else:
                    await self.bot.db.complete_task_execution(
                        exec_id, status="error", error_message=err_msg,
                    )
                    await self._handle_permanent_failure(task, err_msg)

    @scheduler_loop.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Retry / failure helpers
    # ------------------------------------------------------------------
    async def _handle_retryable_failure(self, task: dict, err_msg: str):
        """Increment retry counter; if under max, schedule a backoff run."""
        retry_count = await self.bot.db.increment_task_retry(task["id"])
        max_retries = 3  # default

        if retry_count >= max_retries:
            log.warning("Task '%s' (#%d) exceeded max retries (%d), disabling",
                        task["task_name"], task["id"], max_retries)
            await self._handle_permanent_failure(
                task,
                f"Max retries ({max_retries}) exceeded. Last error: {err_msg}",
            )
            return

        delay = _backoff_delay(retry_count)
        next_run = time.time() + delay
        await self.bot.db.update_task_run(task["id"], next_run)
        log.info("Task '%s' (#%d) retry %d/%d in %.0fs",
                 task["task_name"], task["id"], retry_count, max_retries, delay)

    async def _handle_permanent_failure(self, task: dict, err_msg: str):
        """Disable the task and notify the channel."""
        await self.bot.db.toggle_task(task["guild_id"], task["id"], False)

        guild = self.bot.get_guild(int(task["guild_id"]))
        channel = guild.get_channel(int(task["channel_id"])) if guild else None
        if channel:
            try:
                await channel.send(
                    f"⛔ Scheduled task **{task['task_name']}** (#{task['id']}) "
                    f"has been **disabled** due to repeated failures.\n"
                    f"```\n{err_msg[:1500]}\n```\n"
                    f"Re-enable with the `toggle_scheduled_task` tool after fixing the issue."
                )
            except Exception:
                log.warning("Could not send failure notification for task #%d", task["id"])

    async def _schedule_next_run(self, task: dict):
        """Compute and persist the next cron-based run time."""
        try:
            next_run = next_cron_time(task["cron_expression"])
            await self.bot.db.update_task_run(task["id"], next_run)
        except Exception as e:
            log.error("Failed to calculate next run for '%s': %s",
                      task["task_name"], e)
            await self.bot.db.toggle_task(task["guild_id"], task["id"], False)

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------
    async def _execute_task(self, task: dict) -> str | None:
        """Execute a scheduled task by running it through the AI agent.

        Returns the response text on success.
        """
        guild = self.bot.get_guild(int(task["guild_id"]))
        if not guild:
            log.warning("Guild %s not found for task '%s'",
                        task["guild_id"], task["task_name"])
            return None

        channel = guild.get_channel(int(task["channel_id"]))
        if not channel:
            log.warning("Channel %s not found for task '%s'",
                        task["channel_id"], task["task_name"])
            return None

        log.info("Executing scheduled task: '%s' in #%s",
                 task["task_name"], channel.name)

        agent_cog = self.bot.get_cog("AgentCog")
        if not agent_cog:
            log.error("AgentCog not loaded, cannot execute task")
            return None

        # Proxy object to satisfy the agent interface
        message_proxy = type("TaskProxy", (), {
            "guild": guild,
            "channel": channel,
            "author": guild.me,
        })()

        prompt = f"[SCHEDULED TASK: {task['task_name']}]\n{task['task_prompt']}"

        response = await agent_cog._run_agent(message_proxy, prompt)

        # Deliver response to the channel
        if response and response.strip():
            header = f"\U0001f916 **Scheduled Task: {task['task_name']}**\n"
            full_response = header + response

            if len(full_response) <= 2000:
                await channel.send(full_response)
            else:
                await channel.send(header)
                chunks = agent_cog._split_text(response)
                for chunk in chunks:
                    await channel.send(chunk)

        return response


async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))
