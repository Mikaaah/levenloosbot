from __future__ import annotations

import calendar
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = ROOT / "birthdays.db"
TZ = ZoneInfo("Europe/Amsterdam")
DEFAULT_BIRTHDAY_CHANNEL_ID = 0


def is_admin(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id or member.guild_permissions.administrator:
        return True
    return any(role.name.lower() in {"admin", "owner"} for role in member.roles)


def month_name(month: int) -> str:
    names = [
        "", "januari", "februari", "maart", "april", "mei", "juni",
        "juli", "augustus", "september", "oktober", "november", "december"
    ]
    return names[month]


async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS birthdays (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                day INTEGER NOT NULL,
                month INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                updated_by INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS birthday_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL DEFAULT 0,
                announcements_enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS birthday_posts (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                message_id INTEGER,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, year)
            );
            CREATE TABLE IF NOT EXISTS birthday_congrats (
                guild_id INTEGER NOT NULL,
                birthday_user_id INTEGER NOT NULL,
                congratulator_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, birthday_user_id, congratulator_id, year)
            );
            """
        )
        await db.commit()


class BirthdayConfigView(discord.ui.View):
    def __init__(self, cog: "Birthdays", owner_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dit menu is niet van jou.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="📍 Gebruik dit kanaal", style=discord.ButtonStyle.primary)
    async def set_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.set_config(self.guild_id, channel_id=interaction.channel_id)
        await interaction.response.edit_message(content=await self.cog.render_config(self.guild_id), view=BirthdayConfigView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="🎂 Meldingen aan/uit", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel_id, enabled = await self.cog.get_config(self.guild_id)
        await self.cog.set_config(self.guild_id, announcements_enabled=not enabled)
        await interaction.response.edit_message(content=await self.cog.render_config(self.guild_id), view=BirthdayConfigView(self.cog, self.owner_id, self.guild_id))


class BirthdayButtonView(discord.ui.View):
    def __init__(self, birthday_user_id: int, year: int):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Feliciteer!",
                emoji="🎉",
                style=discord.ButtonStyle.success,
                custom_id=f"birthday_congrats:{birthday_user_id}:{year}",
            )
        )


class Birthdays(commands.Cog):
    verjaardag = app_commands.Group(name="verjaardag", description="Verjaardagen op LEVENLOOS.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await init_db()
        self.birthday_loop.start()

    def cog_unload(self):
        self.birthday_loop.cancel()

    async def get_config(self, guild_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO birthday_config (guild_id,channel_id,announcements_enabled) VALUES (?,?,1)",
                (guild_id, DEFAULT_BIRTHDAY_CHANNEL_ID),
            )
            await db.commit()
            cur = await db.execute("SELECT channel_id,announcements_enabled FROM birthday_config WHERE guild_id=?", (guild_id,))
            row = await cur.fetchone()
        return int(row[0]), bool(row[1])

    async def set_config(self, guild_id: int, channel_id=None, announcements_enabled=None):
        current_channel, current_enabled = await self.get_config(guild_id)
        if channel_id is None:
            channel_id = current_channel
        if announcements_enabled is None:
            announcements_enabled = current_enabled
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "INSERT INTO birthday_config (guild_id,channel_id,announcements_enabled) VALUES (?,?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,announcements_enabled=excluded.announcements_enabled",
                (guild_id, int(channel_id), int(bool(announcements_enabled))),
            )
            await db.commit()

    async def render_config(self, guild_id: int):
        channel_id, enabled = await self.get_config(guild_id)
        return (
            "🎂 **VERJAARDAGEN CONFIG**\n\n"
            f"Verjaardagsmeldingen: **{'AAN' if enabled else 'UIT'}**\n"
            f"Kanaal: {f'<#{channel_id}>' if channel_id else 'niet ingesteld'}\n\n"
            "De verjaardag van een lid kan na het instellen alleen door een admin worden gewijzigd."
        )

    async def get_birthday(self, guild_id: int, user_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT day,month FROM birthdays WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            return await cur.fetchone()

    async def set_birthday(self, guild_id: int, user_id: int, day: int, month: int, actor_id: int, allow_update: bool):
        now = int(time.time())
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT 1 FROM birthdays WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            exists = await cur.fetchone() is not None
            if exists and not allow_update:
                return False
            if exists:
                await db.execute("UPDATE birthdays SET day=?,month=?,updated_at=?,updated_by=? WHERE guild_id=? AND user_id=?", (day, month, now, actor_id, guild_id, user_id))
            else:
                await db.execute("INSERT INTO birthdays (guild_id,user_id,day,month,created_at,updated_at,updated_by) VALUES (?,?,?,?,?,?,?)", (guild_id, user_id, day, month, now, now, actor_id))
            await db.commit()
        return True

    def valid_date(self, day: int, month: int) -> bool:
        if month < 1 or month > 12:
            return False
        max_day = 29 if month == 2 else calendar.monthrange(2025, month)[1]
        return 1 <= day <= max_day

    @verjaardag.command(name="instellen", description="Stel éénmalig je verjaardag in.")
    async def instellen(self, interaction: discord.Interaction, dag: app_commands.Range[int, 1, 31], maand: app_commands.Range[int, 1, 12]):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        if not self.valid_date(dag, maand):
            await interaction.response.send_message("❌ Dat is geen geldige datum.", ephemeral=True)
            return
        existing = await self.get_birthday(interaction.guild.id, interaction.user.id)
        if existing:
            await interaction.response.send_message(
                f"❌ Je verjaardag staat al ingesteld op **{existing[0]} {month_name(existing[1])}**.\n\nAlleen een admin kan deze datum wijzigen.",
                ephemeral=True,
            )
            return
        await self.set_birthday(interaction.guild.id, interaction.user.id, dag, maand, interaction.user.id, False)
        await interaction.response.send_message(
            f"🎂 Je verjaardag is ingesteld op **{dag} {month_name(maand)}**.\n\nDeze datum kun je zelf niet meer wijzigen. Neem contact op met een admin als hij verkeerd staat.",
            ephemeral=True,
        )

    @verjaardag.command(name="wijzig", description="Wijzig als admin de verjaardag van een lid.")
    async def wijzig(self, interaction: discord.Interaction, member: discord.Member, dag: app_commands.Range[int, 1, 31], maand: app_commands.Range[int, 1, 12]):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        if not self.valid_date(dag, maand):
            await interaction.response.send_message("❌ Dat is geen geldige datum.", ephemeral=True)
            return
        await self.set_birthday(interaction.guild.id, member.id, dag, maand, interaction.user.id, True)
        await interaction.response.send_message(f"✅ De verjaardag van {member.mention} is gewijzigd naar **{dag} {month_name(maand)}**.", ephemeral=True)

    @verjaardag.command(name="verwijder", description="Verwijder als admin de verjaardag van een lid.")
    async def verwijder(self, interaction: discord.Interaction, member: discord.Member):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("DELETE FROM birthdays WHERE guild_id=? AND user_id=?", (interaction.guild.id, member.id))
            await db.commit()
        await interaction.response.send_message(f"✅ De verjaardag van {member.mention} is verwijderd.", ephemeral=True)

    @verjaardag.command(name="info", description="Bekijk een ingestelde verjaardag.")
    async def info(self, interaction: discord.Interaction, member: discord.Member | None = None):
        if not interaction.guild:
            return
        member = member or interaction.user
        row = await self.get_birthday(interaction.guild.id, member.id)
        if not row:
            await interaction.response.send_message(f"🎂 {member.mention} heeft nog geen verjaardag ingesteld.", ephemeral=True)
            return
        await interaction.response.send_message(f"🎂 Verjaardag van {member.mention}: **{row[0]} {month_name(row[1])}**.", ephemeral=True)

    @verjaardag.command(name="config", description="Open het verjaardagen configuratiepaneel.")
    async def config(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        await interaction.response.send_message(await self.render_config(interaction.guild.id), view=BirthdayConfigView(self, interaction.user.id, interaction.guild.id), ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component or not interaction.data:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("birthday_congrats:"):
            return
        parts = custom_id.split(":")
        if len(parts) != 3 or not interaction.guild:
            return
        birthday_user_id = int(parts[1])
        year = int(parts[2])
        if interaction.user.id == birthday_user_id:
            await interaction.response.send_message("Je kunt jezelf niet feliciteren 😭", ephemeral=True)
            return
        if getattr(interaction.user, "bot", False):
            return
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute(
                    "INSERT INTO birthday_congrats (guild_id,birthday_user_id,congratulator_id,year,created_at) VALUES (?,?,?,?,?)",
                    (interaction.guild.id, birthday_user_id, interaction.user.id, year, int(time.time())),
                )
                await db.commit()
        except aiosqlite.IntegrityError:
            await interaction.response.send_message(f"Je hebt <@{birthday_user_id}> al gefeliciteerd.", ephemeral=True)
            return
        levels = self.bot.get_cog("Levels")
        amount = 0
        if levels:
            amount = await levels.award_source(interaction.guild, interaction.user.id, "congrats", f"birthday_congrats:{birthday_user_id}:{interaction.user.id}:{year}", birthday_user_id)
        suffix = f" **+{amount} XP**" if amount else ""
        await interaction.response.send_message(f"🎉 Je hebt <@{birthday_user_id}> gefeliciteerd!{suffix}", ephemeral=True)

    @tasks.loop(minutes=10)
    async def birthday_loop(self):
        now = datetime.now(TZ)
        for guild in self.bot.guilds:
            channel_id, enabled = await self.get_config(guild.id)
            if not enabled or not channel_id:
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                continue
            async with aiosqlite.connect(DATABASE_PATH) as db:
                cur = await db.execute("SELECT user_id FROM birthdays WHERE guild_id=? AND day=? AND month=?", (guild.id, now.day, now.month))
                users = [r[0] for r in await cur.fetchall()]
            for user_id in users:
                member = guild.get_member(user_id)
                if not member or member.bot:
                    continue
                try:
                    async with aiosqlite.connect(DATABASE_PATH) as db:
                        await db.execute("INSERT INTO birthday_posts (guild_id,user_id,year,created_at) VALUES (?,?,?,?)", (guild.id, user_id, now.year, int(time.time())))
                        await db.commit()
                except aiosqlite.IntegrityError:
                    continue
                levels = self.bot.get_cog("Levels")
                amount = 0
                if levels:
                    amount = await levels.award_source(guild, user_id, "birthday", f"birthday:{user_id}:{now.year}", user_id)
                suffix = f"\nJe hebt **+{amount} XP** gekregen." if amount else ""
                msg = await channel.send(
                    f"🎂 Vandaag is {member.mention} jarig!\n\nGefeliciteerd! 🥳{suffix}",
                    view=BirthdayButtonView(user_id, now.year),
                )
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute("UPDATE birthday_posts SET message_id=? WHERE guild_id=? AND user_id=? AND year=?", (msg.id, guild.id, user_id, now.year))
                    await db.commit()

    @birthday_loop.before_loop
    async def before_birthday_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Birthdays(bot))
