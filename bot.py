import os

import discord
from discord.ext import commands
from dotenv import load_dotenv


# =========================================================
# ENV
# =========================================================

load_dotenv()

DISCORD_TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN ontbreekt in .env"
    )


# =========================================================
# INTENTS
# =========================================================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.voice_states = True
intents.members = True


# =========================================================
# BOT
# =========================================================

class LevenloosBot(
    commands.Bot
):

    def __init__(
        self
    ):

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents
        )

    # =====================================================
    # STARTUP
    # =====================================================

    async def setup_hook(
        self
    ):

        # ---------------------------------------------
        # COGS
        # ---------------------------------------------

        await self.load_extension(
            "cogs.modules"
        )

        print(
            "[COGS] Modules geladen"
        )

        await self.load_extension(
            "cogs.games"
        )

        print(
            "[COGS] Games geladen"
        )

        await self.load_extension(
            "cogs.sorting"
        )

        print(
            "[COGS] Sorting geladen"
        )

        await self.load_extension(
            "cogs.stats"
        )

        print(
            "[COGS] Stats geladen"
        )

        await self.load_extension(
            "cogs.welcome"
        )

        print(
            "[COGS] Welcome geladen"
        )

        await self.load_extension(
            "cogs.react"
        )

        print(
            "[COGS] React geladen"
        )

        await self.load_extension(
            "cogs.levels"
        )

        print(
            "[COGS] Levels geladen"
        )

        await self.load_extension(
            "cogs.voice"
        )

        print(
            "[COGS] Voice geladen"
        )

        await self.load_extension(
            "cogs.events"
        )

        print(
            "[COGS] Events geladen"
        )

        await self.load_extension(
            "cogs.suggestions"
        )

        print(
            "[COGS] Suggestions geladen"
        )

        await self.load_extension(
            "cogs.changelog"
        )

        print(
            "[COGS] Changelog geladen"
        )

        await self.load_extension(
            "cogs.health"
        )

        print(
            "[COGS] Health geladen"
        )

        await self.load_extension(
            "cogs.botinfo"
        )

        print(
            "[COGS] Botinfo geladen"
        )

        await self.load_extension(
            "cogs.actionlog"
        )

        print(
            "[COGS] Actionlog geladen"
        )

        await self.load_extension(
            "cogs.birthdays"
        )

        print(
            "[COGS] Birthdays geladen"
        )

        await self.load_extension(
            "cogs.reminders"
        )

        print(
            "[COGS] Reminders geladen"
        )

        # ---------------------------------------------
        # GLOBAL SLASH COMMAND SYNC
        # ---------------------------------------------

        synced = await self.tree.sync()

        print(
            f"[SYNC] {len(synced)} globale "
            "command(s) gesynchroniseerd"
        )

    # =====================================================
    # READY
    # =====================================================

    async def on_ready(
        self
    ):

        if self.user is None:
            return

        print(
            f"[BOT] Ingelogd als "
            f"{self.user} ({self.user.id})"
        )

        print(
            f"[BOT] Actief op "
            f"{len(self.guilds)} server(s)"
        )

        for guild in self.guilds:

            print(
                f"[BOT] Server: "
                f"{guild.name} ({guild.id})"
            )


# =========================================================
# RUN
# =========================================================

bot = LevenloosBot()

bot.run(
    DISCORD_TOKEN
)
