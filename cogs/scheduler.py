"""Scheduler cog - executes autonomous tasks on a cron-like schedule."""
import logging
import time
import traceback
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from cron_parser import next_cron_time

log = logging.getLogger("scheduler")

CHECK_INTERVAL_SECONDS = 30


class SchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.scheduler_loop.start()
        log.info("Task scheduler started")

    async def cog_unload(self):
        self.scheduler_loop.cancel()

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def scheduler_loop(self):
        """Check for due tasks and execute them."""
        now = time.time()
        due_tasks = await self.bot.db.get_due_tasks(now)

        for task in due_tasks:
            try:
                await self._execute_task(task)
            except Exception as e:
                log.error(f"Task '{task['task_name']}' failed: {traceback.format_exc()}")

            # Calculate next run
            try:
                next_run = next_cron_time(task["cron_expression"])
                await self.bot.db.update_task_run(task["id"], next_run)
            except Exception as e:
                log.error(f"Failed to calculate next run for '{task['task_name']}': {e}")
                # Disable broken tasks
                await self.bot.db.toggle_task(task["guild_id"], task["id"], False)

    @scheduler_loop.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    async def _execute_task(self, task: dict):
        """Execute a scheduled task by running it through the AI agent."""
        guild = self.bot.get_guild(int(task["guild_id"]))
        if not guild:
            log.warning(f"Guild {task['guild_id']} not found for task '{task['task_name']}'")
            return

        channel = guild.get_channel(int(task["channel_id"]))
        if not channel:
            log.warning(f"Channel {task['channel_id']} not found for task '{task['task_name']}'")
            return

        log.info(f"Executing scheduled task: '{task['task_name']}' in #{channel.name}")

        # Get the agent cog to run the prompt
        agent_cog = self.bot.get_cog("AgentCog")
        if not agent_cog:
            log.error("AgentCog not loaded, cannot execute task")
            return

        # Create a proxy message-like object for the agent
        message_proxy = type("TaskProxy", (), {
            "guild": guild,
            "channel": channel,
            "author": guild.me,
        })()

        prompt = f"[SCHEDULED TASK: {task['task_name']}]\n{task['task_prompt']}"

        try:
            response = await agent_cog._run_agent(message_proxy, prompt)

            # Send the response to the designated channel
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

        except Exception as e:
            log.error(f"Task execution error: {e}")
            await channel.send(
                f"\u26a0\ufe0f Scheduled task **{task['task_name']}** encountered an error: {e}"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))
