import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks


# =========================================================
# CONFIG
# =========================================================

DATABASE_PATH = "levenloos.db"

ACTIVE_GAME_CATEGORY_ID = 940987098427707412
GAME_PROPOSAL_CHANNEL_ID = 940336215331319858
ARCHIVE_GAME_CATEGORY_ID = 1545088593716973568
ARCHIVE_RESTORE_CHANNEL_ID = 1545089501745913916

STATS_CATEGORY_NAME = "📊 SERVER STATS"

MEMBER_UPDATE_MINUTES = 5
ROTATION_MINUTES = 20

TIMEZONE = ZoneInfo("Europe/Amsterdam")


# =========================================================
# DATABASE
# =========================================================

async def initialise_stats_database():

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        await db.execute(
            "PRAGMA journal_mode=WAL"
        )

        await db.execute(
            "PRAGMA busy_timeout=5000"
        )

        # ---------------------------------------------
        # CONFIG PER SERVER
        # ---------------------------------------------

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stats_config (
                guild_id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL,
                members_channel_id INTEGER NOT NULL,
                date_channel_id INTEGER,
                rotating_channel_id INTEGER NOT NULL,
                rotation_index INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # Bestaande installaties automatisch uitbreiden met date_channel_id.
        cursor = await db.execute(
            "PRAGMA table_info(stats_config)"
        )
        columns = {
            row[1]
            for row in await cursor.fetchall()
        }

        if "date_channel_id" not in columns:
            await db.execute(
                "ALTER TABLE stats_config ADD COLUMN date_channel_id INTEGER"
            )

        # ---------------------------------------------
        # USER WEEK STATS
        # ---------------------------------------------

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stats_weekly_users (
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,
                voice_seconds INTEGER NOT NULL DEFAULT 0,

                PRIMARY KEY (
                    guild_id,
                    week_start,
                    user_id
                )
            )
            """
        )

        # ---------------------------------------------
        # GAME CHANNEL WEEK STATS
        # ---------------------------------------------

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stats_weekly_games (
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,

                PRIMARY KEY (
                    guild_id,
                    week_start,
                    channel_id
                )
            )
            """
        )

        # ---------------------------------------------
        # ACTIVE VOICE SESSIONS
        # ---------------------------------------------

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS stats_voice_sessions (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                started_at REAL NOT NULL,

                PRIMARY KEY (
                    guild_id,
                    user_id
                )
            )
            """
        )

        await db.commit()

    print("[STATS DB] Database klaar")


# =========================================================
# WEEK HELPERS
# =========================================================

def get_week_start(
    moment: datetime | None = None
) -> str:

    if moment is None:
        moment = datetime.now(
            TIMEZONE
        )

    if moment.tzinfo is None:
        moment = moment.replace(
            tzinfo=TIMEZONE
        )
    else:
        moment = moment.astimezone(
            TIMEZONE
        )

    monday = (
        moment
        -
        timedelta(
            days=moment.weekday()
        )
    )

    return monday.date().isoformat()


def get_week_start_timestamp() -> float:

    now = datetime.now(
        TIMEZONE
    )

    monday = (
        now
        -
        timedelta(
            days=now.weekday()
        )
    ).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    return monday.timestamp()


# =========================================================
# PERMISSIONS
# =========================================================

def management_access(
    member: discord.Member
) -> bool:

    if member.guild.owner_id == member.id:
        return True

    if member.guild_permissions.administrator:
        return True

    role_names = {
        role.name.lower()
        for role in member.roles
    }

    return (
        "admin" in role_names
        or
        "owner" in role_names
    )


# =========================================================
# TEXT HELPERS
# =========================================================

def shorten(
    text: str,
    maximum: int = 70
) -> str:

    text = str(text).strip()

    if len(text) <= maximum:
        return text

    return text[:maximum - 1] + "…"


def game_channel_display_name(
    channel: discord.TextChannel
) -> str:

    name = channel.name

    name = name.lstrip(
        "▏│┃|◆ "
    )

    name = name.replace(
        "-",
        " "
    )

    return name.title()


def format_number(
    number: int
) -> str:

    return f"{number:,}".replace(
        ",",
        "."
    )


def format_voice_hours(
    seconds: int
) -> str:

    hours = seconds / 3600

    if hours < 10:
        return f"{hours:.1f}u"

    return f"{hours:.0f}u"


def format_date_channel() -> str:
    now = datetime.now(
        TIMEZONE
    )

    weekdays = [
        "MA",
        "DI",
        "WO",
        "DO",
        "VR",
        "ZA",
        "ZO"
    ]

    months = [
        "JAN",
        "FEB",
        "MRT",
        "APR",
        "MEI",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OKT",
        "NOV",
        "DEC"
    ]

    return (
        f"▏📅 {weekdays[now.weekday()]} "
        f"{now.day} {months[now.month - 1]} {now.year}"
    )


# =========================================================
# STATS COG
# =========================================================

class Stats(
    commands.Cog
):

    def __init__(
        self,
        bot: commands.Bot
    ):

        self.bot = bot

        self.db_lock = asyncio.Lock()

        self.ready_guilds = set()

    # =====================================================
    # LOAD / UNLOAD
    # =====================================================

    async def cog_load(
        self
    ):

        await initialise_stats_database()

        self.member_update_loop.start()

        self.rotation_loop.start()

        print("[STATS] Stats module geladen")

    def cog_unload(
        self
    ):

        self.member_update_loop.cancel()

        self.rotation_loop.cancel()

    # =====================================================
    # /STATS GROUP
    # =====================================================

    stats = app_commands.Group(
        name="stats",
        description="Beheer de server statistieken"
    )

    # =====================================================
    # SETUP
    # =====================================================

    @stats.command(
        name="setup",
        description="Maak de server stats kanalen aan"
    )
    async def stats_setup(
        self,
        interaction: discord.Interaction
    ):

        if interaction.guild is None:
            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):
            return

        if not management_access(
            member
        ):

            await interaction.response.send_message(
                "❌ Je hebt geen toegang tot dit command.",
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        guild = interaction.guild

        # ---------------------------------------------
        # BESTAANDE CONFIG
        # ---------------------------------------------

        config = await self.get_config(
            guild.id
        )

        if config:

            category = guild.get_channel(
                config["category_id"]
            )

            members_channel = guild.get_channel(
                config["members_channel_id"]
            )

            date_channel = guild.get_channel(
                config["date_channel_id"]
            ) if config["date_channel_id"] else None

            rotating_channel = guild.get_channel(
                config["rotating_channel_id"]
            )

            if (
                isinstance(
                    category,
                    discord.CategoryChannel
                )
                and
                isinstance(
                    members_channel,
                    discord.VoiceChannel
                )
                and
                isinstance(
                    rotating_channel,
                    discord.VoiceChannel
                )
            ):

                if not isinstance(
                    date_channel,
                    discord.VoiceChannel
                ):
                    date_channel = await guild.create_voice_channel(
                        format_date_channel(),
                        category=category,
                        overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False)},
                        reason="Server stats datum kanaal"
                    )

                    async with aiosqlite.connect(
                        DATABASE_PATH
                    ) as db:
                        await db.execute(
                            """
                            UPDATE stats_config
                            SET date_channel_id = ?
                            WHERE guild_id = ?
                            """,
                            (
                                date_channel.id,
                                guild.id
                            )
                        )
                        await db.commit()

                await self.update_members_channel(
                    guild
                )

                await self.update_date_channel(
                    guild
                )

                await self.update_rotating_channel(
                    guild,
                    force_current=True
                )

                await interaction.followup.send(
                    "✅ Server stats bestaan al en zijn bijgewerkt.",
                    ephemeral=True
                )

                return

        # ---------------------------------------------
        # CATEGORY
        # ---------------------------------------------

        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=True,
                    connect=False
                )
        }

        category = await guild.create_category(
            STATS_CATEGORY_NAME,
            reason="Server stats setup"
        )

        # ---------------------------------------------
        # MEMBERS
        # ---------------------------------------------

        members_channel = await guild.create_voice_channel(
            f"▏👥 Members: {guild.member_count or 0}",
            category=category,
            overwrites=overwrites,
            reason="Server stats setup"
        )

        # ---------------------------------------------
        # DATUM
        # ---------------------------------------------

        date_channel = await guild.create_voice_channel(
            format_date_channel(),
            category=category,
            overwrites=overwrites,
            reason="Server stats setup"
        )

        # ---------------------------------------------
        # ROTATING
        # ---------------------------------------------

        rotating_channel = await guild.create_voice_channel(
            "▏📊 Stats worden geladen...",
            category=category,
            overwrites=overwrites,
            reason="Server stats setup"
        )

        # ---------------------------------------------
        # SAVE
        # ---------------------------------------------

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            await db.execute(
                """
                INSERT OR REPLACE INTO stats_config (
                    guild_id,
                    category_id,
                    members_channel_id,
                    date_channel_id,
                    rotating_channel_id,
                    rotation_index
                )
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (
                    guild.id,
                    category.id,
                    members_channel.id,
                    date_channel.id,
                    rotating_channel.id
                )
            )

            await db.commit()

        await self.update_rotating_channel(
            guild,
            force_current=True
        )

        await interaction.followup.send(
            "✅ **Server stats aangemaakt.**\n\n"
            f"👥 {members_channel.mention}\n"
            f"📅 {date_channel.mention}\n"
            f"📊 {rotating_channel.mention}",
            ephemeral=True
        )

    # =====================================================
    # FORCE UPDATE
    # =====================================================

    @stats.command(
        name="update",
        description="Werk de server stats direct bij"
    )
    async def stats_update(
        self,
        interaction: discord.Interaction
    ):

        if interaction.guild is None:
            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):
            return

        if not management_access(
            member
        ):

            await interaction.response.send_message(
                "❌ Je hebt geen toegang.",
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        await self.update_members_channel(
            interaction.guild
        )

        await self.update_date_channel(
            interaction.guild
        )

        await self.update_rotating_channel(
            interaction.guild,
            force_current=True
        )

        await interaction.followup.send(
            "✅ Stats bijgewerkt.",
            ephemeral=True
        )

    # =====================================================
    # NEXT ROTATING STAT
    # =====================================================

    @stats.command(
        name="next",
        description="Ga direct naar de volgende server stat"
    )
    async def stats_next(
        self,
        interaction: discord.Interaction
    ):

        if interaction.guild is None:
            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):
            return

        if not management_access(
            member
        ):

            await interaction.response.send_message(
                "❌ Je hebt geen toegang.",
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        config = await self.get_config(
            interaction.guild.id
        )

        if not config:

            await interaction.followup.send(
                "❌ Server stats zijn nog niet ingesteld. Gebruik eerst `/stats setup`.",
                ephemeral=True
            )

            return

        await self.update_rotating_channel(
            interaction.guild,
            force_current=False
        )

        await interaction.followup.send(
            "✅ Volgende stat weergegeven.",
            ephemeral=True
        )

    # =====================================================
    # GET CONFIG
    # =====================================================

    async def get_config(
        self,
        guild_id: int
    ):

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    category_id,
                    members_channel_id,
                    date_channel_id,
                    rotating_channel_id,
                    rotation_index

                FROM stats_config

                WHERE guild_id = ?
                """,
                (
                    guild_id,
                )
            )

            row = await cursor.fetchone()

        if not row:
            return None

        return {
            "category_id": row[0],
            "members_channel_id": row[1],
            "date_channel_id": row[2],
            "rotating_channel_id": row[3],
            "rotation_index": row[4]
        }

    # =====================================================
    # MESSAGE TRACKING
    # =====================================================

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):

        if message.guild is None:
            return

        if message.author.bot:
            return

        week = get_week_start()

        async with self.db_lock:

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                await db.execute(
                    "PRAGMA busy_timeout=5000"
                )

                # -----------------------------------------
                # USER MESSAGE
                # -----------------------------------------

                await db.execute(
                    """
                    INSERT INTO stats_weekly_users (
                        guild_id,
                        week_start,
                        user_id,
                        messages,
                        voice_seconds
                    )
                    VALUES (?, ?, ?, 1, 0)

                    ON CONFLICT(
                        guild_id,
                        week_start,
                        user_id
                    )

                    DO UPDATE SET
                        messages = messages + 1
                    """,
                    (
                        message.guild.id,
                        week,
                        message.author.id
                    )
                )

                # -----------------------------------------
                # GAME MESSAGE
                # -----------------------------------------

                if (
                    isinstance(
                        message.channel,
                        discord.TextChannel
                    )
                    and
                    message.channel.category_id
                    ==
                    ACTIVE_GAME_CATEGORY_ID
                    and
                    message.channel.id
                    !=
                    GAME_PROPOSAL_CHANNEL_ID
                ):

                    await db.execute(
                        """
                        INSERT INTO stats_weekly_games (
                            guild_id,
                            week_start,
                            channel_id,
                            messages
                        )
                        VALUES (?, ?, ?, 1)

                        ON CONFLICT(
                            guild_id,
                            week_start,
                            channel_id
                        )

                        DO UPDATE SET
                            messages = messages + 1
                        """,
                        (
                            message.guild.id,
                            week,
                            message.channel.id
                        )
                    )

                await db.commit()

    # =====================================================
    # VOICE TRACKING
    # =====================================================

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):

        if member.bot:
            return

        guild = member.guild

        # ---------------------------------------------
        # JOIN
        # ---------------------------------------------

        if (
            before.channel is None
            and
            after.channel is not None
        ):

            await self.start_voice_session(
                guild.id,
                member.id,
                after.channel.id
            )

            return

        # ---------------------------------------------
        # LEAVE
        # ---------------------------------------------

        if (
            before.channel is not None
            and
            after.channel is None
        ):

            await self.finish_voice_session(
                guild.id,
                member.id
            )

            return

        # ---------------------------------------------
        # MOVE
        # ---------------------------------------------

        if (
            before.channel is not None
            and
            after.channel is not None
            and
            before.channel.id
            !=
            after.channel.id
        ):

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                await db.execute(
                    """
                    UPDATE stats_voice_sessions
                    SET channel_id = ?
                    WHERE guild_id = ?
                    AND user_id = ?
                    """,
                    (
                        after.channel.id,
                        guild.id,
                        member.id
                    )
                )

                await db.commit()

    async def start_voice_session(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int
    ):

        async with self.db_lock:

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                await db.execute(
                    """
                    INSERT OR IGNORE INTO stats_voice_sessions (
                        guild_id,
                        user_id,
                        channel_id,
                        started_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        guild_id,
                        user_id,
                        channel_id,
                        time.time()
                    )
                )

                await db.commit()

    async def finish_voice_session(
        self,
        guild_id: int,
        user_id: int
    ):

        async with self.db_lock:

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                cursor = await db.execute(
                    """
                    SELECT started_at
                    FROM stats_voice_sessions
                    WHERE guild_id = ?
                    AND user_id = ?
                    """,
                    (
                        guild_id,
                        user_id
                    )
                )

                row = await cursor.fetchone()

                if not row:
                    return

                started_at = row[0]

                ended_at = time.time()

                await db.execute(
                    """
                    DELETE FROM stats_voice_sessions
                    WHERE guild_id = ?
                    AND user_id = ?
                    """,
                    (
                        guild_id,
                        user_id
                    )
                )

                await db.commit()

        await self.add_voice_duration(
            guild_id,
            user_id,
            started_at,
            ended_at
        )

    async def add_voice_duration(
        self,
        guild_id: int,
        user_id: int,
        started_at: float,
        ended_at: float
    ):

        if ended_at <= started_at:
            return

        cursor_time = started_at

        while cursor_time < ended_at:

            local_start = datetime.fromtimestamp(
                cursor_time,
                TIMEZONE
            )

            week = get_week_start(
                local_start
            )

            monday = (
                local_start
                -
                timedelta(
                    days=local_start.weekday()
                )
            ).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0
            )

            next_monday = (
                monday
                +
                timedelta(
                    days=7
                )
            )

            chunk_end = min(
                ended_at,
                next_monday.timestamp()
            )

            seconds = int(
                chunk_end - cursor_time
            )

            if seconds > 0:

                async with aiosqlite.connect(
                    DATABASE_PATH
                ) as db:

                    await db.execute(
                        """
                        INSERT INTO stats_weekly_users (
                            guild_id,
                            week_start,
                            user_id,
                            messages,
                            voice_seconds
                        )
                        VALUES (?, ?, ?, 0, ?)

                        ON CONFLICT(
                            guild_id,
                            week_start,
                            user_id
                        )

                        DO UPDATE SET
                            voice_seconds =
                            voice_seconds + excluded.voice_seconds
                        """,
                        (
                            guild_id,
                            week,
                            user_id,
                            seconds
                        )
                    )

                    await db.commit()

            cursor_time = chunk_end

    # =====================================================
    # BOT READY / VOICE RECONCILIATION
    # =====================================================

    @commands.Cog.listener()
    async def on_ready(
        self
    ):

        for guild in self.bot.guilds:

            if guild.id in self.ready_guilds:
                continue

            await self.reconcile_voice_sessions(
                guild
            )

            self.ready_guilds.add(
                guild.id
            )

    async def reconcile_voice_sessions(
        self,
        guild: discord.Guild
    ):

        currently_connected = {}

        for channel in guild.voice_channels:

            for member in channel.members:

                if member.bot:
                    continue

                currently_connected[
                    member.id
                ] = channel.id

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT user_id
                FROM stats_voice_sessions
                WHERE guild_id = ?
                """,
                (
                    guild.id,
                )
            )

            rows = await cursor.fetchall()

            stored_ids = {
                row[0]
                for row in rows
            }

            # Oude sessies verwijderen van mensen
            # die nu niet meer in voice zitten.
            for user_id in stored_ids:

                if user_id not in currently_connected:

                    await db.execute(
                        """
                        DELETE FROM stats_voice_sessions
                        WHERE guild_id = ?
                        AND user_id = ?
                        """,
                        (
                            guild.id,
                            user_id
                        )
                    )

            # Mensen die al in voice zitten toevoegen.
            for user_id, channel_id in currently_connected.items():

                await db.execute(
                    """
                    INSERT OR IGNORE INTO stats_voice_sessions (
                        guild_id,
                        user_id,
                        channel_id,
                        started_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        guild.id,
                        user_id,
                        channel_id,
                        time.time()
                    )
                )

            await db.commit()

        print(
            f"[STATS] Voice sessions gecontroleerd voor {guild.name}"
        )

    # =====================================================
    # WEEK DATA
    # =====================================================

    async def get_week_messages(
        self,
        guild: discord.Guild
    ) -> int:

        week = get_week_start()

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT COALESCE(
                    SUM(messages),
                    0
                )
                FROM stats_weekly_users
                WHERE guild_id = ?
                AND week_start = ?
                """,
                (
                    guild.id,
                    week
                )
            )

            row = await cursor.fetchone()

        return int(
            row[0] or 0
        )

    async def get_active_games_count(
        self,
        guild: discord.Guild
    ) -> int:

        category = guild.get_channel(
            ACTIVE_GAME_CATEGORY_ID
        )

        if not isinstance(
            category,
            discord.CategoryChannel
        ):
            return 0

        return len(
            [
                channel
                for channel in category.text_channels
                if channel.id
                !=
                GAME_PROPOSAL_CHANNEL_ID
            ]
        )

    async def get_archived_games_count(
        self,
        guild: discord.Guild
    ) -> int:

        category = guild.get_channel(
            ARCHIVE_GAME_CATEGORY_ID
        )

        if not isinstance(
            category,
            discord.CategoryChannel
        ):
            return 0

        return len(
            [
                channel
                for channel in category.text_channels
                if channel.id
                !=
                ARCHIVE_RESTORE_CHANNEL_ID
            ]
        )

    async def get_active_users_count(
        self,
        guild: discord.Guild
    ) -> int:

        week = get_week_start()

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT COUNT(*)
                FROM stats_weekly_users
                WHERE guild_id = ?
                AND week_start = ?
                AND (
                    messages > 0
                    OR voice_seconds > 0
                )
                """,
                (
                    guild.id,
                    week
                )
            )

            row = await cursor.fetchone()

        return int(
            row[0] or 0
        )

    def get_voice_now_count(
        self,
        guild: discord.Guild
    ) -> int:

        return sum(
            1
            for channel in guild.voice_channels
            for member in channel.members
            if not member.bot
        )

    async def get_top_game(
        self,
        guild: discord.Guild
    ):

        category = guild.get_channel(
            ACTIVE_GAME_CATEGORY_ID
        )

        if not isinstance(
            category,
            discord.CategoryChannel
        ):
            return None, 0

        channels = [
            channel
            for channel in category.text_channels
            if channel.id
            !=
            GAME_PROPOSAL_CHANNEL_ID
        ]

        if not channels:
            return None, 0

        ids = [
            channel.id
            for channel in channels
        ]

        placeholders = ",".join(
            "?"
            for _ in ids
        )

        week = get_week_start()

        query = f"""
            SELECT
                channel_id,
                messages
            FROM stats_weekly_games
            WHERE guild_id = ?
            AND week_start = ?
            AND channel_id IN ({placeholders})
            ORDER BY messages DESC
            LIMIT 1
        """

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                query,
                (
                    guild.id,
                    week,
                    *ids
                )
            )

            row = await cursor.fetchone()

        if not row:
            return None, 0

        channel = guild.get_channel(
            row[0]
        )

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return None, 0

        return channel, int(
            row[1]
        )

    async def get_voice_totals(
        self,
        guild: discord.Guild
    ):

        week = get_week_start()

        week_start_timestamp = (
            get_week_start_timestamp()
        )

        totals = {}

        # ---------------------------------------------
        # COMPLETED VOICE
        # ---------------------------------------------

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    user_id,
                    voice_seconds

                FROM stats_weekly_users

                WHERE guild_id = ?
                AND week_start = ?
                """,
                (
                    guild.id,
                    week
                )
            )

            rows = await cursor.fetchall()

            for user_id, seconds in rows:

                totals[user_id] = (
                    totals.get(
                        user_id,
                        0
                    )
                    +
                    int(seconds or 0)
                )

            # -----------------------------------------
            # CURRENT ACTIVE VOICE
            # -----------------------------------------

            cursor = await db.execute(
                """
                SELECT
                    user_id,
                    started_at

                FROM stats_voice_sessions

                WHERE guild_id = ?
                """,
                (
                    guild.id,
                )
            )

            active_rows = await cursor.fetchall()

        now = time.time()

        for user_id, started_at in active_rows:

            counted_from = max(
                started_at,
                week_start_timestamp
            )

            seconds = max(
                0,
                int(
                    now - counted_from
                )
            )

            totals[user_id] = (
                totals.get(
                    user_id,
                    0
                )
                +
                seconds
            )

        return totals

    async def get_top_player(
        self,
        guild: discord.Guild
    ):

        week = get_week_start()

        user_data = {}

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    user_id,
                    messages,
                    voice_seconds

                FROM stats_weekly_users

                WHERE guild_id = ?
                AND week_start = ?
                """,
                (
                    guild.id,
                    week
                )
            )

            rows = await cursor.fetchall()

        for (
            user_id,
            messages,
            voice_seconds
        ) in rows:

            user_data[user_id] = {
                "messages":
                    int(messages or 0),

                "voice":
                    int(voice_seconds or 0)
            }

        # Voeg actieve voice tijd toe
        voice_totals = await self.get_voice_totals(
            guild
        )

        for user_id, seconds in voice_totals.items():

            if user_id not in user_data:

                user_data[user_id] = {
                    "messages": 0,
                    "voice": 0
                }

            # get_voice_totals bevat completed + active.
            # Dus overschrijven i.p.v. optellen.
            user_data[user_id][
                "voice"
            ] = seconds

        best_member = None
        best_score = -1
        best_messages = 0
        best_voice_seconds = 0

        for user_id, data in user_data.items():

            member = guild.get_member(
                user_id
            )

            if member is None:
                continue

            if member.bot:
                continue

            # -----------------------------------------
            # ACTIVITY SCORE
            #
            # 1 bericht = 1 punt
            # 1 minuut voice = 1 punt
            # -----------------------------------------

            score = (
                data["messages"]
                +
                (
                    data["voice"]
                    / 60
                )
            )

            if score > best_score:

                best_score = score

                best_member = member
                best_messages = data["messages"]
                best_voice_seconds = data["voice"]

        return (
            best_member,
            best_score,
            best_messages,
            best_voice_seconds
        )

    # =====================================================
    # MEMBERS CHANNEL
    # =====================================================

    async def update_members_channel(
        self,
        guild: discord.Guild
    ):

        config = await self.get_config(
            guild.id
        )

        if not config:
            return

        channel = guild.get_channel(
            config["members_channel_id"]
        )

        if not isinstance(
            channel,
            discord.VoiceChannel
        ):
            return

        desired_name = (
            f"▏👥 Members: "
            f"{format_number(guild.member_count or 0)}"
        )

        if channel.name == desired_name:
            return

        try:

            await channel.edit(
                name=desired_name,
                reason="Server member stats update"
            )

            print(
                f"[STATS] Members → {desired_name}"
            )

        except discord.HTTPException as exc:

            print(
                f"[STATS] Member channel update fout: {exc}"
            )

    # =====================================================
    # DATE CHANNEL
    # =====================================================

    async def update_date_channel(
        self,
        guild: discord.Guild
    ):

        config = await self.get_config(
            guild.id
        )

        if not config:
            return

        channel = guild.get_channel(
            config["date_channel_id"]
        )

        if not isinstance(
            channel,
            discord.VoiceChannel
        ):
            return

        desired_name = format_date_channel()

        if channel.name == desired_name:
            return

        try:

            await channel.edit(
                name=desired_name,
                reason="Server stats datum update"
            )

            print(
                f"[STATS] Datum → {desired_name}"
            )

        except discord.HTTPException as exc:

            print(
                f"[STATS] Datum channel update fout: {exc}"
            )

    # =====================================================
    # ROTATING CHANNEL
    # =====================================================

    async def update_rotating_channel(
        self,
        guild: discord.Guild,
        force_current: bool = False
    ):

        config = await self.get_config(
            guild.id
        )

        if not config:
            return

        channel = guild.get_channel(
            config["rotating_channel_id"]
        )

        if not isinstance(
            channel,
            discord.VoiceChannel
        ):
            return

        rotation_index = (
            config["rotation_index"]
        )

        # ---------------------------------------------
        # STATS
        # ---------------------------------------------

        boosts = (
            guild.premium_subscription_count
            or 0
        )

        active_games = await self.get_active_games_count(
            guild
        )

        archived_games = await self.get_archived_games_count(
            guild
        )

        active_users = await self.get_active_users_count(
            guild
        )

        voice_now = self.get_voice_now_count(
            guild
        )

        week_messages = await self.get_week_messages(
            guild
        )

        top_game, top_game_messages = await self.get_top_game(
            guild
        )

        voice_totals = await self.get_voice_totals(
            guild
        )

        total_voice_seconds = sum(
            voice_totals.values()
        )

        (
            top_player,
            top_score,
            top_player_messages,
            top_player_voice_seconds
        ) = await self.get_top_player(
            guild
        )

        # ---------------------------------------------
        # DISPLAY VALUES
        # ---------------------------------------------

        if top_game:

            top_game_name = game_channel_display_name(
                top_game
            )

        else:

            top_game_name = "Nog geen data"

        if top_player:

            top_player_name = shorten(
                top_player.display_name,
                45
            )

        else:

            top_player_name = "Nog geen data"

        rotations = [
            f"▏🚀 Boosts: {format_number(boosts)}",

            (
                f"▏🎮 Games actief: "
                f"{format_number(active_games)}"
            ),

            (
                f"▏📦 Games archived: "
                f"{format_number(archived_games)}"
            ),

            (
                f"▏💬 Berichten deze week: "
                f"{format_number(week_messages)}"
            ),

            (
                f"▏👤 Actief deze week: "
                f"{format_number(active_users)} leden"
            ),

            (
                f"▏🔥 Gamechat #1: "
                f"{shorten(top_game_name, 34)} • "
                f"{format_number(top_game_messages)} msg"
            ),

            (
                f"▏🔊 Voice deze week: "
                f"{format_voice_hours(total_voice_seconds)}"
            ),

            (
                f"▏🎙️ Nu in voice: "
                f"{format_number(voice_now)}"
            ),

            (
                f"▏🏆 Actiefste: "
                f"{shorten(top_player_name, 26)} • "
                f"{format_number(top_player_messages)}💬 + "
                f"{format_voice_hours(top_player_voice_seconds)}🔊"
            )
        ]

        rotation_index %= len(
            rotations
        )

        # ---------------------------------------------
        # HUIDIGE STAT BEPALEN
        #
        # De database bewaart voortaan de stat die
        # daadwerkelijk zichtbaar is.
        #
        # Voor compatibiliteit met de oude versie
        # proberen we eerst de huidige channelnaam
        # terug te vinden in de rotatie.
        # ---------------------------------------------

        current_index = rotation_index

        for index, rotation_name in enumerate(
            rotations
        ):

            if channel.name == rotation_name:

                current_index = index
                break

        # /stats update toont dezelfde stat opnieuw.
        # De 20-minutenrotatie en /stats next gaan
        # precies één stat vooruit.
        if force_current:

            display_index = current_index

        else:

            display_index = (
                current_index + 1
            ) % len(rotations)

        desired_name = rotations[
            display_index
        ]

        # ---------------------------------------------
        # RENAME ALLEEN ALS NODIG
        # ---------------------------------------------

        if channel.name != desired_name:

            try:

                await channel.edit(
                    name=desired_name,
                    reason="Server stats rotatie"
                )

                print(
                    f"[STATS] Rotatie → {desired_name}"
                )

            except discord.HTTPException as exc:

                print(
                    f"[STATS] Rotation update fout: {exc}"
                )

                return

        # ---------------------------------------------
        # ZICHTBARE INDEX OPSLAAN
        # ---------------------------------------------

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            await db.execute(
                """
                UPDATE stats_config
                SET rotation_index = ?
                WHERE guild_id = ?
                """,
                (
                    display_index,
                    guild.id
                )
            )

            await db.commit()

    # =====================================================
    # BACKGROUND LOOPS
    # =====================================================

    @tasks.loop(
        minutes=MEMBER_UPDATE_MINUTES
    )
    async def member_update_loop(
        self
    ):

        for guild in self.bot.guilds:

            try:

                await self.update_members_channel(
                    guild
                )

                await self.update_date_channel(
                    guild
                )

            except Exception as exc:

                print(
                    f"[STATS MEMBERS LOOP] "
                    f"{type(exc).__name__}: {exc}"
                )

    @member_update_loop.before_loop
    async def before_member_update_loop(
        self
    ):

        await self.bot.wait_until_ready()

    @tasks.loop(
        minutes=ROTATION_MINUTES
    )
    async def rotation_loop(
        self
    ):

        for guild in self.bot.guilds:

            try:

                await self.update_rotating_channel(
                    guild
                )

            except Exception as exc:

                print(
                    f"[STATS ROTATION LOOP] "
                    f"{type(exc).__name__}: {exc}"
                )

    @rotation_loop.before_loop
    async def before_rotation_loop(
        self
    ):

        await self.bot.wait_until_ready()


# =========================================================
# EXTENSION SETUP
# =========================================================

async def setup(
    bot: commands.Bot
):

    await bot.add_cog(
        Stats(bot)
    )