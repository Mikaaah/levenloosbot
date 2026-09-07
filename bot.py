import os

import discord
from discord.ext import commands
from dotenv import load_dotenv


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN ontbreekt in .env")

intents = discord.Intents.all()

COGS = (
    "modules",
    "games",
    "sorting",
    "stats",
    "welcome",
    "react",
    "levels",
    "voice",
    "events",
    "suggestions",
    "changelog",
    "health",
    "botinfo",
    "actionlog",
    "birthdays",
    "reminders",
)


# =========================================================
# BOT
# =========================================================

class LevenloosBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
        )

    async def setup_hook(self):
        for cog in COGS:
            await self.load_extension(f"cogs.{cog}")
            print(f"[COGS] {cog.capitalize()} geladen")

        synced = await self.tree.sync()
        print(f"[SYNC] {len(synced)} globale command(s) gesynchroniseerd")

    async def on_ready(self):
        if self.user is None:
            return

        print(f"[BOT] Ingelogd als {self.user} ({self.user.id})")
        print(f"[BOT] Actief op {len(self.guilds)} server(s)")

        for guild in self.guilds:
            print(f"[BOT] Server: {guild.name} ({guild.id})")


# =========================================================
# RUN
# =========================================================

bot = LevenloosBot()
bot.run(DISCORD_TOKEN)
