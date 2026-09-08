import asyncio
import binascii
import io
import re
import struct
import zlib
from datetime import datetime, timedelta, timezone

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks


DB_PATH = "levenloos.db"
PURPLE = discord.Color.from_rgb(108, 39, 218)

SUGGESTION_COOLDOWN_MINUTES = 5
VOTING_DAYS = 7

HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")

LEVEL_CHOICES = [
    app_commands.Choice(name="Level 25", value=25),
    app_commands.Choice(name="Level 50", value=50),
    app_commands.Choice(name="Level 75", value=75),
    app_commands.Choice(name="Level 100", value=100),
    app_commands.Choice(name="Level 125", value=125),
    app_commands.Choice(name="Level 150", value=150),
]

MANAGE_ACTION_CHOICES = [
    app_commands.Choice(name="Accepteren", value="accepted"),
    app_commands.Choice(name="Afwijzen", value="rejected"),
    app_commands.Choice(name="Sluiten", value="closed"),
    app_commands.Choice(name="Heropenen", value="active"),
    app_commands.Choice(name="Verwijderen", value="delete"),
]


def has_management_access(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id:
        return True

    if member.guild_permissions.administrator:
        return True

    role_names = {role.name.casefold() for role in member.roles}
    return "admin" in role_names or "owner" in role_names


def normalize_hex(value: str) -> str:
    value = value.strip()

    if not value.startswith("#"):
        value = f"#{value}"

    if not HEX_RE.fullmatch(value):
        raise ValueError("Ongeldige hexkleur")

    return value.upper()


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")

    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
    )


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    payload = chunk_type + data

    return (
        struct.pack(">I", len(data))
        + payload
        + struct.pack(
            ">I",
            binascii.crc32(payload) & 0xFFFFFFFF,
        )
    )


def make_gradient_png(
    color1: str,
    color2: str | None,
    width: int = 900,
    height: int = 180,
) -> bytes:
    rgb1 = hex_to_rgb(color1)
    rgb2 = hex_to_rgb(color2 or color1)

    row = bytearray([0])

    for x in range(width):
        t = x / max(width - 1, 1)

        r = round(rgb1[0] + (rgb2[0] - rgb1[0]) * t)
        g = round(rgb1[1] + (rgb2[1] - rgb1[1]) * t)
        b = round(rgb1[2] + (rgb2[2] - rgb1[2]) * t)

        row.extend((r, g, b))

    raw = bytes(row) * height

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    return (
        signature
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(raw, 9))
        + png_chunk(b"IEND", b"")
    )


def status_text(status: str) -> str:
    return {
        "active": "🟣 Open",
        "pending_review": "🟡 Wacht op beoordeling",
        "accepted": "✅ Geaccepteerd",
        "rejected": "❌ Afgewezen",
        "closed": "🔒 Gesloten",
        "cancelled": "🚫 Geannuleerd",
    }.get(status, status)


def type_text(suggestion_type: str) -> tuple[str, str]:
    return {
        "color": ("🎨", "Kleur"),
        "general": ("💡", "Algemeen"),
        "bot": ("🤖", "Bot"),
        "event": ("📅", "Event"),
    }.get(suggestion_type, ("💡", "Suggestie"))


class VoteButton(discord.ui.Button):
    def __init__(
        self,
        cog: "Suggestions",
        suggestion_id: int,
        vote: int,
        count: int,
        disabled: bool = False,
    ):
        self.cog = cog
        self.suggestion_id = suggestion_id
        self.vote = vote

        if vote == 1:
            super().__init__(
                label=f"Voor • {count}",
                emoji="👍",
                style=discord.ButtonStyle.success,
                custom_id=f"suggestion:{suggestion_id}:up",
                disabled=disabled,
            )
        else:
            super().__init__(
                label=f"Tegen • {count}",
                emoji="👎",
                style=discord.ButtonStyle.danger,
                custom_id=f"suggestion:{suggestion_id}:down",
                disabled=disabled,
            )

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_vote(
            interaction,
            self.suggestion_id,
            self.vote,
        )


class CancelSuggestionButton(discord.ui.Button):
    def __init__(
        self,
        cog: "Suggestions",
        suggestion_id: int,
        disabled: bool = False,
    ):
        self.cog = cog
        self.suggestion_id = suggestion_id

        super().__init__(
            label="Annuleren",
            emoji="✖️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"suggestion:{suggestion_id}:cancel",
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_cancel(
            interaction,
            self.suggestion_id,
        )


class SuggestionVoteView(discord.ui.View):
    def __init__(
        self,
        cog: "Suggestions",
        suggestion_id: int,
        upvotes: int = 0,
        downvotes: int = 0,
        disabled: bool = False,
    ):
        super().__init__(timeout=None)

        self.add_item(
            VoteButton(
                cog,
                suggestion_id,
                1,
                upvotes,
                disabled,
            )
        )

        self.add_item(
            VoteButton(
                cog,
                suggestion_id,
                -1,
                downvotes,
                disabled,
            )
        )

        self.add_item(
            CancelSuggestionButton(
                cog,
                suggestion_id,
                disabled,
            )
        )


class Suggestions(commands.Cog):
    suggestie = app_commands.Group(
        name="suggestie",
        description="Suggesties voor LEVENLOOS.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_lock = asyncio.Lock()

    async def cog_load(self):
        await self.init_db()
        await self.restore_views()

        if not self.expire_suggestions.is_running():
            self.expire_suggestions.start()

    async def cog_unload(self):
        self.expire_suggestions.cancel()

    async def init_db(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS suggestions_config (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS suggestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER,
                    author_id INTEGER NOT NULL,
                    suggestion_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    level INTEGER,
                    color1 TEXT,
                    color2 TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS suggestion_votes (
                    suggestion_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    vote INTEGER NOT NULL CHECK(vote IN (-1, 1)),
                    PRIMARY KEY (suggestion_id, user_id),
                    FOREIGN KEY (suggestion_id)
                        REFERENCES suggestions(id)
                        ON DELETE CASCADE
                )
                """
            )

            await db.commit()

    async def get_suggestion_channel_id(self, guild_id: int) -> int | None:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT channel_id
                FROM suggestions_config
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            row = await cursor.fetchone()

        return int(row[0]) if row else None

    async def set_suggestion_channel(
        self,
        guild_id: int,
        channel_id: int,
    ):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO suggestions_config (
                    guild_id,
                    channel_id
                )
                VALUES (?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    channel_id = excluded.channel_id
                """,
                (guild_id, channel_id),
            )
            await db.commit()

    async def get_suggestion(self, suggestion_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row

            cursor = await db.execute(
                """
                SELECT *
                FROM suggestions
                WHERE id = ?
                """,
                (suggestion_id,),
            )
            return await cursor.fetchone()

    async def get_vote_counts(
        self,
        suggestion_id: int,
    ) -> tuple[int, int]:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT
                    SUM(CASE WHEN vote = 1 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN vote = -1 THEN 1 ELSE 0 END)
                FROM suggestion_votes
                WHERE suggestion_id = ?
                """,
                (suggestion_id,),
            )
            row = await cursor.fetchone()

        return int(row[0] or 0), int(row[1] or 0)

    async def check_command_channel(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Dit command kan alleen in een server gebruikt worden.",
                ephemeral=True,
            )
            return False

        configured_channel_id = await self.get_suggestion_channel_id(
            interaction.guild.id
        )

        if configured_channel_id is None:
            await interaction.response.send_message(
                "Er is nog geen suggestiekanaal ingesteld. "
                "Een admin kan dit doen met `/suggestie setup`.",
                ephemeral=True,
            )
            return False

        if interaction.channel.id != configured_channel_id:
            await interaction.response.send_message(
                f"Gebruik dit command in <#{configured_channel_id}>.",
                ephemeral=True,
            )
            return False

        return True

    async def check_cooldown(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(minutes=SUGGESTION_COOLDOWN_MINUTES)
        ).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT created_at
                FROM suggestions
                WHERE guild_id = ?
                  AND author_id = ?
                  AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    interaction.guild.id,
                    interaction.user.id,
                    cutoff,
                ),
            )
            row = await cursor.fetchone()

        if row is None:
            return True

        last_time = datetime.fromisoformat(row[0])
        retry_at = last_time + timedelta(
            minutes=SUGGESTION_COOLDOWN_MINUTES
        )

        remaining_seconds = max(
            1,
            int(
                (
                    retry_at
                    - datetime.now(timezone.utc)
                ).total_seconds()
            ),
        )

        minutes = max(1, (remaining_seconds + 59) // 60)

        await interaction.response.send_message(
            f"Wacht nog ongeveer **{minutes} minuut/minuten** "
            "voordat je een nieuwe suggestie plaatst.",
            ephemeral=True,
        )
        return False

    async def restore_views(self):
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT id, message_id
                FROM suggestions
                WHERE status = 'active'
                  AND message_id IS NOT NULL
                """
            )
            rows = await cursor.fetchall()

        for suggestion_id, message_id in rows:
            upvotes, downvotes = await self.get_vote_counts(suggestion_id)

            self.bot.add_view(
                SuggestionVoteView(
                    self,
                    suggestion_id,
                    upvotes,
                    downvotes,
                ),
                message_id=message_id,
            )

    async def build_embed(
        self,
        suggestion,
        upvotes: int,
        downvotes: int,
    ) -> discord.Embed:
        emoji, label = type_text(
            suggestion["suggestion_type"]
        )

        parts = []

        if suggestion["suggestion_type"] == "color":
            if suggestion["color2"]:
                color_value = (
                    f"`{suggestion['color1']}` → "
                    f"`{suggestion['color2']}`"
                )
                mode = "Gradient"
            else:
                color_value = f"`{suggestion['color1']}`"
                mode = "Solid"

            parts.extend(
                [
                    f"**Levelcategorie:** {suggestion['level']}",
                    f"**Type:** {mode}",
                    f"**Kleur:** {color_value}",
                ]
            )
        elif suggestion["description"]:
            parts.append(suggestion["description"])

        parts.extend(
            [
                "",
                f"Voorgesteld door <@{suggestion['author_id']}>",
                f"**Status:** {status_text(suggestion['status'])}",
            ]
        )

        if suggestion["status"] == "active":
            created_at = datetime.fromisoformat(suggestion["created_at"])
            closes_at = created_at + timedelta(days=VOTING_DAYS)

            parts.append(
                f"**Stemmen sluit:** "
                f"<t:{int(closes_at.timestamp())}:R>"
            )

        embed = discord.Embed(
            title=f"{emoji} {label} suggestie • {suggestion['title']}",
            description="\n".join(parts),
            color=PURPLE,
        )

        if suggestion["suggestion_type"] == "color":
            embed.set_image(
                url="attachment://kleur-preview.png"
            )

        embed.set_footer(
            text=(
                f"Suggestie #{suggestion['id']} • "
                f"👍 {upvotes} voor • "
                f"👎 {downvotes} tegen"
            )
        )

        return embed

    async def update_suggestion_message(
        self,
        suggestion_id: int,
        disabled: bool,
    ):
        suggestion = await self.get_suggestion(suggestion_id)

        if suggestion is None:
            return

        guild = self.bot.get_guild(suggestion["guild_id"])

        if guild is None:
            return

        channel = guild.get_channel(suggestion["channel_id"])

        if not isinstance(channel, discord.TextChannel):
            return

        if suggestion["message_id"] is None:
            return

        try:
            message = await channel.fetch_message(
                suggestion["message_id"]
            )
        except (discord.NotFound, discord.Forbidden):
            return

        upvotes, downvotes = await self.get_vote_counts(
            suggestion_id
        )

        embed = await self.build_embed(
            suggestion,
            upvotes,
            downvotes,
        )

        view = SuggestionVoteView(
            self,
            suggestion_id,
            upvotes,
            downvotes,
            disabled=disabled,
        )

        try:
            await message.edit(
                embed=embed,
                view=view,
            )
        except (discord.NotFound, discord.Forbidden):
            pass

    async def create_suggestion(
        self,
        interaction: discord.Interaction,
        suggestion_type: str,
        title: str,
        description: str | None = None,
        level: int | None = None,
        color1: str | None = None,
        color2: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        created_at = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                INSERT INTO suggestions (
                    guild_id,
                    channel_id,
                    author_id,
                    suggestion_type,
                    title,
                    description,
                    level,
                    color1,
                    color2,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    interaction.guild.id,
                    interaction.channel.id,
                    interaction.user.id,
                    suggestion_type,
                    title,
                    description,
                    level,
                    color1,
                    color2,
                    created_at,
                ),
            )
            suggestion_id = cursor.lastrowid
            await db.commit()

        suggestion = await self.get_suggestion(suggestion_id)
        embed = await self.build_embed(suggestion, 0, 0)
        view = SuggestionVoteView(self, suggestion_id)

        file = None

        if suggestion_type == "color":
            preview_bytes = make_gradient_png(
                color1,
                color2,
            )

            file = discord.File(
                io.BytesIO(preview_bytes),
                filename="kleur-preview.png",
            )

        try:
            if file is not None:
                message = await interaction.channel.send(
                    embed=embed,
                    file=file,
                    view=view,
                )
            else:
                message = await interaction.channel.send(
                    embed=embed,
                    view=view,
                )
        except Exception:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    DELETE FROM suggestions
                    WHERE id = ?
                    """,
                    (suggestion_id,),
                )
                await db.commit()
            raise

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE suggestions
                SET message_id = ?
                WHERE id = ?
                """,
                (message.id, suggestion_id),
            )
            await db.commit()

        self.bot.add_view(
            view,
            message_id=message.id,
        )

        self.bot.dispatch(
            "suggestion_created",
            interaction.guild,
            interaction.user.id,
            suggestion_id,
        )

        await interaction.followup.send(
            f"Je suggestie is geplaatst: {message.jump_url}",
            ephemeral=True,
        )

    async def handle_vote(
        self,
        interaction: discord.Interaction,
        suggestion_id: int,
        vote: int,
    ):
        if interaction.user.bot:
            await interaction.response.send_message(
                "Bots kunnen niet stemmen.",
                ephemeral=True,
            )
            return

        suggestion = await self.get_suggestion(suggestion_id)

        if suggestion is None or suggestion["status"] != "active":
            await interaction.response.send_message(
                "Deze suggestie is niet meer open voor stemmen.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        vote_counts_for_xp = False

        async with self.db_lock:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    """
                    SELECT vote
                    FROM suggestion_votes
                    WHERE suggestion_id = ?
                      AND user_id = ?
                    """,
                    (
                        suggestion_id,
                        interaction.user.id,
                    ),
                )
                existing = await cursor.fetchone()

                if existing and existing[0] == vote:
                    await db.execute(
                        """
                        DELETE FROM suggestion_votes
                        WHERE suggestion_id = ?
                          AND user_id = ?
                        """,
                        (
                            suggestion_id,
                            interaction.user.id,
                        ),
                    )
                    result = "Je stem is verwijderd."
                else:
                    vote_counts_for_xp = True
                    await db.execute(
                        """
                        INSERT INTO suggestion_votes (
                            suggestion_id,
                            user_id,
                            vote
                        )
                        VALUES (?, ?, ?)
                        ON CONFLICT(suggestion_id, user_id)
                        DO UPDATE SET vote = excluded.vote
                        """,
                        (
                            suggestion_id,
                            interaction.user.id,
                            vote,
                        ),
                    )

                    result = (
                        "Je stem is aangepast."
                        if existing
                        else "Je stem is toegevoegd."
                    )

                await db.commit()

            upvotes, downvotes = await self.get_vote_counts(
                suggestion_id
            )

        suggestion = await self.get_suggestion(suggestion_id)
        embed = await self.build_embed(
            suggestion,
            upvotes,
            downvotes,
        )

        view = SuggestionVoteView(
            self,
            suggestion_id,
            upvotes,
            downvotes,
        )

        if interaction.message is not None:
            await interaction.message.edit(
                embed=embed,
                view=view,
            )

        if vote_counts_for_xp:
            self.bot.dispatch(
                "suggestion_vote",
                interaction.guild,
                interaction.user.id,
                suggestion_id,
            )

        await interaction.followup.send(
            result,
            ephemeral=True,
        )

    async def handle_cancel(
        self,
        interaction: discord.Interaction,
        suggestion_id: int,
    ):
        suggestion = await self.get_suggestion(suggestion_id)

        if suggestion is None:
            await interaction.response.send_message(
                "Deze suggestie bestaat niet meer.",
                ephemeral=True,
            )
            return

        if interaction.user.id != suggestion["author_id"]:
            await interaction.response.send_message(
                "Alleen de maker van deze suggestie kan hem annuleren.",
                ephemeral=True,
            )
            return

        if suggestion["status"] != "active":
            await interaction.response.send_message(
                "Deze suggestie kan niet meer geannuleerd worden.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE suggestions
                SET status = 'cancelled'
                WHERE id = ?
                  AND status = 'active'
                """,
                (suggestion_id,),
            )
            await db.commit()

        await self.update_suggestion_message(
            suggestion_id,
            disabled=True,
        )

        await interaction.followup.send(
            "Je suggestie is geannuleerd.",
            ephemeral=True,
        )

    @tasks.loop(minutes=5)
    async def expire_suggestions(self):
        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(days=VOTING_DAYS)
        ).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT id
                FROM suggestions
                WHERE status = 'active'
                  AND created_at <= ?
                """,
                (cutoff,),
            )
            rows = await cursor.fetchall()

            if not rows:
                return

            ids = [row[0] for row in rows]

            await db.executemany(
                """
                UPDATE suggestions
                SET status = 'pending_review'
                WHERE id = ?
                  AND status = 'active'
                """,
                [(suggestion_id,) for suggestion_id in ids],
            )

            await db.commit()

        for suggestion_id in ids:
            await self.update_suggestion_message(
                suggestion_id,
                disabled=True,
            )

    @expire_suggestions.before_loop
    async def before_expire_suggestions(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ):
        if message.author.bot or message.guild is None:
            return

        channel_id = await self.get_suggestion_channel_id(
            message.guild.id
        )

        if channel_id is None or message.channel.id != channel_id:
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

    @suggestie.command(
        name="setup",
        description="Stel het suggestiekanaal in.",
    )
    @app_commands.describe(
        channel="Kanaal waarin suggesties geplaatst worden.",
    )
    async def setup_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Dit command kan alleen in een server gebruikt worden.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Je serverlid kon niet worden gevonden.",
                ephemeral=True,
            )
            return

        if not has_management_access(interaction.user):
            await interaction.response.send_message(
                "Alleen een admin of owner kan dit instellen.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        bot_member = interaction.guild.me

        if bot_member is None:
            await interaction.followup.send(
                "Ik kon mijn eigen botlid niet vinden.",
                ephemeral=True,
            )
            return

        try:
            await channel.set_permissions(
                interaction.guild.default_role,
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                send_messages_in_threads=False,
                create_public_threads=False,
                create_private_threads=False,
                add_reactions=False,
                use_application_commands=True,
                reason="LEVENLOOS suggestiekanaal setup",
            )

            await channel.set_permissions(
                bot_member,
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                use_application_commands=True,
                reason="LEVENLOOS suggestiekanaal setup",
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "Ik heb niet genoeg rechten om de "
                "kanaalpermissies aan te passen.",
                ephemeral=True,
            )
            return

        await self.set_suggestion_channel(
            interaction.guild.id,
            channel.id,
        )

        await interaction.followup.send(
            f"{channel.mention} is nu het suggestiekanaal.\n\n"
            "Leden kunnen `/suggestie` commands gebruiken en stemmen. "
            "Normale berichten worden automatisch verwijderd.",
            ephemeral=True,
        )

    @suggestie.command(
        name="kleur",
        description="Stel een nieuwe naamkleur of gradient voor.",
    )
    @app_commands.describe(
        naam="Naam van de voorgestelde kleur.",
        level="Levelcategorie waarin de kleur moet komen.",
        kleur1="Eerste kleur, bijvoorbeeld #24124F.",
        kleur2="Tweede kleur voor een gradient. Leeg = solid.",
    )
    @app_commands.choices(level=LEVEL_CHOICES)
    async def color_command(
        self,
        interaction: discord.Interaction,
        naam: app_commands.Range[str, 2, 32],
        level: app_commands.Choice[int],
        kleur1: str,
        kleur2: str | None = None,
    ):
        if not await self.check_command_channel(interaction):
            return

        if not await self.check_cooldown(interaction):
            return

        try:
            color1 = normalize_hex(kleur1)
            color2 = normalize_hex(kleur2) if kleur2 else None
        except ValueError:
            await interaction.response.send_message(
                "Gebruik een geldige hexkleur, bijvoorbeeld `#6C27DA`.",
                ephemeral=True,
            )
            return

        clean_name = " ".join(naam.split()).strip()

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT id
                FROM suggestions
                WHERE guild_id = ?
                  AND suggestion_type = 'color'
                  AND LOWER(title) = LOWER(?)
                  AND status = 'active'
                LIMIT 1
                """,
                (
                    interaction.guild.id,
                    clean_name,
                ),
            )
            duplicate = await cursor.fetchone()

        if duplicate:
            await interaction.response.send_message(
                "Er staat al een actieve kleursuggestie met die naam.",
                ephemeral=True,
            )
            return

        await self.create_suggestion(
            interaction,
            suggestion_type="color",
            title=clean_name,
            level=level.value,
            color1=color1,
            color2=color2,
        )

    @suggestie.command(
        name="algemeen",
        description="Doe een algemene suggestie voor de server.",
    )
    @app_commands.describe(
        titel="Korte titel voor je suggestie.",
        beschrijving="Leg je suggestie uit.",
    )
    async def general_command(
        self,
        interaction: discord.Interaction,
        titel: app_commands.Range[str, 2, 60],
        beschrijving: app_commands.Range[str, 5, 1000],
    ):
        if not await self.check_command_channel(interaction):
            return

        if not await self.check_cooldown(interaction):
            return

        await self.create_suggestion(
            interaction,
            suggestion_type="general",
            title=" ".join(titel.split()).strip(),
            description=beschrijving.strip(),
        )

    @suggestie.command(
        name="bot",
        description="Doe een suggestie voor de LEVENLOOS bot.",
    )
    @app_commands.describe(
        titel="Korte titel voor je bot-suggestie.",
        beschrijving="Leg uit wat de bot moet kunnen of veranderen.",
    )
    async def bot_command(
        self,
        interaction: discord.Interaction,
        titel: app_commands.Range[str, 2, 60],
        beschrijving: app_commands.Range[str, 5, 1000],
    ):
        if not await self.check_command_channel(interaction):
            return

        if not await self.check_cooldown(interaction):
            return

        await self.create_suggestion(
            interaction,
            suggestion_type="bot",
            title=" ".join(titel.split()).strip(),
            description=beschrijving.strip(),
        )

    @suggestie.command(
        name="event",
        description="Stel een event of activiteit voor.",
    )
    @app_commands.describe(
        titel="Naam of korte titel van het event.",
        beschrijving="Leg het eventidee uit.",
    )
    async def event_command(
        self,
        interaction: discord.Interaction,
        titel: app_commands.Range[str, 2, 60],
        beschrijving: app_commands.Range[str, 5, 1000],
    ):
        if not await self.check_command_channel(interaction):
            return

        if not await self.check_cooldown(interaction):
            return

        await self.create_suggestion(
            interaction,
            suggestion_type="event",
            title=" ".join(titel.split()).strip(),
            description=beschrijving.strip(),
        )

    @suggestie.command(
        name="beheer",
        description="Beheer een bestaande suggestie.",
    )
    @app_commands.describe(
        id="Suggestie-ID uit de footer van de suggestie.",
        actie="Wat je met de suggestie wilt doen.",
    )
    @app_commands.choices(actie=MANAGE_ACTION_CHOICES)
    async def manage_command(
        self,
        interaction: discord.Interaction,
        id: app_commands.Range[int, 1],
        actie: app_commands.Choice[str],
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Dit command kan alleen in een server gebruikt worden.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Je serverlid kon niet worden gevonden.",
                ephemeral=True,
            )
            return

        if not has_management_access(interaction.user):
            await interaction.response.send_message(
                "Alleen een admin of owner kan suggesties beheren.",
                ephemeral=True,
            )
            return

        suggestion = await self.get_suggestion(id)

        if (
            suggestion is None
            or suggestion["guild_id"] != interaction.guild.id
        ):
            await interaction.response.send_message(
                "Ik kan die suggestie niet vinden.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        channel = interaction.guild.get_channel(
            suggestion["channel_id"]
        )

        message = None

        if (
            isinstance(channel, discord.TextChannel)
            and suggestion["message_id"] is not None
        ):
            try:
                message = await channel.fetch_message(
                    suggestion["message_id"]
                )
            except (discord.NotFound, discord.Forbidden):
                message = None

        if actie.value == "delete":
            if message is not None:
                try:
                    await message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    """
                    DELETE FROM suggestion_votes
                    WHERE suggestion_id = ?
                    """,
                    (id,),
                )
                await db.execute(
                    """
                    DELETE FROM suggestions
                    WHERE id = ?
                    """,
                    (id,),
                )
                await db.commit()

            await interaction.followup.send(
                f"Suggestie **#{id}** is verwijderd.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE suggestions
                SET status = ?
                WHERE id = ?
                """,
                (actie.value, id),
            )
            await db.commit()

        if actie.value == "accepted":
            self.bot.dispatch(
                "suggestion_accepted",
                interaction.guild,
                suggestion["author_id"],
                id,
            )

        suggestion = await self.get_suggestion(id)
        upvotes, downvotes = await self.get_vote_counts(id)

        embed = await self.build_embed(
            suggestion,
            upvotes,
            downvotes,
        )

        view = SuggestionVoteView(
            self,
            id,
            upvotes,
            downvotes,
            disabled=(actie.value != "active"),
        )

        if message is not None:
            await message.edit(
                embed=embed,
                view=view,
            )

        action_name = {
            "accepted": "geaccepteerd",
            "rejected": "afgewezen",
            "closed": "gesloten",
            "active": "heropend",
        }[actie.value]

        await interaction.followup.send(
            f"Suggestie **#{id}** is {action_name}.",
            ephemeral=True,
        )


    @suggestie.command(
        name="refresh",
        description="Werk alle bestaande suggestieberichten opnieuw bij.",
    )
    async def refresh_command(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Dit command kan alleen in een server gebruikt worden.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Je serverlid kon niet worden gevonden.",
                ephemeral=True,
            )
            return

        if not has_management_access(interaction.user):
            await interaction.response.send_message(
                "Alleen een admin of owner kan suggesties vernieuwen.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc)
        cutoff = (
            now
            - timedelta(days=VOTING_DAYS)
        ).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                SELECT id
                FROM suggestions
                WHERE guild_id = ?
                ORDER BY id ASC
                """,
                (interaction.guild.id,),
            )
            rows = await cursor.fetchall()

            await db.execute(
                """
                UPDATE suggestions
                SET status = 'pending_review'
                WHERE guild_id = ?
                  AND status = 'active'
                  AND created_at <= ?
                """,
                (
                    interaction.guild.id,
                    cutoff,
                ),
            )

            await db.commit()

        updated = 0
        missing = 0
        failed = 0

        for (suggestion_id,) in rows:
            suggestion = await self.get_suggestion(
                suggestion_id
            )

            if suggestion is None:
                continue

            channel = interaction.guild.get_channel(
                suggestion["channel_id"]
            )

            if (
                not isinstance(
                    channel,
                    discord.TextChannel,
                )
                or suggestion["message_id"] is None
            ):
                missing += 1
                continue

            try:
                message = await channel.fetch_message(
                    suggestion["message_id"]
                )
            except (
                discord.NotFound,
                discord.Forbidden,
            ):
                missing += 1
                continue
            except discord.HTTPException:
                failed += 1
                continue

            upvotes, downvotes = (
                await self.get_vote_counts(
                    suggestion_id
                )
            )

            embed = await self.build_embed(
                suggestion,
                upvotes,
                downvotes,
            )

            disabled = (
                suggestion["status"]
                != "active"
            )

            view = SuggestionVoteView(
                self,
                suggestion_id,
                upvotes,
                downvotes,
                disabled=disabled,
            )

            try:
                await message.edit(
                    embed=embed,
                    view=view,
                )
                updated += 1
            except discord.HTTPException:
                failed += 1

            await asyncio.sleep(0.35)

        await interaction.followup.send(
            (
                f"Suggesties vernieuwd.\n"
                f"✅ Bijgewerkt: **{updated}**\n"
                f"⚠️ Bericht niet gevonden: **{missing}**\n"
                f"❌ Mislukt: **{failed}**"
            ),
            ephemeral=True,
        )

    @suggestie.command(
        name="info",
        description="Bekijk informatie over het suggestiesysteem.",
    )
    async def info_command(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Dit command kan alleen in een server gebruikt worden.",
                ephemeral=True,
            )
            return

        channel_id = await self.get_suggestion_channel_id(
            interaction.guild.id
        )

        channel_text = (
            f"<#{channel_id}>"
            if channel_id
            else "Nog niet ingesteld"
        )

        embed = discord.Embed(
            title="💡 Suggesties",
            description=(
                f"**Suggestiekanaal:** {channel_text}\n\n"
                "**Commands**\n"
                "`/suggestie kleur` — kleur of gradient\n"
                "`/suggestie algemeen` — algemene serverideeën\n"
                "`/suggestie bot` — ideeën voor de bot\n"
                "`/suggestie event` — events en activiteiten\n\n"
                f"Suggesties staan **{VOTING_DAYS} dagen** open voor stemmen. "
                "Daarna worden ze automatisch **Wacht op beoordeling**.\n"
                "De maker kan een open suggestie zelf annuleren met de "
                "**Annuleren**-knop.\n\n"
                "Iedere suggestie krijgt automatisch "
                "👍 Voor en 👎 Tegen stemknoppen."
            ),
            color=PURPLE,
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Suggestions(bot))
