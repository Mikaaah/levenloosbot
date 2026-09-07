import asyncio
import os
import platform
from datetime import datetime, timezone
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands, tasks


# =========================================================
# CONFIG
# =========================================================

BOT_VERSION = "1.0.0"
STATUS_ROTATION_MINUTES = 5

ACTIVE_GAME_CATEGORY_ID = 940987098427707412
GAME_PROPOSAL_CHANNEL_ID = 940336215331319858

ARCHIVE_CATEGORY_ID = 1545088593716973568
ARCHIVE_RESTORE_CHANNEL_ID = 1545089501745913916

DATABASE_FILES = {
    "Main": "levenloos.db",
    "Levels": "levels.db",
    "Birthdays": "birthdays.db",
}

EXPECTED_MODULES = [
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
]

MODULE_LABELS = {
    "modules": "Modules",
    "games": "Games",
    "sorting": "Sorting",
    "stats": "Stats",
    "welcome": "Welcome",
    "react": "React Roles",
    "levels": "Levels",
    "voice": "Voice",
    "events": "Events",
    "suggestions": "Suggesties",
    "changelog": "Changelog",
    "health": "Health",
    "botinfo": "Bot Info",
    "actionlog": "Action Log",
    "birthdays": "Verjaardagen",
    "reminders": "Reminders",
}

PURPLE = discord.Color.from_rgb(108, 39, 218)


# =========================================================
# HELPERS
# =========================================================

def format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []

    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}u")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts[:3])


def shorten(text: str, length: int = 40) -> str:
    text = str(text)

    if len(text) <= length:
        return text

    return text[: max(0, length - 1)] + "…"


def clean_game_channel_name(channel: discord.abc.GuildChannel) -> str:
    name = channel.name

    if name.startswith("▏"):
        name = name[1:]

    return name.replace("-", " ").strip().title()


def extension_loaded(bot: commands.Bot, module_name: str) -> bool:
    return f"cogs.{module_name}" in bot.extensions


def get_group_commands(
    command: app_commands.Command | app_commands.Group,
) -> Iterable[app_commands.Command]:
    if isinstance(command, app_commands.Group):
        for child in command.commands:
            if isinstance(child, app_commands.Group):
                yield from get_group_commands(child)
            else:
                yield child
    else:
        yield command


def command_path(command: app_commands.Command) -> str:
    parts = [command.name]
    parent = command.parent

    while parent is not None:
        parts.append(parent.name)
        parent = parent.parent

    parts.reverse()
    return "/" + " ".join(parts)


def management_access(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id:
        return True

    if member.guild_permissions.administrator:
        return True

    role_names = {role.name.lower() for role in member.roles}

    return "admin" in role_names or "owner" in role_names


# =========================================================
# COG
# =========================================================

class BotInfo(commands.Cog):

    info_group = app_commands.Group(
        name="bot",
        description="Informatie over de LEVENLOOS bot",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.status_index = 0

        if not hasattr(bot, "levenloos_started_at"):
            bot.levenloos_started_at = datetime.now(timezone.utc)

    async def cog_load(self):
        self.status_loop.start()
        print("[BOTINFO] Bot Info module geladen")

    def cog_unload(self):
        self.status_loop.cancel()

    # =====================================================
    # GENERAL DATA
    # =====================================================

    def uptime_seconds(self) -> float:
        started_at = getattr(
            self.bot,
            "levenloos_started_at",
            datetime.now(timezone.utc),
        )

        return (
            datetime.now(timezone.utc) - started_at
        ).total_seconds()

    def active_games_count(self, guild: discord.Guild) -> int:
        category = guild.get_channel(ACTIVE_GAME_CATEGORY_ID)

        if not isinstance(category, discord.CategoryChannel):
            return 0

        return sum(
            1
            for channel in category.channels
            if isinstance(channel, discord.TextChannel)
            and channel.id != GAME_PROPOSAL_CHANNEL_ID
        )

    def archived_games_count(self, guild: discord.Guild) -> int:
        category = guild.get_channel(ARCHIVE_CATEGORY_ID)

        if not isinstance(category, discord.CategoryChannel):
            return 0

        return sum(
            1
            for channel in category.channels
            if isinstance(channel, discord.TextChannel)
            and channel.id != ARCHIVE_RESTORE_CHANNEL_ID
        )

    def voice_now_count(self, guild: discord.Guild) -> int:
        return sum(
            1
            for channel in guild.voice_channels
            for member in channel.members
            if not member.bot
        )

    def command_list(self) -> list[app_commands.Command]:
        commands_found = []

        for root in self.bot.tree.get_commands():
            commands_found.extend(get_group_commands(root))

        commands_found.sort(
            key=lambda command: command_path(command).lower()
        )

        return commands_found

    async def get_stats_data(
        self,
        guild: discord.Guild,
    ) -> dict:
        data = {
            "week_messages": None,
            "active_users": None,
            "top_game": None,
            "top_game_messages": None,
            "voice_seconds": None,
            "top_player": None,
        }

        stats_cog = self.bot.get_cog("Stats")

        if stats_cog is None:
            return data

        try:
            if hasattr(stats_cog, "get_week_messages"):
                data["week_messages"] = await stats_cog.get_week_messages(
                    guild
                )
        except Exception:
            pass

        try:
            if hasattr(stats_cog, "get_active_users_count"):
                data["active_users"] = (
                    await stats_cog.get_active_users_count(guild)
                )
        except Exception:
            pass

        try:
            if hasattr(stats_cog, "get_top_game"):
                result = await stats_cog.get_top_game(guild)

                if result:
                    top_game, top_game_messages = result
                    data["top_game"] = top_game
                    data["top_game_messages"] = top_game_messages
        except Exception:
            pass

        try:
            if hasattr(stats_cog, "get_voice_totals"):
                totals = await stats_cog.get_voice_totals(guild)
                data["voice_seconds"] = sum(totals.values())
        except Exception:
            pass

        try:
            if hasattr(stats_cog, "get_top_player"):
                result = await stats_cog.get_top_player(guild)

                if result:
                    data["top_player"] = result[0]
        except Exception:
            pass

        return data

    # =====================================================
    # STATUS ROTATION
    # =====================================================

    async def build_statuses(
        self,
        guild: discord.Guild,
    ) -> list[tuple[discord.ActivityType, str]]:

        members = guild.member_count or len(guild.members)
        humans = sum(1 for member in guild.members if not member.bot)
        boosts = guild.premium_subscription_count or 0
        active_games = self.active_games_count(guild)
        archived_games = self.archived_games_count(guild)
        voice_now = self.voice_now_count(guild)
        commands_count = len(self.command_list())

        loaded_modules = sum(
            1
            for module in EXPECTED_MODULES
            if extension_loaded(self.bot, module)
        )

        latency_ms = round(self.bot.latency * 1000)
        stats = await self.get_stats_data(guild)

        statuses: list[tuple[discord.ActivityType, str]] = [
            (
                discord.ActivityType.watching,
                f"{format_number(humans)} spelers in LEVENLOOS",
            ),
            (
                discord.ActivityType.playing,
                f"met {format_number(active_games)} actieve games",
            ),
            (
                discord.ActivityType.watching,
                f"{format_number(archived_games)} gearchiveerde games",
            ),
            (
                discord.ActivityType.watching,
                f"{format_number(boosts)} server boosts 🚀",
            ),
            (
                discord.ActivityType.watching,
                f"{format_number(voice_now)} spelers in voice",
            ),
            (
                discord.ActivityType.playing,
                "/level",
            ),
            (
                discord.ActivityType.playing,
                "/top",
            ),
            (
                discord.ActivityType.playing,
                "/react",
            ),
            (
                discord.ActivityType.playing,
                "/verjaardag",
            ),
            (
                discord.ActivityType.playing,
                "/game vote",
            ),
            (
                discord.ActivityType.watching,
                f"{format_number(commands_count)} slash commands",
            ),
            (
                discord.ActivityType.watching,
                f"{loaded_modules}/{len(EXPECTED_MODULES)} modules",
            ),
            (
                discord.ActivityType.watching,
                f"{latency_ms}ms ping",
            ),
            (
                discord.ActivityType.playing,
                "LEVENLOOS 🎮",
            ),
            (
                discord.ActivityType.watching,
                "jouw XP oplopen ✨",
            ),
        ]

        if stats["week_messages"] is not None:
            statuses.append(
                (
                    discord.ActivityType.watching,
                    f"{format_number(stats['week_messages'])} berichten deze week",
                )
            )

        if stats["active_users"] is not None:
            statuses.append(
                (
                    discord.ActivityType.watching,
                    f"{format_number(stats['active_users'])} actieve leden deze week",
                )
            )

        if stats["voice_seconds"] is not None:
            voice_hours = stats["voice_seconds"] / 3600

            statuses.append(
                (
                    discord.ActivityType.watching,
                    f"{voice_hours:.0f}u voice deze week",
                )
            )

        if stats["top_game"] is not None:
            game_name = clean_game_channel_name(
                stats["top_game"]
            )

            statuses.append(
                (
                    discord.ActivityType.watching,
                    f"🔥 {shorten(game_name, 30)} is #1",
                )
            )

        if stats["top_player"] is not None:
            statuses.append(
                (
                    discord.ActivityType.watching,
                    f"🏆 {shorten(stats['top_player'].display_name, 28)} is actiefste",
                )
            )

        fallback_statuses = [
            (
                discord.ActivityType.watching,
                "nieuwe levels binnenkomen",
            ),
            (
                discord.ActivityType.playing,
                "met React Roles 🎨",
            ),
            (
                discord.ActivityType.watching,
                f"{format_number(members)} accounts op de server",
            ),
            (
                discord.ActivityType.listening,
                "naar LEVENLOOS",
            ),
            (
                discord.ActivityType.watching,
                "game votes 🎮",
            ),
        ]

        for status in fallback_statuses:
            if len(statuses) >= 20:
                break

            statuses.append(status)

        return statuses[:20]

    async def rotate_status(self):
        if not self.bot.guilds:
            return

        guild = max(
            self.bot.guilds,
            key=lambda item: item.member_count or 0,
        )

        statuses = await self.build_statuses(guild)

        if not statuses:
            return

        self.status_index %= len(statuses)

        activity_type, text = statuses[
            self.status_index
        ]

        activity = discord.Activity(
            type=activity_type,
            name=text,
        )

        await self.bot.change_presence(
            status=discord.Status.online,
            activity=activity,
        )

        print(
            f"[BOTINFO] Status {self.status_index + 1}/"
            f"{len(statuses)} → {text}"
        )

        self.status_index = (
            self.status_index + 1
        ) % len(statuses)

    @tasks.loop(minutes=STATUS_ROTATION_MINUTES)
    async def status_loop(self):
        try:
            await self.rotate_status()
        except Exception as exc:
            print(
                f"[BOTINFO STATUS] "
                f"{type(exc).__name__}: {exc}"
            )

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()

    # =====================================================
    # EMBEDS
    # =====================================================

    async def build_info_embed(
        self,
        guild: discord.Guild,
    ) -> discord.Embed:

        ping = round(self.bot.latency * 1000)
        uptime = format_duration(
            self.uptime_seconds()
        )

        humans = sum(
            1
            for member in guild.members
            if not member.bot
        )

        bots = sum(
            1
            for member in guild.members
            if member.bot
        )

        active_games = self.active_games_count(
            guild
        )

        archived_games = self.archived_games_count(
            guild
        )

        voice_now = self.voice_now_count(
            guild
        )

        loaded_modules = [
            module
            for module in EXPECTED_MODULES
            if extension_loaded(self.bot, module)
        ]

        command_count = len(
            self.command_list()
        )

        stats = await self.get_stats_data(
            guild
        )

        embed = discord.Embed(
            title="🤖 LEVENLOOS BOT",
            description=(
                "De officiële bot van **LEVENLOOS**.\n"
                "Levels, games, React Roles, statistieken, "
                "verjaardagen en serverbeheer in één bot."
            ),
            color=PURPLE,
            timestamp=datetime.now(timezone.utc),
        )

        if self.bot.user is not None:
            embed.set_thumbnail(
                url=self.bot.user.display_avatar.url
            )

        embed.add_field(
            name="⚡ BOT",
            value=(
                f"> **Ping:** `{ping} ms`\n"
                f"> **Uptime:** `{uptime}`\n"
                f"> **Versie:** `{BOT_VERSION}`\n"
                f"> **Discord.py:** `{discord.__version__}`\n"
                f"> **Python:** `{platform.python_version()}`"
            ),
            inline=True,
        )

        embed.add_field(
            name="👥 SERVER",
            value=(
                f"> **Leden:** `{format_number(humans)}`\n"
                f"> **Bots:** `{format_number(bots)}`\n"
                f"> **Boosts:** `{format_number(guild.premium_subscription_count or 0)}`\n"
                f"> **In voice:** `{format_number(voice_now)}`\n"
                f"> **Rollen:** `{format_number(len(guild.roles))}`"
            ),
            inline=True,
        )

        embed.add_field(
            name="🎮 GAMES",
            value=(
                f"> **Actief:** `{format_number(active_games)}`\n"
                f"> **Archived:** `{format_number(archived_games)}`\n"
                f"> **Tekstkanalen:** `{format_number(len(guild.text_channels))}`\n"
                f"> **Voicekanalen:** `{format_number(len(guild.voice_channels))}`\n"
                f"> **Categorieën:** `{format_number(len(guild.categories))}`"
            ),
            inline=True,
        )

        weekly_lines = []

        if stats["week_messages"] is not None:
            weekly_lines.append(
                f"> **Berichten:** `{format_number(stats['week_messages'])}`"
            )

        if stats["active_users"] is not None:
            weekly_lines.append(
                f"> **Actieve leden:** `{format_number(stats['active_users'])}`"
            )

        if stats["voice_seconds"] is not None:
            weekly_lines.append(
                f"> **Voice:** `{stats['voice_seconds'] / 3600:.1f} uur`"
            )

        if stats["top_game"] is not None:
            weekly_lines.append(
                f"> **Top game:** `{shorten(clean_game_channel_name(stats['top_game']), 32)}`"
            )

        if stats["top_player"] is not None:
            weekly_lines.append(
                f"> **Actiefste:** `{shorten(stats['top_player'].display_name, 32)}`"
            )

        if not weekly_lines:
            weekly_lines.append(
                "> Stats-module heeft nog geen weekdata."
            )

        embed.add_field(
            name="📊 DEZE WEEK",
            value="\n".join(weekly_lines),
            inline=False,
        )

        database_lines = []

        for label, path in DATABASE_FILES.items():
            if os.path.exists(path):
                try:
                    size = os.path.getsize(path)
                    size_kb = size / 1024
                    database_lines.append(
                        f"> 🟢 **{label}:** `{size_kb:.1f} KB`"
                    )
                except OSError:
                    database_lines.append(
                        f"> 🟡 **{label}:** aanwezig"
                    )
            else:
                database_lines.append(
                    f"> ⚫ **{label}:** niet aangemaakt"
                )

        embed.add_field(
            name="💾 DATABASES",
            value="\n".join(database_lines),
            inline=True,
        )

        embed.add_field(
            name="🧩 SYSTEEM",
            value=(
                f"> **Modules:** `{len(loaded_modules)}/{len(EXPECTED_MODULES)}`\n"
                f"> **Commands:** `{format_number(command_count)}`\n"
                f"> **Servers:** `{format_number(len(self.bot.guilds))}`\n"
                f"> **OS:** `{platform.system()} {platform.release()}`\n"
                f"> **Statussen:** `20`"
            ),
            inline=True,
        )

        bot_created = (
            discord.utils.format_dt(
                self.bot.user.created_at,
                style="D",
            )
            if self.bot.user is not None
            else "Onbekend"
        )

        embed.add_field(
            name="ℹ️ APPLICATIE",
            value=(
                f"> **Naam:** `{self.bot.user.name if self.bot.user else 'LEVENLOOS'}`\n"
                f"> **ID:** `{self.bot.user.id if self.bot.user else 'Onbekend'}`\n"
                f"> **Aangemaakt:** {bot_created}\n"
                f"> **Statusrotatie:** `{STATUS_ROTATION_MINUTES} min`\n"
                f"> **Slash commands:** `{command_count}`"
            ),
            inline=False,
        )

        embed.set_footer(
            text="LEVENLOOS • Bot Info"
        )

        return embed

    def build_modules_embed(self) -> discord.Embed:
        loaded = []
        unloaded = []

        for module in EXPECTED_MODULES:
            label = MODULE_LABELS.get(
                module,
                module.title(),
            )

            if extension_loaded(self.bot, module):
                loaded.append(f"🟢 {label}")
            else:
                unloaded.append(f"⚫ {label}")

        embed = discord.Embed(
            title="🧩 LEVENLOOS MODULES",
            color=PURPLE,
            description=(
                f"**{len(loaded)}/{len(EXPECTED_MODULES)}** modules geladen."
            ),
        )

        embed.add_field(
            name="Geladen",
            value="\n".join(loaded) or "Geen",
            inline=True,
        )

        embed.add_field(
            name="Uitgeschakeld",
            value="\n".join(unloaded) or "Geen",
            inline=True,
        )

        return embed

    def build_commands_embed(
        self,
        page: int = 0,
    ) -> tuple[discord.Embed, int, int]:

        all_commands = self.command_list()
        per_page = 15

        pages = max(
            1,
            (len(all_commands) + per_page - 1)
            // per_page,
        )

        page = max(
            0,
            min(page, pages - 1),
        )

        start = page * per_page
        end = min(
            start + per_page,
            len(all_commands),
        )

        lines = []

        for command in all_commands[start:end]:
            description = (
                command.description
                or "Geen beschrijving"
            )

            lines.append(
                f"> `{command_path(command)}`\n"
                f"> └ {shorten(description, 80)}"
            )

        if not lines:
            lines.append(
                "> Er zijn momenteel geen slash commands geladen."
            )

        embed = discord.Embed(
            title="⌨️ LEVENLOOS COMMANDS",
            description="\n\n".join(lines),
            color=PURPLE,
        )

        embed.set_footer(
            text=(
                f"Pagina {page + 1}/{pages} • "
                f"{len(all_commands)} commands"
            )
        )

        return embed, page, pages

    # =====================================================
    # /BOT INFO
    # =====================================================

    @info_group.command(
        name="info",
        description="Bekijk informatie en statistieken van de LEVENLOOS bot",
    )
    async def info_command(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Dit command werkt alleen in een server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        embed = await self.build_info_embed(
            interaction.guild
        )

        await interaction.followup.send(
            embed=embed,
            view=BotInfoView(
                self,
                interaction.user.id,
            ),
        )

    # =====================================================
    # /BOT COMMANDS
    # =====================================================

    @info_group.command(
        name="commands",
        description="Bekijk alle slash commands van de LEVENLOOS bot",
    )
    async def commands_command(
        self,
        interaction: discord.Interaction,
    ):
        embed, page, pages = self.build_commands_embed(
            0
        )

        await interaction.response.send_message(
            embed=embed,
            view=CommandPagesView(
                self,
                interaction.user.id,
                page,
                pages,
            ),
            ephemeral=True,
        )

    # =====================================================
    # /BOT MODULES
    # =====================================================

    @info_group.command(
        name="modules",
        description="Bekijk welke botmodules geladen zijn",
    )
    async def modules_command(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.send_message(
            embed=self.build_modules_embed(),
            ephemeral=True,
        )

    # =====================================================
    # /BOT STATUS
    # =====================================================

    @info_group.command(
        name="status",
        description="Ga als admin direct naar de volgende botstatus",
    )
    async def status_command(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            return

        member = interaction.user

        if (
            not isinstance(member, discord.Member)
            or not management_access(member)
        ):
            await interaction.response.send_message(
                "❌ Je hebt geen toegang tot dit command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        await self.rotate_status()

        await interaction.followup.send(
            "✅ Botstatus doorgedraaid naar de volgende status.",
            ephemeral=True,
        )


# =========================================================
# INFO VIEW
# =========================================================

class BotInfoView(discord.ui.View):

    def __init__(
        self,
        cog: BotInfo,
        owner_id: int,
    ):
        super().__init__(
            timeout=900
        )

        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Dit menu hoort bij iemand anders.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Vernieuwen",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
    )
    async def refresh_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.guild is None:
            return

        embed = await self.cog.build_info_embed(
            interaction.guild
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

    @discord.ui.button(
        label="Commands",
        emoji="⌨️",
        style=discord.ButtonStyle.secondary,
    )
    async def commands_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        embed, page, pages = (
            self.cog.build_commands_embed(0)
        )

        await interaction.response.send_message(
            embed=embed,
            view=CommandPagesView(
                self.cog,
                interaction.user.id,
                page,
                pages,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Modules",
        emoji="🧩",
        style=discord.ButtonStyle.secondary,
    )
    async def modules_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await interaction.response.send_message(
            embed=self.cog.build_modules_embed(),
            ephemeral=True,
        )


# =========================================================
# COMMAND PAGINATION
# =========================================================

class CommandPagesView(discord.ui.View):

    def __init__(
        self,
        cog: BotInfo,
        owner_id: int,
        page: int,
        pages: int,
    ):
        super().__init__(
            timeout=900
        )

        self.cog = cog
        self.owner_id = owner_id
        self.page = page
        self.pages = pages

        self.update_buttons()

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "❌ Dit menu hoort bij iemand anders.",
                ephemeral=True,
            )
            return False

        return True

    def update_buttons(self):
        self.previous_button.disabled = (
            self.page <= 0
        )

        self.next_button.disabled = (
            self.page >= self.pages - 1
        )

        self.page_button.label = (
            f"{self.page + 1}/{self.pages}"
        )

    @discord.ui.button(
        label="Vorige",
        emoji="◀️",
        style=discord.ButtonStyle.primary,
    )
    async def previous_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.page = max(
            0,
            self.page - 1,
        )

        embed, self.page, self.pages = (
            self.cog.build_commands_embed(
                self.page
            )
        )

        self.update_buttons()

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

    @discord.ui.button(
        label="1/1",
        style=discord.ButtonStyle.secondary,
        disabled=True,
    )
    async def page_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        pass

    @discord.ui.button(
        label="Volgende",
        emoji="▶️",
        style=discord.ButtonStyle.primary,
    )
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        self.page = min(
            self.pages - 1,
            self.page + 1,
        )

        embed, self.page, self.pages = (
            self.cog.build_commands_embed(
                self.page
            )
        )

        self.update_buttons()

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )


# =========================================================
# SETUP
# =========================================================

async def setup(bot: commands.Bot):
    await bot.add_cog(
        BotInfo(bot)
    )
