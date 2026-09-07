import asyncio
import difflib
import re
import time
import unicodedata

import aiosqlite
import discord

from discord import app_commands
from discord.ext import commands


# =========================================================
# CONFIG
# =========================================================

DATABASE_PATH = "levenloos.db"


ACTIVE_GAME_CATEGORY_ID = 940987098427707412

GAME_PROPOSAL_CHANNEL_ID = 940336215331319858

ARCHIVE_CATEGORY_ID = 1545088593716973568

UNARCHIVE_CHANNEL_ID = 1545089501745913916

OWNER_DM_USER_ID = 267246682947715087


ACTIVE_ROLE_COLOR = discord.Color.from_str(
    "#FFAE00"
)

ARCHIVE_ROLE_COLOR = discord.Color.from_str(
    "#808080"
)


GAME_VOTE_REQUIRED = 3

GAME_VOTE_DURATION = 24 * 60 * 60

GAME_VOTE_START_COOLDOWN = 2 * 60 * 60


UNARCHIVE_VOTE_REQUIRED = 3

UNARCHIVE_VOTE_DURATION = 24 * 60 * 60


TEMP_ROLE_BUTTON_DURATION = 24 * 60 * 60


# =========================================================
# DATABASE
# =========================================================

async def initialise_database():

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS game_button_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                creator_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS game_button_support (
                vote_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                UNIQUE(vote_id, user_id)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS game_vote_state (
                guild_id INTEGER PRIMARY KEY,
                last_vote_started_at INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS temp_role_buttons_v2 (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS unarchive_button_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                creator_id INTEGER NOT NULL,
                game_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS unarchive_button_support (
                vote_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,

                UNIQUE(vote_id, user_id)
            )
            """
        )

        await db.commit()

    print("[DB] Game database klaar")


# =========================================================
# NAME HELPERS
# =========================================================

def clean_game_name(
    name: str
) -> str:

    name = name.strip()

    name = re.sub(
        r"^[◆▏│┃|\s]+",
        "",
        name
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    )

    return name.strip().title()


def normalize_game_name(
    name: str
) -> str:

    name = clean_game_name(
        name
    )

    name = unicodedata.normalize(
        "NFKD",
        name
    )

    name = "".join(
        character
        for character in name
        if not unicodedata.combining(
            character
        )
    )

    name = name.lower()

    name = re.sub(
        r"[^a-z0-9]",
        "",
        name
    )

    return name


def channel_slug(
    game_name: str
) -> str:

    text = unicodedata.normalize(
        "NFKD",
        game_name
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(
            character
        )
    )

    text = text.lower()

    text = re.sub(
        r"[^a-z0-9]+",
        "-",
        text
    )

    text = text.strip("-")

    return f"▏{text}"


def role_name(
    game_name: str
) -> str:

    return f"◆ {game_name}"


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

    names = {
        role.name.lower()
        for role in member.roles
    }

    if "admin" in names:
        return True

    if "owner" in names:
        return True

    return False


# =========================================================
# GAME SEARCH
# =========================================================

def find_game_role(
    guild: discord.Guild,
    game_name: str
):

    wanted = normalize_game_name(
        game_name
    )

    for role in guild.roles:

        if not role.name.startswith(
            "◆"
        ):
            continue

        existing = normalize_game_name(
            role.name
        )

        if existing == wanted:
            return role

    return None


def find_active_game_channel(
    guild: discord.Guild,
    game_name: str
):

    category = guild.get_channel(
        ACTIVE_GAME_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel
    ):
        return None

    wanted = normalize_game_name(
        game_name
    )

    for channel in category.text_channels:

        existing = normalize_game_name(
            channel.name
        )

        if existing == wanted:
            return channel

    return None


def find_archived_game_channel(
    guild: discord.Guild,
    game_name: str
):

    category = guild.get_channel(
        ARCHIVE_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel
    ):
        return None

    wanted = normalize_game_name(
        game_name
    )

    for channel in category.text_channels:

        existing = normalize_game_name(
            channel.name
        )

        if existing == wanted:
            return channel

    return None


def all_game_names(
    guild: discord.Guild
):

    result = []

    for role in guild.roles:

        if not role.name.startswith(
            "◆"
        ):
            continue

        result.append(
            clean_game_name(
                role.name
            )
        )

    return result


def find_near_match(
    guild: discord.Guild,
    game_name: str
):

    wanted = normalize_game_name(
        game_name
    )

    for existing_name in all_game_names(
        guild
    ):

        existing = normalize_game_name(
            existing_name
        )

        similarity = difflib.SequenceMatcher(
            None,
            wanted,
            existing
        ).ratio()

        if similarity >= 0.82:
            return existing_name

    return None


# =========================================================
# SORTING
# =========================================================

async def sort_category(
    category: discord.CategoryChannel,
    pinned_channel_id: int
):

    channels = list(
        category.text_channels
    )

    pinned = None

    others = []

    for channel in channels:

        if channel.id == pinned_channel_id:
            pinned = channel
        else:
            others.append(
                channel
            )

    others.sort(
        key=lambda channel:
        normalize_game_name(
            channel.name
        )
    )

    target = []

    if pinned:
        target.append(
            pinned
        )

    target.extend(
        others
    )

    try:

        for wanted_position, channel in enumerate(
            target
        ):

            if channel.position == wanted_position:
                continue

            await channel.edit(
                position=wanted_position,
                reason="Gamekanalen alfabetisch sorteren"
            )

            await asyncio.sleep(
                0.65
            )

    except asyncio.TimeoutError:
        print(
            "[SORT] Sorteren timeout"
        )

    except discord.HTTPException as exc:
        print(
            f"[SORT] Discord fout: {exc}"
        )


async def sort_active_games(
    guild: discord.Guild
):

    category = guild.get_channel(
        ACTIVE_GAME_CATEGORY_ID
    )

    if isinstance(
        category,
        discord.CategoryChannel
    ):

        try:

            await asyncio.wait_for(
                sort_category(
                    category,
                    GAME_PROPOSAL_CHANNEL_ID
                ),
                timeout=30
            )

        except asyncio.TimeoutError:
            pass


async def sort_archived_games(
    guild: discord.Guild
):

    category = guild.get_channel(
        ARCHIVE_CATEGORY_ID
    )

    if isinstance(
        category,
        discord.CategoryChannel
    ):

        try:

            await asyncio.wait_for(
                sort_category(
                    category,
                    UNARCHIVE_CHANNEL_ID
                ),
                timeout=30
            )

        except asyncio.TimeoutError:
            pass


# =========================================================
# ROLE SELECTOR FUTURE HOOK
# =========================================================

def notify_role_system(
    bot: commands.Bot,
    guild: discord.Guild,
    game_name: str,
    action: str
):

    """
    roles.py kan hier later naar luisteren met:

    @commands.Cog.listener()
    async def on_game_catalog_changed(
        self,
        guild,
        game_name,
        action
    ):
        ...

    Mogelijke actions:
    created
    archived
    unarchived
    removed
    """

    bot.dispatch(
        "game_catalog_changed",
        guild,
        game_name,
        action
    )


# =========================================================
# CREATE GAME
# =========================================================

async def create_game(
    bot: commands.Bot,
    guild: discord.Guild,
    game_name: str
):

    game_name = clean_game_name(
        game_name
    )

    role = find_game_role(
        guild,
        game_name
    )

    if role is None:

        role = await guild.create_role(
            name=role_name(
                game_name
            ),
            color=ACTIVE_ROLE_COLOR,
            reason=f"Game aangemaakt: {game_name}"
        )

    else:

        try:

            await role.edit(
                name=role_name(
                    game_name
                ),
                color=ACTIVE_ROLE_COLOR
            )

        except discord.HTTPException:
            pass

    category = guild.get_channel(
        ACTIVE_GAME_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel
    ):

        raise RuntimeError(
            "Active game category niet gevonden."
        )

    channel = find_active_game_channel(
        guild,
        game_name
    )

    if channel is None:

        archived = find_archived_game_channel(
            guild,
            game_name
        )

        if archived:

            channel = archived

            await channel.edit(
                category=category
            )

        else:

            overwrites = {
                guild.default_role:
                    discord.PermissionOverwrite(
                        view_channel=False
                    ),

                role:
                    discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True
                    )
            }

            channel = await guild.create_text_channel(
                channel_slug(
                    game_name
                ),
                category=category,
                overwrites=overwrites,
                topic=(
                    f"🎮 Gamekanaal voor {game_name} • "
                    "Praat hier over de game, deel "
                    "tips/screenshots en speel samen."
                ),
                reason=f"Game aangemaakt: {game_name}"
            )

    await channel.edit(
        name=channel_slug(
            game_name
        ),
        topic=(
            f"🎮 Gamekanaal voor {game_name} • "
            "Praat hier over de game, deel "
            "tips/screenshots en speel samen."
        ),
        overwrites={
            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=False
                ),

            role:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
        }
    )

    await sort_active_games(
        guild
    )

    notify_role_system(
        bot,
        guild,
        game_name,
        "created"
    )

    return role, channel


# =========================================================
# ARCHIVE
# =========================================================

async def archive_game(
    bot: commands.Bot,
    guild: discord.Guild,
    game_name: str
):

    game_name = clean_game_name(
        game_name
    )

    channel = find_active_game_channel(
        guild,
        game_name
    )

    if channel is None:
        return None

    category = guild.get_channel(
        ARCHIVE_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel
    ):
        raise RuntimeError(
            "Archive category niet gevonden."
        )

    role = find_game_role(
        guild,
        game_name
    )

    await channel.edit(
        category=category,
        topic=(
            f"📦 Gearchiveerd gamekanaal voor "
            f"{game_name} • Dit kanaal is alleen-lezen."
        ),
        overwrites={
            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    add_reactions=False,
                    create_public_threads=False,
                    create_private_threads=False,
                    send_messages_in_threads=False,
                    read_message_history=True
                )
        }
    )

    if role:

        try:

            await role.edit(
                color=ARCHIVE_ROLE_COLOR
            )

        except discord.HTTPException:
            pass

    await sort_archived_games(
        guild
    )

    notify_role_system(
        bot,
        guild,
        game_name,
        "archived"
    )

    return channel


# =========================================================
# UNARCHIVE
# =========================================================

async def unarchive_game_now(
    bot: commands.Bot,
    guild: discord.Guild,
    game_name: str
):

    game_name = clean_game_name(
        game_name
    )

    channel = find_archived_game_channel(
        guild,
        game_name
    )

    if channel is None:
        return None

    category = guild.get_channel(
        ACTIVE_GAME_CATEGORY_ID
    )

    if not isinstance(
        category,
        discord.CategoryChannel
    ):
        raise RuntimeError(
            "Active category niet gevonden."
        )

    role = find_game_role(
        guild,
        game_name
    )

    if role is None:

        role = await guild.create_role(
            name=role_name(
                game_name
            ),
            color=ACTIVE_ROLE_COLOR
        )

    else:

        try:

            await role.edit(
                color=ACTIVE_ROLE_COLOR
            )

        except discord.HTTPException:
            pass

    await channel.edit(
        category=category,
        name=channel_slug(
            game_name
        ),
        topic=(
            f"🎮 Gamekanaal voor {game_name} • "
            "Praat hier over de game, deel "
            "tips/screenshots en speel samen."
        ),
        overwrites={
            guild.default_role:
                discord.PermissionOverwrite(
                    view_channel=False
                ),

            role:
                discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True
                )
        }
    )

    await sort_active_games(
        guild
    )

    notify_role_system(
        bot,
        guild,
        game_name,
        "unarchived"
    )

    return channel


# =========================================================
# OWNER DM
# =========================================================

async def notify_owner_new_game(
    bot: commands.Bot,
    guild: discord.Guild,
    game_name: str,
    role: discord.Role,
    game_channel: discord.TextChannel
):

    try:

        owner = bot.get_user(
            OWNER_DM_USER_ID
        )

        if owner is None:

            owner = await bot.fetch_user(
                OWNER_DM_USER_ID
            )

        await owner.send(
            f"🎮 **Nieuwe game toegevoegd: "
            f"{game_name}**\n\n"
            f"Server: **{guild.name}**\n"
            f"Role: **{role.name}**\n"
            f"Channel: {game_channel.mention}\n\n"
            f"De tijdelijke **Pak {role.name}** "
            "button staat nu 24 uur open.\n\n"
            "Maak later de custom emoji / "
            "permanente game-role."
        )

        print(
            f"[DM] Owner notified voor {game_name}"
        )

    except discord.Forbidden:

        print(
            "[DM] Owner accepteert geen DM's."
        )

    except discord.HTTPException as exc:

        print(
            f"[DM] Owner DM mislukt: {exc}"
        )


# =========================================================
# TEMPORARY ROLE BUTTON
# =========================================================

class TempRoleView(
    discord.ui.View
):

    def __init__(
        self,
        role_id: int,
        game_name: str
    ):

        super().__init__(
            timeout=None
        )

        self.role_id = role_id

        self.game_name = game_name

        button = discord.ui.Button(
            label=f"Pak ◆ {game_name}",
            style=discord.ButtonStyle.success,
            custom_id=f"temp_game_role:{role_id}"
        )

        button.callback = self.claim_role

        self.add_item(
            button
        )

    async def claim_role(
        self,
        interaction: discord.Interaction
    ):

        if not interaction.guild:

            await interaction.response.send_message(
                "❌ Dit werkt alleen op een server.",
                ephemeral=True
            )

            return

        role = interaction.guild.get_role(
            self.role_id
        )

        if role is None:

            await interaction.response.send_message(
                "❌ Deze role bestaat niet meer.",
                ephemeral=True
            )

            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):

            return

        if role in member.roles:

            await interaction.response.send_message(
                f"✅ Je hebt **{role.name}** al.",
                ephemeral=True
            )

            return

        try:

            await member.add_roles(
                role,
                reason="Tijdelijke game role button"
            )

            await interaction.response.send_message(
                f"✅ Je hebt **{role.name}** gekregen.",
                ephemeral=True
            )

        except discord.Forbidden:

            await interaction.response.send_message(
                "❌ Ik kan deze role niet geven. "
                "Controleer mijn role-positie.",
                ephemeral=True
            )


async def create_temporary_role_button(
    bot: commands.Bot,
    proposal_channel: discord.TextChannel,
    game_name: str,
    role: discord.Role
):

    expires_at = int(
        time.time()
    ) + TEMP_ROLE_BUTTON_DURATION

    view = TempRoleView(
        role.id,
        game_name
    )

    message = await proposal_channel.send(
        f"🎮 **{game_name} is toegevoegd!**\n\n"
        f"Wil je toegang tot het gamekanaal?\n"
        f"Pak hieronder tijdelijk **{role.name}**.\n\n"
        "⏱️ Deze button blijft **24 uur** beschikbaar.",
        view=view
    )

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        await db.execute(
            """
            INSERT OR REPLACE INTO temp_role_buttons_v2 (
                message_id,
                guild_id,
                channel_id,
                role_id,
                game_name,
                expires_at,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                message.id,
                proposal_channel.guild.id,
                proposal_channel.id,
                role.id,
                game_name,
                expires_at
            )
        )

        await db.commit()

    bot.add_view(
        view,
        message_id=message.id
    )

    asyncio.create_task(
        expire_temporary_role_button(
            bot,
            message.id,
            proposal_channel.guild.id,
            proposal_channel.id,
            role.id,
            game_name,
            expires_at
        )
    )

    return message


async def expire_temporary_role_button(
    bot,
    message_id,
    guild_id,
    channel_id,
    role_id,
    game_name,
    expires_at
):

    delay = max(
        0,
        expires_at - int(
            time.time()
        )
    )

    await asyncio.sleep(
        delay
    )

    guild = bot.get_guild(
        guild_id
    )

    if guild:

        channel = guild.get_channel(
            channel_id
        )

        if isinstance(
            channel,
            discord.TextChannel
        ):

            try:

                message = await channel.fetch_message(
                    message_id
                )

                disabled_view = discord.ui.View(
                    timeout=None
                )

                button = discord.ui.Button(
                    label=f"Pak ◆ {game_name}",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                    custom_id=f"expired_game_role:{role_id}:{message_id}"
                )

                disabled_view.add_item(
                    button
                )

                await message.edit(
                    view=disabled_view
                )

            except discord.HTTPException:
                pass

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        await db.execute(
            """
            UPDATE temp_role_buttons_v2
            SET status = 'expired'
            WHERE message_id = ?
            """,
            (
                message_id,
            )
        )

        await db.commit()


# =========================================================
# GAME VOTE VIEW
# =========================================================

class GameVoteView(
    discord.ui.View
):

    def __init__(
        self,
        cog,
        vote_id: int
    ):

        super().__init__(
            timeout=None
        )

        self.cog = cog

        self.vote_id = vote_id

        button = discord.ui.Button(
            label="Steun voorstel",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"game_vote_support:{vote_id}"
        )

        button.callback = self.support_vote

        self.add_item(
            button
        )

    async def support_vote(
        self,
        interaction: discord.Interaction
    ):

        await self.cog.handle_game_vote_support(
            interaction,
            self.vote_id
        )


# =========================================================
# UNARCHIVE VOTE VIEW
# =========================================================

class UnarchiveVoteView(
    discord.ui.View
):

    def __init__(
        self,
        cog,
        vote_id: int
    ):

        super().__init__(
            timeout=None
        )

        self.cog = cog

        self.vote_id = vote_id

        button = discord.ui.Button(
            label="Steun terugzetten",
            emoji="📦",
            style=discord.ButtonStyle.primary,
            custom_id=f"unarchive_vote_support:{vote_id}"
        )

        button.callback = self.support_vote

        self.add_item(
            button
        )

    async def support_vote(
        self,
        interaction
    ):

        await self.cog.handle_unarchive_support(
            interaction,
            self.vote_id
        )


# =========================================================
# GAME COG
# =========================================================

class Games(
    commands.Cog
):

    def __init__(
        self,
        bot: commands.Bot
    ):

        self.bot = bot

        self.game_vote_lock = asyncio.Lock()

    async def cog_load(
        self
    ):

        await initialise_database()

        await self.restore_game_votes()

        await self.restore_temp_buttons()

        await self.restore_unarchive_votes()

    # =====================================================
    # /GAME GROUP
    # =====================================================

    game = app_commands.Group(
        name="game",
        description="Beheer gamekanalen"
    )

    # =====================================================
    # SETUP
    # =====================================================

    @game.command(
        name="setup",
        description="Maak een gamekanaal aan"
    )
    async def setup_game(
        self,
        interaction: discord.Interaction,
        game_name: str
    ):

        if not interaction.guild:

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

        game_name = clean_game_name(
            game_name
        )

        if (
            find_active_game_channel(
                interaction.guild,
                game_name
            )
            or find_archived_game_channel(
                interaction.guild,
                game_name
            )
        ):

            await interaction.response.send_message(
                f"❌ **{game_name}** bestaat al.",
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        try:

            role, channel = await create_game(
                self.bot,
                interaction.guild,
                game_name
            )

            await interaction.followup.send(
                f"✅ **{game_name}** aangemaakt.\n"
                f"Role: **{role.name}**\n"
                f"Channel: {channel.mention}",
                ephemeral=True
            )

        except Exception as exc:

            print(
                f"[SETUP] {exc}"
            )

            await interaction.followup.send(
                f"❌ Game kon niet worden aangemaakt:\n"
                f"`{exc}`",
                ephemeral=True
            )

    # =====================================================
    # ARCHIVE
    # =====================================================

    @game.command(
        name="archive",
        description="Archiveer een gamekanaal"
    )
    async def archive_command(
        self,
        interaction: discord.Interaction,
        game_name: str
    ):

        if not interaction.guild:
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
                "❌ Geen toegang.",
                ephemeral=True
            )

            return

        game_name = clean_game_name(
            game_name
        )

        await interaction.response.defer(
            ephemeral=True
        )

        channel = await archive_game(
            self.bot,
            interaction.guild,
            game_name
        )

        if channel is None:

            await interaction.followup.send(
                f"❌ Actieve game **{game_name}** "
                "niet gevonden.",
                ephemeral=True
            )

            return

        await interaction.followup.send(
            f"📦 **{game_name}** is gearchiveerd.",
            ephemeral=True
        )

    # =====================================================
    # REMOVE
    # =====================================================

    @game.command(
        name="remove",
        description="Verwijder een game volledig"
    )
    async def remove_command(
        self,
        interaction: discord.Interaction,
        game_name: str
    ):

        if not interaction.guild:
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
                "❌ Geen toegang.",
                ephemeral=True
            )

            return

        game_name = clean_game_name(
            game_name
        )

        view = RemoveConfirmView(
            self,
            interaction.user.id,
            game_name
        )

        await interaction.response.send_message(
            f"⚠️ Weet je zeker dat je "
            f"**{game_name}** volledig wilt verwijderen?",
            view=view,
            ephemeral=True
        )

    async def remove_game_now(
        self,
        guild,
        game_name
    ):

        active = find_active_game_channel(
            guild,
            game_name
        )

        archived = find_archived_game_channel(
            guild,
            game_name
        )

        role = find_game_role(
            guild,
            game_name
        )

        if active:

            await active.delete(
                reason=f"Game verwijderd: {game_name}"
            )

        if archived:

            await archived.delete(
                reason=f"Game verwijderd: {game_name}"
            )

        if role:

            try:

                await role.delete(
                    reason=f"Game verwijderd: {game_name}"
                )

            except discord.Forbidden:
                pass

        await sort_active_games(
            guild
        )

        await sort_archived_games(
            guild
        )

        notify_role_system(
            self.bot,
            guild,
            game_name,
            "removed"
        )

    # =====================================================
    # GAME VOTE
    # =====================================================

    @game.command(
        name="vote",
        description="Start een vote voor een nieuwe game"
    )
    async def game_vote(
        self,
        interaction: discord.Interaction,
        game_name: str
    ):

        if not interaction.guild:

            return

        if interaction.channel_id != GAME_PROPOSAL_CHANNEL_ID:

            await interaction.response.send_message(
                "❌ /game vote kan alleen gebruikt worden "
                "in #Games-voorstellen.",
                ephemeral=True
            )

            return

        game_name = clean_game_name(
            game_name
        )

        normalized = normalize_game_name(
            game_name
        )

        guild = interaction.guild

        async with self.game_vote_lock:

            # ---------------------------------------------
            # AL BESTAAT
            # ---------------------------------------------

            if (
                find_active_game_channel(
                    guild,
                    game_name
                )
                or
                find_archived_game_channel(
                    guild,
                    game_name
                )
            ):

                await interaction.response.send_message(
                    f"❌ **{game_name}** bestaat al.",
                    ephemeral=True
                )

                return

            near = find_near_match(
                guild,
                game_name
            )

            if near:

                await interaction.response.send_message(
                    f"⚠️ **{game_name}** lijkt erg op "
                    f"een bestaande game: **{near}**.\n"
                    "Gebruik de bestaande naam of vraag "
                    "een admin om dit te controleren.",
                    ephemeral=True
                )

                return

            now = int(
                time.time()
            )

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                # -----------------------------------------
                # MAX 1 ACTIEVE VOTE PER SERVER
                # -----------------------------------------

                cursor = await db.execute(
                    """
                    SELECT id, game_name
                    FROM game_button_votes
                    WHERE guild_id = ?
                    AND status = 'active'
                    AND expires_at > ?
                    LIMIT 1
                    """,
                    (
                        guild.id,
                        now
                    )
                )

                active_vote = await cursor.fetchone()

                if active_vote:

                    await interaction.response.send_message(
                        f"❌ Er loopt al een actieve "
                        f"game-vote voor "
                        f"**{active_vote[1]}**.\n"
                        "Er kan maar **1 game-vote tegelijk** "
                        "lopen.",
                        ephemeral=True
                    )

                    return

                # -----------------------------------------
                # MAX 1 NIEUWE VOTE PER 2 UUR
                # -----------------------------------------

                cursor = await db.execute(
                    """
                    SELECT last_vote_started_at
                    FROM game_vote_state
                    WHERE guild_id = ?
                    """,
                    (
                        guild.id,
                    )
                )

                row = await cursor.fetchone()

                if row:

                    last_started = row[0]

                    remaining = (
                        GAME_VOTE_START_COOLDOWN
                        -
                        (
                            now
                            -
                            last_started
                        )
                    )

                    if remaining > 0:

                        minutes = (
                            remaining + 59
                        ) // 60

                        await interaction.response.send_message(
                            "⏳ Er kan maximaal **1 nieuwe "
                            "game-vote per 2 uur** gestart "
                            "worden.\n"
                            f"Probeer over ongeveer "
                            f"**{minutes} minuten** opnieuw.",
                            ephemeral=True
                        )

                        return

                expires_at = (
                    now
                    +
                    GAME_VOTE_DURATION
                )

                cursor = await db.execute(
                    """
                    INSERT INTO game_button_votes (
                        guild_id,
                        channel_id,
                        creator_id,
                        game_name,
                        normalized_name,
                        created_at,
                        expires_at,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                    """,
                    (
                        guild.id,
                        interaction.channel_id,
                        interaction.user.id,
                        game_name,
                        normalized,
                        now,
                        expires_at
                    )
                )

                vote_id = cursor.lastrowid

                # Creator telt als eerste stem
                await db.execute(
                    """
                    INSERT INTO game_button_support (
                        vote_id,
                        user_id
                    )
                    VALUES (?, ?)
                    """,
                    (
                        vote_id,
                        interaction.user.id
                    )
                )

                await db.execute(
                    """
                    INSERT INTO game_vote_state (
                        guild_id,
                        last_vote_started_at
                    )
                    VALUES (?, ?)

                    ON CONFLICT(guild_id)
                    DO UPDATE SET
                    last_vote_started_at =
                    excluded.last_vote_started_at
                    """,
                    (
                        guild.id,
                        now
                    )
                )

                await db.commit()

            # Levels-koppeling: een geldig nieuw gamevoorstel levert XP op.
            levels = self.bot.get_cog("Levels")
            if levels is not None:
                try:
                    await levels.award_source(
                        guild,
                        interaction.user.id,
                        "game_suggestion",
                        f"game_suggestion:{vote_id}",
                        interaction.user.id,
                    )
                except Exception as error:
                    print(f"[GAME SUGGESTION XP] {error}")

            view = GameVoteView(
                self,
                vote_id
            )

            await interaction.response.send_message(
                f"🎮 **Nieuwe game voorgesteld: "
                f"{game_name}**\n\n"
                f"Voorgesteld door: "
                f"{interaction.user.mention}\n\n"
                f"✅ Stemmen: **1/{GAME_VOTE_REQUIRED}**\n"
                f"⏱️ Vote blijft maximaal **24 uur** open.\n\n"
                "Klik op **Steun voorstel** om te stemmen.",
                view=view
            )

            message = await interaction.original_response()

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                await db.execute(
                    """
                    UPDATE game_button_votes
                    SET message_id = ?
                    WHERE id = ?
                    """,
                    (
                        message.id,
                        vote_id
                    )
                )

                await db.commit()

            self.bot.add_view(
                view,
                message_id=message.id
            )

            asyncio.create_task(
                self.expire_game_vote(
                    vote_id,
                    expires_at
                )
            )

    async def handle_game_vote_support(
        self,
        interaction,
        vote_id
    ):

        now = int(
            time.time()
        )

        async with self.game_vote_lock:

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                cursor = await db.execute(
                    """
                    SELECT
                        guild_id,
                        channel_id,
                        message_id,
                        creator_id,
                        game_name,
                        expires_at,
                        status

                    FROM game_button_votes

                    WHERE id = ?
                    """,
                    (
                        vote_id,
                    )
                )

                vote = await cursor.fetchone()

                if not vote:

                    await interaction.response.send_message(
                        "❌ Deze vote bestaat niet meer.",
                        ephemeral=True
                    )

                    return

                (
                    guild_id,
                    channel_id,
                    message_id,
                    creator_id,
                    game_name,
                    expires_at,
                    status
                ) = vote

                if (
                    status != "active"
                    or
                    expires_at <= now
                ):

                    await interaction.response.send_message(
                        "❌ Deze vote is niet meer actief.",
                        ephemeral=True
                    )

                    return

                try:

                    await db.execute(
                        """
                        INSERT INTO game_button_support (
                            vote_id,
                            user_id
                        )
                        VALUES (?, ?)
                        """,
                        (
                            vote_id,
                            interaction.user.id
                        )
                    )

                except aiosqlite.IntegrityError:

                    await interaction.response.send_message(
                        "✅ Je hebt deze vote al gesteund.",
                        ephemeral=True
                    )

                    return

                cursor = await db.execute(
                    """
                    SELECT COUNT(*)
                    FROM game_button_support
                    WHERE vote_id = ?
                    """,
                    (
                        vote_id,
                    )
                )

                row = await cursor.fetchone()

                count = row[0]

                await db.commit()

            # Levels-koppeling: alleen een nieuwe, unieke steun op deze vote krijgt XP.
            # De maker telt als eerste stem bij het starten en krijgt daarvoor geen vote-XP.
            levels = self.bot.get_cog("Levels")
            if levels is not None:
                try:
                    await levels.award_source(
                        interaction.guild,
                        interaction.user.id,
                        "game_vote",
                        f"game_vote:{vote_id}:{interaction.user.id}",
                        creator_id,
                    )
                except Exception as error:
                    print(f"[GAME VOTE XP] {error}")

            if count < GAME_VOTE_REQUIRED:

                await interaction.response.send_message(
                    f"✅ Stem toegevoegd.\n"
                    f"De vote staat nu op "
                    f"**{count}/{GAME_VOTE_REQUIRED}**.",
                    ephemeral=True
                )

                try:

                    await interaction.message.edit(
                        content=(
                            f"🎮 **Nieuwe game voorgesteld: "
                            f"{game_name}**\n\n"
                            f"✅ Stemmen: "
                            f"**{count}/{GAME_VOTE_REQUIRED}**\n"
                            "⏱️ Vote blijft maximaal "
                            "**24 uur** open.\n\n"
                            "Klik op **Steun voorstel** "
                            "om te stemmen."
                        )
                    )

                except discord.HTTPException:
                    pass

                return

            # ---------------------------------------------
            # VOTE GESLAAGD
            # ---------------------------------------------

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                await db.execute(
                    """
                    UPDATE game_button_votes
                    SET status = 'passed'
                    WHERE id = ?
                    AND status = 'active'
                    """,
                    (
                        vote_id,
                    )
                )

                await db.commit()

            await interaction.response.send_message(
                f"🎉 **{game_name}** heeft "
                f"{GAME_VOTE_REQUIRED} stemmen gehaald!",
                ephemeral=True
            )

            guild = self.bot.get_guild(
                guild_id
            )

            if guild is None:
                return

            try:

                role, game_channel = await create_game(
                    self.bot,
                    guild,
                    game_name
                )

            except Exception as exc:

                print(
                    f"[GAME VOTE CREATE] {exc}"
                )

                return

            proposal_channel = guild.get_channel(
                GAME_PROPOSAL_CHANNEL_ID
            )

            if isinstance(
                proposal_channel,
                discord.TextChannel
            ):

                await create_temporary_role_button(
                    self.bot,
                    proposal_channel,
                    game_name,
                    role
                )

            await notify_owner_new_game(
                self.bot,
                guild,
                game_name,
                role,
                game_channel
            )

            # Levels-koppeling: de oorspronkelijke voorsteller krijgt bonus-XP
            # zodra het voorstel daadwerkelijk succesvol is aangemaakt.
            levels = self.bot.get_cog("Levels")
            if levels is not None:
                try:
                    await levels.award_source(
                        guild,
                        creator_id,
                        "game_success",
                        f"game_success:{vote_id}",
                        interaction.user.id,
                    )
                except Exception as error:
                    print(f"[GAME SUCCESS XP] {error}")

            try:

                disabled = GameVoteView(
                    self,
                    vote_id
                )

                for item in disabled.children:
                    item.disabled = True

                await interaction.message.edit(
                    content=(
                        f"✅ **Vote geslaagd — "
                        f"{game_name} is toegevoegd!**\n\n"
                        f"Stemmen: "
                        f"**{count}/{GAME_VOTE_REQUIRED}**"
                    ),
                    view=disabled
                )

            except discord.HTTPException:
                pass

    async def expire_game_vote(
        self,
        vote_id,
        expires_at
    ):

        delay = max(
            0,
            expires_at - int(
                time.time()
            )
        )

        await asyncio.sleep(
            delay
        )

        async with self.game_vote_lock:

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                cursor = await db.execute(
                    """
                    SELECT
                        guild_id,
                        channel_id,
                        message_id,
                        game_name,
                        status
                    FROM game_button_votes
                    WHERE id = ?
                    """,
                    (
                        vote_id,
                    )
                )

                row = await cursor.fetchone()

                if not row:
                    return

                (
                    guild_id,
                    channel_id,
                    message_id,
                    game_name,
                    status
                ) = row

                if status != "active":
                    return

                await db.execute(
                    """
                    UPDATE game_button_votes
                    SET status = 'expired'
                    WHERE id = ?
                    """,
                    (
                        vote_id,
                    )
                )

                await db.commit()

        guild = self.bot.get_guild(
            guild_id
        )

        if not guild:
            return

        channel = guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return

        try:

            message = await channel.fetch_message(
                message_id
            )

            disabled = GameVoteView(
                self,
                vote_id
            )

            for item in disabled.children:
                item.disabled = True

            await message.edit(
                content=(
                    f"⌛ **Vote verlopen: "
                    f"{game_name}**\n\n"
                    f"De vote heeft niet binnen 24 uur "
                    f"{GAME_VOTE_REQUIRED} stemmen gehaald."
                ),
                view=disabled
            )

        except discord.HTTPException:
            pass

    # =====================================================
    # UNARCHIVE
    # =====================================================

    @game.command(
        name="unarchive",
        description="Zet een gearchiveerde game terug"
    )
    async def unarchive_command(
        self,
        interaction: discord.Interaction,
        game_name: str
    ):

        if not interaction.guild:
            return

        game_name = clean_game_name(
            game_name
        )

        channel = find_archived_game_channel(
            interaction.guild,
            game_name
        )

        if channel is None:

            await interaction.response.send_message(
                f"❌ Gearchiveerde game "
                f"**{game_name}** niet gevonden.",
                ephemeral=True
            )

            return

        member = interaction.user

        if (
            isinstance(
                member,
                discord.Member
            )
            and
            management_access(
                member
            )
        ):

            await interaction.response.defer(
                ephemeral=True
            )

            await unarchive_game_now(
                self.bot,
                interaction.guild,
                game_name
            )

            await interaction.followup.send(
                f"✅ **{game_name}** teruggezet.",
                ephemeral=True
            )

            return

        await self.create_unarchive_vote(
            interaction,
            game_name
        )

    async def create_unarchive_vote(
        self,
        interaction,
        game_name
    ):

        guild = interaction.guild

        vote_channel = guild.get_channel(
            UNARCHIVE_CHANNEL_ID
        )

        if not isinstance(
            vote_channel,
            discord.TextChannel
        ):

            await interaction.response.send_message(
                "❌ Unarchive vote-channel niet gevonden.",
                ephemeral=True
            )

            return

        now = int(
            time.time()
        )

        expires_at = (
            now
            +
            UNARCHIVE_VOTE_DURATION
        )

        normalized = normalize_game_name(
            game_name
        )

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT id
                FROM unarchive_button_votes
                WHERE guild_id = ?
                AND normalized_name = ?
                AND status = 'active'
                AND expires_at > ?
                LIMIT 1
                """,
                (
                    guild.id,
                    normalized,
                    now
                )
            )

            existing = await cursor.fetchone()

            if existing:

                await interaction.response.send_message(
                    "❌ Voor deze game loopt al "
                    "een unarchive-vote.",
                    ephemeral=True
                )

                return

            cursor = await db.execute(
                """
                INSERT INTO unarchive_button_votes (
                    guild_id,
                    channel_id,
                    creator_id,
                    game_name,
                    normalized_name,
                    created_at,
                    expires_at,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
                """,
                (
                    guild.id,
                    vote_channel.id,
                    interaction.user.id,
                    game_name,
                    normalized,
                    now,
                    expires_at
                )
            )

            vote_id = cursor.lastrowid

            await db.execute(
                """
                INSERT INTO unarchive_button_support (
                    vote_id,
                    user_id
                )
                VALUES (?, ?)
                """,
                (
                    vote_id,
                    interaction.user.id
                )
            )

            await db.commit()

        view = UnarchiveVoteView(
            self,
            vote_id
        )

        message = await vote_channel.send(
            f"📦 **Game terugzetten: {game_name}**\n\n"
            f"Aangevraagd door: "
            f"{interaction.user.mention}\n\n"
            f"Stemmen: "
            f"**1/{UNARCHIVE_VOTE_REQUIRED}**\n"
            "⏱️ Maximaal **24 uur**.",
            view=view
        )

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            await db.execute(
                """
                UPDATE unarchive_button_votes
                SET message_id = ?
                WHERE id = ?
                """,
                (
                    message.id,
                    vote_id
                )
            )

            await db.commit()

        self.bot.add_view(
            view,
            message_id=message.id
        )

        asyncio.create_task(
            self.expire_unarchive_vote(
                vote_id,
                expires_at
            )
        )

        await interaction.response.send_message(
            f"✅ Unarchive-vote voor "
            f"**{game_name}** gestart in "
            f"{vote_channel.mention}.",
            ephemeral=True
        )

    async def handle_unarchive_support(
        self,
        interaction,
        vote_id
    ):

        now = int(
            time.time()
        )

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    guild_id,
                    game_name,
                    expires_at,
                    status
                FROM unarchive_button_votes
                WHERE id = ?
                """,
                (
                    vote_id,
                )
            )

            row = await cursor.fetchone()

            if not row:

                await interaction.response.send_message(
                    "❌ Vote niet gevonden.",
                    ephemeral=True
                )

                return

            guild_id, game_name, expires_at, status = row

            if (
                status != "active"
                or
                expires_at <= now
            ):

                await interaction.response.send_message(
                    "❌ Deze vote is niet meer actief.",
                    ephemeral=True
                )

                return

            try:

                await db.execute(
                    """
                    INSERT INTO unarchive_button_support (
                        vote_id,
                        user_id
                    )
                    VALUES (?, ?)
                    """,
                    (
                        vote_id,
                        interaction.user.id
                    )
                )

            except aiosqlite.IntegrityError:

                await interaction.response.send_message(
                    "✅ Je hebt al gestemd.",
                    ephemeral=True
                )

                return

            cursor = await db.execute(
                """
                SELECT COUNT(*)
                FROM unarchive_button_support
                WHERE vote_id = ?
                """,
                (
                    vote_id,
                )
            )

            count = (
                await cursor.fetchone()
            )[0]

            await db.commit()

        if count < UNARCHIVE_VOTE_REQUIRED:

            await interaction.response.send_message(
                f"✅ Stem toegevoegd: "
                f"**{count}/{UNARCHIVE_VOTE_REQUIRED}**",
                ephemeral=True
            )

            try:

                await interaction.message.edit(
                    content=(
                        f"📦 **Game terugzetten: "
                        f"{game_name}**\n\n"
                        f"Stemmen: "
                        f"**{count}/{UNARCHIVE_VOTE_REQUIRED}**\n"
                        "⏱️ Maximaal **24 uur**."
                    )
                )

            except discord.HTTPException:
                pass

            return

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            await db.execute(
                """
                UPDATE unarchive_button_votes
                SET status = 'passed'
                WHERE id = ?
                """,
                (
                    vote_id,
                )
            )

            await db.commit()

        guild = self.bot.get_guild(
            guild_id
        )

        if guild:

            await unarchive_game_now(
                self.bot,
                guild,
                game_name
            )

        await interaction.response.send_message(
            f"🎉 **{game_name}** wordt teruggezet!",
            ephemeral=True
        )

        disabled = UnarchiveVoteView(
            self,
            vote_id
        )

        for item in disabled.children:
            item.disabled = True

        try:

            await interaction.message.edit(
                content=(
                    f"✅ **{game_name} is teruggezet!**\n\n"
                    f"Stemmen: "
                    f"**{count}/{UNARCHIVE_VOTE_REQUIRED}**"
                ),
                view=disabled
            )

        except discord.HTTPException:
            pass

    async def expire_unarchive_vote(
        self,
        vote_id,
        expires_at
    ):

        delay = max(
            0,
            expires_at - int(
                time.time()
            )
        )

        await asyncio.sleep(
            delay
        )

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    guild_id,
                    channel_id,
                    message_id,
                    game_name,
                    status
                FROM unarchive_button_votes
                WHERE id = ?
                """,
                (
                    vote_id,
                )
            )

            row = await cursor.fetchone()

            if not row:
                return

            guild_id, channel_id, message_id, game_name, status = row

            if status != "active":
                return

            await db.execute(
                """
                UPDATE unarchive_button_votes
                SET status = 'expired'
                WHERE id = ?
                """,
                (
                    vote_id,
                )
            )

            await db.commit()

        guild = self.bot.get_guild(
            guild_id
        )

        if not guild:
            return

        channel = guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            return

        try:

            message = await channel.fetch_message(
                message_id
            )

            disabled = UnarchiveVoteView(
                self,
                vote_id
            )

            for item in disabled.children:
                item.disabled = True

            await message.edit(
                content=(
                    f"⌛ **Unarchive-vote verlopen: "
                    f"{game_name}**"
                ),
                view=disabled
            )

        except discord.HTTPException:
            pass

    # =====================================================
    # RESTORE
    # =====================================================

    async def restore_game_votes(
        self
    ):

        now = int(
            time.time()
        )

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    id,
                    message_id,
                    expires_at
                FROM game_button_votes
                WHERE status = 'active'
                """
            )

            rows = await cursor.fetchall()

        for vote_id, message_id, expires_at in rows:

            if expires_at <= now:

                asyncio.create_task(
                    self.expire_game_vote(
                        vote_id,
                        now
                    )
                )

                continue

            if message_id:

                self.bot.add_view(
                    GameVoteView(
                        self,
                        vote_id
                    ),
                    message_id=message_id
                )

            asyncio.create_task(
                self.expire_game_vote(
                    vote_id,
                    expires_at
                )
            )

        print(
            f"[RESTORE] {len(rows)} game-vote(s)"
        )

    async def restore_temp_buttons(
        self
    ):

        now = int(
            time.time()
        )

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    message_id,
                    guild_id,
                    channel_id,
                    role_id,
                    game_name,
                    expires_at
                FROM temp_role_buttons_v2
                WHERE status = 'active'
                """
            )

            rows = await cursor.fetchall()

        for (
            message_id,
            guild_id,
            channel_id,
            role_id,
            game_name,
            expires_at
        ) in rows:

            if expires_at > now:

                self.bot.add_view(
                    TempRoleView(
                        role_id,
                        game_name
                    ),
                    message_id=message_id
                )

            asyncio.create_task(
                expire_temporary_role_button(
                    self.bot,
                    message_id,
                    guild_id,
                    channel_id,
                    role_id,
                    game_name,
                    expires_at
                )
            )

        print(
            f"[RESTORE] {len(rows)} temp role-button(s)"
        )

    async def restore_unarchive_votes(
        self
    ):

        now = int(
            time.time()
        )

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    id,
                    message_id,
                    expires_at
                FROM unarchive_button_votes
                WHERE status = 'active'
                """
            )

            rows = await cursor.fetchall()

        for vote_id, message_id, expires_at in rows:

            if message_id and expires_at > now:

                self.bot.add_view(
                    UnarchiveVoteView(
                        self,
                        vote_id
                    ),
                    message_id=message_id
                )

            asyncio.create_task(
                self.expire_unarchive_vote(
                    vote_id,
                    expires_at
                )
            )

        print(
            f"[RESTORE] {len(rows)} unarchive-vote(s)"
        )


# =========================================================
# REMOVE CONFIRMATION
# =========================================================

class RemoveConfirmView(
    discord.ui.View
):

    def __init__(
        self,
        cog,
        user_id,
        game_name
    ):

        super().__init__(
            timeout=60
        )

        self.cog = cog

        self.user_id = user_id

        self.game_name = game_name

    @discord.ui.button(
        label="Ja, verwijderen",
        style=discord.ButtonStyle.danger
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if interaction.user.id != self.user_id:

            await interaction.response.send_message(
                "❌ Alleen degene die dit command "
                "gebruikte kan bevestigen.",
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        await self.cog.remove_game_now(
            interaction.guild,
            self.game_name
        )

        await interaction.followup.send(
            f"🗑️ **{self.game_name}** verwijderd.",
            ephemeral=True
        )

        self.stop()

    @discord.ui.button(
        label="Annuleren",
        style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if interaction.user.id != self.user_id:
            return

        await interaction.response.edit_message(
            content="❌ Verwijderen geannuleerd.",
            view=None
        )

        self.stop()


# =========================================================
# SETUP
# =========================================================

async def setup(
    bot: commands.Bot
):

    await bot.add_cog(
        Games(
            bot
        )
    )