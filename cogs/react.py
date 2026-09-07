import aiosqlite
import discord

from discord import app_commands
from discord.ext import commands


# =========================================================
# CONFIG
# =========================================================

DATABASE_PATH = "levenloos.db"

DEFAULT_ROLE_CHANNEL_ID = 940376085865037884
ADMIN_ACCESS_ROLE_ID = 940331020434161674
DIVIDER_ROLE_ID = 1546599899682967662

REACT_ROLES_BANNER = (
    "<:r1:1546442663903895604>"
    "<:r2:1546442688595890186>"
    "<:r3:1546442710439829566>"
    "<:r4:1546442734062141460>"
    "<:r5:1546442754777686087>"
    "<:r6:1546442824025899018>"
    "<:r7:1546442846792450128>"
    "<:r8:1546442879012962344>"
    "<:r9:1546442901272137768>"
)

GAMES_EMOJI = "<:games:1546435140790517780>"
GENRES_EMOJI = "<:genres:1546435084897222686>"
ROLES_EMOJI = "<:roll:1546435118455988332>"

ITEMS_PER_PAGE = 20


# =========================================================
# ACCESS
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
# DATABASE
# =========================================================

async def initialise_database():

    async with aiosqlite.connect(
        DATABASE_PATH
    ) as db:

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS react_config (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS react_items (
                guild_id INTEGER NOT NULL,
                section TEXT NOT NULL,
                role_id INTEGER NOT NULL,
                button_name TEXT NOT NULL,
                emoji TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                min_level INTEGER NOT NULL DEFAULT 1,
                item_group TEXT NOT NULL DEFAULT 'roles',

                PRIMARY KEY (
                    guild_id,
                    section,
                    role_id
                )
            )
            """
        )

        # Voor oudere versies waarin react_config nog geen message_id had.
        try:
            await db.execute(
                """
                ALTER TABLE react_config
                ADD COLUMN message_id INTEGER
                """
            )
        except aiosqlite.OperationalError:
            pass

        # Migraties voor level-gated rollen en subgroepen.
        try:
            await db.execute(
                "ALTER TABLE react_items ADD COLUMN min_level INTEGER NOT NULL DEFAULT 1"
            )
        except aiosqlite.OperationalError:
            pass

        try:
            await db.execute(
                "ALTER TABLE react_items ADD COLUMN item_group TEXT NOT NULL DEFAULT 'roles'"
            )
        except aiosqlite.OperationalError:
            pass

        await db.commit()

    print(
        "[DB] React roles database klaar"
    )


# =========================================================
# HELPERS
# =========================================================

def section_info(
    section: str
) -> tuple[str, str, str]:

    if section == "game":
        return (
            "Games",
            GAMES_EMOJI,
            "Kies de games die je speelt."
        )

    if section == "genre":
        return (
            "Genres",
            GENRES_EMOJI,
            "Kies je favoriete gamegenres."
        )

    if section == "color":
        return (
            "Kleuren",
            "🎨",
            "Kies één kleur die je hebt vrijgespeeld."
        )

    if section == "roles":
        return (
            "Rollen",
            ROLES_EMOJI,
            "Kies de extra rollen die bij jou passen."
        )

    return (
        "Roles",
        ROLES_EMOJI,
        "Kies de overige rollen die bij jou passen."
    )


def clean_role_name(
    role: discord.Role
) -> str:

    return role.name.lstrip(
        "◆ "
    ).strip()


def parse_emoji(
    value: str | None,
    default: str
) -> str:

    if value is None:
        return default

    value = value.strip()

    if not value:
        return default

    emoji = discord.PartialEmoji.from_str(
        value
    )

    if not emoji.name:
        raise ValueError(
            "Ongeldige emoji"
        )

    return value


# =========================================================
# REACT COG
# =========================================================

class ReactRoles(
    commands.Cog
):

    react = app_commands.Group(
        name="react",
        description="Beheer het React Roles-systeem"
    )

    game = app_commands.Group(
        name="game",
        description="Beheer game-rollen",
        parent=react
    )

    genre = app_commands.Group(
        name="genre",
        description="Beheer genre-rollen",
        parent=react
    )

    role_group = app_commands.Group(
        name="role",
        description="Beheer algemene rollen",
        parent=react
    )

    def __init__(
        self,
        bot: commands.Bot
    ):

        self.bot = bot

    async def cog_load(
        self
    ):

        await initialise_database()

        print(
            "[REACT] React-role module geladen"
        )

    # =====================================================
    # CONFIG
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
                    channel_id,
                    message_id
                FROM react_config
                WHERE guild_id = ?
                """,
                (
                    guild_id,
                )
            )

            return await cursor.fetchone()

    async def set_config(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int | None
    ):

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            await db.execute(
                """
                INSERT INTO react_config (
                    guild_id,
                    channel_id,
                    message_id
                )
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id)
                DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_id = excluded.message_id
                """,
                (
                    guild_id,
                    channel_id,
                    message_id,
                )
            )

            await db.commit()

    # =====================================================
    # PUBLIC MAIN MENU
    # =====================================================

    def build_main_view(
        self
    ) -> discord.ui.View:

        view = discord.ui.View(
            timeout=None
        )

        view.add_item(
            discord.ui.Button(
                label="Games",
                emoji=discord.PartialEmoji.from_str(
                    GAMES_EMOJI
                ),
                style=discord.ButtonStyle.primary,
                custom_id="react_open:game"
            )
        )

        view.add_item(
            discord.ui.Button(
                label="Genres",
                emoji=discord.PartialEmoji.from_str(
                    GENRES_EMOJI
                ),
                style=discord.ButtonStyle.primary,
                custom_id="react_open:genre"
            )
        )

        view.add_item(
            discord.ui.Button(
                label="Roles",
                emoji=discord.PartialEmoji.from_str(
                    ROLES_EMOJI
                ),
                style=discord.ButtonStyle.primary,
                custom_id="react_open:role"
            )
        )

        return view

    def main_content(
        self
    ) -> str:

        return (
            f"# {REACT_ROLES_BANNER}\n\n"
            "> **HOE WERKT HET?**\n"
            "> └ Kies hieronder de games, genres en rollen die bij jou passen.\n"
            "> └ Klik opnieuw op een knop om een rol weer te verwijderen.\n\n"
            f"> {GAMES_EMOJI} **GAMES**\n"
            "> └ Kies de games die je speelt.\n\n"
            f"> {GENRES_EMOJI} **GENRES**\n"
            "> └ Kies je favoriete gamegenres.\n\n"
            f"> {ROLES_EMOJI} **ROLES**\n"
            "> └ Kies de overige rollen die bij jou passen."
        )

    async def refresh_main_message(
        self,
        guild: discord.Guild
    ):

        config = await self.get_config(
            guild.id
        )

        if config:
            channel_id, message_id = config
        else:
            channel_id = DEFAULT_ROLE_CHANNEL_ID
            message_id = None

        channel = guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.TextChannel
        ):
            raise RuntimeError(
                "React Roles-kanaal niet gevonden."
            )

        message = None

        if message_id:

            try:
                message = await channel.fetch_message(
                    message_id
                )
            except discord.HTTPException:
                message = None

        if message:

            await message.edit(
                content=self.main_content(),
                view=self.build_main_view()
            )

        else:

            message = await channel.send(
                self.main_content(),
                view=self.build_main_view()
            )

            await self.set_config(
                guild.id,
                channel.id,
                message.id
            )

        return message

    # =====================================================
    # ITEMS
    # =====================================================

    async def get_entries(
        self,
        guild: discord.Guild,
        section: str
    ) -> list[dict]:

        db_section = "role" if section in {"color", "roles"} else section

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    role_id,
                    button_name,
                    emoji,
                    sort_order,
                    min_level,
                    item_group
                FROM react_items
                WHERE guild_id = ?
                AND section = ?
                ORDER BY
                    button_name COLLATE NOCASE ASC,
                    sort_order ASC
                """,
                (
                    guild.id,
                    db_section,
                )
            )

            rows = await cursor.fetchall()

        entries = []
        missing_role_ids = []

        for (
            role_id,
            button_name,
            emoji,
            sort_order,
            min_level,
            item_group
        ) in rows:

            row_group = (item_group or "roles").lower()
            if section in {"color", "roles"} and row_group != section:
                continue

            role = guild.get_role(
                role_id
            )

            if role is None:

                missing_role_ids.append(
                    role_id
                )

                continue

            entries.append(
                {
                    "role_id": role_id,
                    "button_name": button_name,
                    "emoji": emoji,
                    "sort_order": sort_order,
                    "min_level": max(1, int(min_level or 1)),
                    "group": (item_group or "roles").lower(),
                }
            )

        if missing_role_ids:

            async with aiosqlite.connect(
                DATABASE_PATH
            ) as db:

                await db.executemany(
                    """
                    DELETE FROM react_items
                    WHERE guild_id = ?
                    AND section = ?
                    AND role_id = ?
                    """,
                    [
                        (
                            guild.id,
                            db_section,
                            role_id,
                        )
                        for role_id in missing_role_ids
                    ]
                )

                await db.commit()

        return entries

    async def get_entry(
        self,
        guild_id: int,
        section: str,
        role_id: int
    ):

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT
                    button_name,
                    emoji,
                    min_level,
                    item_group
                FROM react_items
                WHERE guild_id = ?
                AND section = ?
                AND role_id = ?
                """,
                (
                    guild_id,
                    section,
                    role_id,
                )
            )

            return await cursor.fetchone()

    async def add_or_update_item(
        self,
        guild_id: int,
        section: str,
        role_id: int,
        button_name: str,
        emoji: str,
        min_level: int = 1,
        item_group: str = "roles"
    ):

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                SELECT COALESCE(
                    MAX(sort_order),
                    -1
                ) + 1
                FROM react_items
                WHERE guild_id = ?
                AND section = ?
                """,
                (
                    guild_id,
                    section,
                )
            )

            row = await cursor.fetchone()

            sort_order = (
                row[0]
                if row
                else 0
            )

            await db.execute(
                """
                INSERT INTO react_items (
                    guild_id,
                    section,
                    role_id,
                    button_name,
                    emoji,
                    sort_order,
                    min_level,
                    item_group
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    guild_id,
                    section,
                    role_id
                )
                DO UPDATE SET
                    button_name = excluded.button_name,
                    emoji = excluded.emoji,
                    min_level = excluded.min_level,
                    item_group = excluded.item_group
                """,
                (
                    guild_id,
                    section,
                    role_id,
                    button_name,
                    emoji,
                    sort_order,
                    max(1, int(min_level)),
                    item_group,
                )
            )

            await db.commit()

    async def get_member_level(self, member: discord.Member) -> int:
        """Lees het actuele level uit de Levels-cog."""
        levels_cog = self.bot.get_cog("Levels")
        if levels_cog is None or not hasattr(levels_cog, "get_user"):
            return 1
        try:
            row = await levels_cog.get_user(member.guild.id, member.id)
        except Exception:
            return 1
        if not row:
            return 1
        total_xp = max(0, int(row[0]))
        # Zelfde curve als levels.py: cumulatief 100 * ((L*(L+1)/2)-1).
        level = 1
        while 100 * (((level + 1) * (level + 2) // 2) - 1) <= total_xp:
            level += 1
        return level

    def build_role_hub_view(self) -> discord.ui.View:
        view = discord.ui.View(timeout=900)
        view.add_item(discord.ui.Button(
            label="Kleuren", emoji="🎨", style=discord.ButtonStyle.primary,
            custom_id="react_subopen:color"
        ))
        view.add_item(discord.ui.Button(
            label="Rollen", emoji=discord.PartialEmoji.from_str(ROLES_EMOJI),
            style=discord.ButtonStyle.primary, custom_id="react_subopen:roles"
        ))
        return view

    async def send_role_hub(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"{ROLES_EMOJI} **ROLES**\n"
            "└ Kies wat je wilt aanpassen.\n"
            "└ 🎨 **Kleuren** = je vrijgespeelde naamkleur.\n"
            "└ ◆ **Rollen** = Events, Movienight en andere extra rollen.",
            view=self.build_role_hub_view(),
            ephemeral=True
        )

    # =====================================================
    # EPHEMERAL SELECTOR
    # =====================================================

    async def build_selector_view(
        self,
        member: discord.Member,
        section: str,
        entries: list[dict],
        page: int
    ) -> discord.ui.View:

        view = discord.ui.View(timeout=900)
        member_level = await self.get_member_level(member) if section in {"color", "roles"} else 999

        # Admin Kleur is alleen voor deze admin-role zichtbaar.
        if section == "color" and member.guild.get_role(ADMIN_ACCESS_ROLE_ID) not in member.roles:
            entries = [e for e in entries if e["button_name"].strip().lower() != "admin kleur"]

        # Geen kleur is een virtuele optie: geen extra Discord-role nodig.
        if section == "color":
            entries = [{
                "role_id": 0, "button_name": "Geen kleur", "emoji": "⚪",
                "sort_order": -1, "min_level": 1, "group": "color"
            }] + entries

        total_pages = max(
            1,
            (
                len(entries)
                + ITEMS_PER_PAGE
                - 1
            )
            // ITEMS_PER_PAGE
        )

        page = max(
            0,
            min(
                page,
                total_pages - 1
            )
        )

        start = (
            page
            * ITEMS_PER_PAGE
        )

        page_entries = entries[
            start:start + ITEMS_PER_PAGE
        ]

        for index, entry in enumerate(
            page_entries
        ):

            role = member.guild.get_role(entry["role_id"]) if entry["role_id"] else None
            if entry["role_id"] and role is None:
                continue

            required_level = max(1, int(entry.get("min_level", 1)))
            is_admin_color = entry["button_name"].strip().lower() == "admin kleur"
            locked = (not is_admin_color) and member_level < required_level
            has_role = role in member.roles if role is not None else not any(
                member.guild.get_role(e["role_id"]) in member.roles
                for e in entries if e.get("role_id")
            )

            try:
                emoji = discord.PartialEmoji.from_str(
                    entry["emoji"]
                )
            except Exception:
                _, default_emoji, _ = section_info(
                    section
                )
                emoji = discord.PartialEmoji.from_str(
                    default_emoji
                )

            button = discord.ui.Button(
                label=(
                    f"🔒 {entry['button_name']} • Lvl {required_level}"
                    if locked else entry["button_name"]
                )[:80],
                emoji=emoji,
                style=(
                    discord.ButtonStyle.success
                    if has_role
                    else discord.ButtonStyle.secondary
                ),
                disabled=locked,
                custom_id=(
                    f"react_toggle:"
                    f"{section}:"
                    f"{entry['role_id']}:"
                    f"{page}"
                ),
                row=index // 5
            )

            view.add_item(
                button
            )

        # Rij 5 = navigatie. 20 role buttons laten hier ruimte voor.
        if total_pages > 1:

            view.add_item(
                discord.ui.Button(
                    label="Vorige",
                    emoji="◀️",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"react_page:{section}:{page - 1}",
                    disabled=(page <= 0),
                    row=4
                )
            )

            view.add_item(
                discord.ui.Button(
                    label=f"{page + 1}/{total_pages}",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"react_pageinfo:{section}:{page}",
                    disabled=True,
                    row=4
                )
            )

            view.add_item(
                discord.ui.Button(
                    label="Volgende",
                    emoji="▶️",
                    style=discord.ButtonStyle.primary,
                    custom_id=f"react_page:{section}:{page + 1}",
                    disabled=(page >= total_pages - 1),
                    row=4
                )
            )

        return view

    async def selector_content(
        self,
        guild: discord.Guild,
        section: str,
        page: int
    ) -> tuple[str, list[dict], int]:

        entries = await self.get_entries(
            guild,
            section
        )

        title, emoji, description = section_info(
            section
        )

        total_pages = max(
            1,
            (
                len(entries)
                + ITEMS_PER_PAGE
                - 1
            )
            // ITEMS_PER_PAGE
        )

        page = max(
            0,
            min(
                page,
                total_pages - 1
            )
        )

        if entries:
            content = (
                f"{emoji} **{title.upper()}**\n"
                f"└ {description}\n"
                "└ Groen = je hebt deze rol.\n"
                + ("└ 🔒 = je level is nog te laag.\n" if section in {"color", "roles"} else "")
                + ("└ Je kunt maar één kleur tegelijk kiezen.\n" if section == "color" else "")
                + "└ Klik opnieuw om hem te verwijderen."
            )
        else:
            content = (
                f"{emoji} **{title.upper()}**\n"
                "└ Hier zijn nog geen rollen ingesteld."
            )

        return (
            content,
            entries,
            page,
        )

    async def send_selector(
        self,
        interaction: discord.Interaction,
        section: str,
        page: int = 0
    ):

        if interaction.guild is None:
            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):
            return

        content, entries, page = await self.selector_content(
            interaction.guild,
            section,
            page
        )

        view = await self.build_selector_view(
            member,
            section,
            entries,
            page
        )

        await interaction.response.send_message(
            content,
            view=view,
            ephemeral=True
        )

    async def edit_selector(
        self,
        interaction: discord.Interaction,
        section: str,
        page: int
    ):

        if interaction.guild is None:
            return

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):
            return

        # Haal de member opnieuw op bij Discord zodat de knopkleur
        # direct klopt na add/remove van een rol. interaction.user
        # kan op dit moment nog de oude roles-cache bevatten.
        try:
            fresh_member = await interaction.guild.fetch_member(
                member.id
            )
        except discord.HTTPException:
            fresh_member = member

        content, entries, page = await self.selector_content(
            interaction.guild,
            section,
            page
        )

        view = await self.build_selector_view(
            fresh_member,
            section,
            entries,
            page
        )

        await interaction.response.edit_message(
            content=content,
            view=view
        )

    # =====================================================
    # ADMIN HELPERS
    # =====================================================

    async def validate_admin(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.guild is None:
            return False

        member = interaction.user

        if (
            not isinstance(
                member,
                discord.Member
            )
            or
            not management_access(
                member
            )
        ):

            await interaction.response.send_message(
                "❌ Je hebt geen toegang tot dit command.",
                ephemeral=True
            )

            return False

        return True

    async def add_item_command(
        self,
        interaction: discord.Interaction,
        section: str,
        role: discord.Role,
        buttonnaam: str,
        emoji: str | None,
        min_level: int = 1,
        item_group: str = "roles"
    ):

        if not await self.validate_admin(
            interaction
        ):
            return

        assert interaction.guild is not None

        buttonnaam = buttonnaam.strip()

        if not 1 <= len(
            buttonnaam
        ) <= 80:

            await interaction.response.send_message(
                "❌ Buttonnaam moet tussen 1 en 80 tekens zijn.",
                ephemeral=True
            )
            return

        if role.is_default():

            await interaction.response.send_message(
                "❌ @everyone kan niet gebruikt worden.",
                ephemeral=True
            )
            return

        if role.managed:

            await interaction.response.send_message(
                "❌ Deze rol wordt door Discord of een integratie beheerd.",
                ephemeral=True
            )
            return

        bot_member = interaction.guild.me

        if (
            bot_member is None
            or
            role >= bot_member.top_role
        ):

            await interaction.response.send_message(
                "❌ Mijn botrol moet boven deze rol staan.",
                ephemeral=True
            )
            return

        _, default_emoji, _ = section_info(
            section
        )

        try:
            chosen_emoji = parse_emoji(
                emoji,
                default_emoji
            )
        except ValueError:
            await interaction.response.send_message(
                "❌ Ongeldige emoji. Gebruik bijvoorbeeld `🎮` "
                "of `<:naam:123456789>`.",
                ephemeral=True
            )
            return

        if section == "role":
            item_group = item_group.strip().lower()
            if item_group not in {"roles", "color"}:
                await interaction.response.send_message(
                    "❌ Group moet `roles` of `color` zijn.", ephemeral=True
                )
                return
            if min_level < 1 or min_level > 999:
                await interaction.response.send_message(
                    "❌ Level moet tussen 1 en 999 liggen.", ephemeral=True
                )
                return
        else:
            min_level = 1
            item_group = "roles"

        await self.add_or_update_item(
            interaction.guild.id,
            section,
            role.id,
            buttonnaam,
            chosen_emoji,
            min_level,
            item_group
        )

        await interaction.response.send_message(
            f"✅ **{buttonnaam}** toegevoegd.\n"
            f"└ Rol: {role.mention}\n"
            f"└ Emoji: {chosen_emoji}"
            + (f"\n└ Level: **{min_level}**\n└ Group: `{item_group}`" if section == "role" else ""),
            ephemeral=True
        )

    async def edit_item_command(
        self,
        interaction: discord.Interaction,
        section: str,
        role: discord.Role,
        buttonnaam: str | None,
        emoji: str | None,
        min_level: int | None = None,
        item_group: str | None = None
    ):

        if not await self.validate_admin(
            interaction
        ):
            return

        assert interaction.guild is not None

        current = await self.get_entry(
            interaction.guild.id,
            section,
            role.id
        )

        if current is None:

            await interaction.response.send_message(
                "❌ Deze rol staat niet in dit React Roles-menu.",
                ephemeral=True
            )
            return

        new_name = current[0]
        new_emoji = current[1]
        current_level = int(current[2] or 1)
        current_group = (current[3] or "roles").lower()

        if section == "role":
            if min_level is not None:
                current_level = max(1, int(min_level))
            if item_group is not None:
                candidate_group = item_group.strip().lower()
                if candidate_group not in {"roles", "color"}:
                    await interaction.response.send_message(
                        "❌ Group moet `roles` of `color` zijn.", ephemeral=True
                    )
                    return
                current_group = candidate_group

        if buttonnaam is not None:

            new_name = buttonnaam.strip()

            if not 1 <= len(
                new_name
            ) <= 80:

                await interaction.response.send_message(
                    "❌ Buttonnaam moet tussen 1 en 80 tekens zijn.",
                    ephemeral=True
                )
                return

        if emoji is not None:

            try:
                new_emoji = parse_emoji(
                    emoji,
                    current[1]
                )
            except ValueError:
                await interaction.response.send_message(
                    "❌ Ongeldige emoji.",
                    ephemeral=True
                )
                return

        await self.add_or_update_item(
            interaction.guild.id,
            section,
            role.id,
            new_name,
            new_emoji,
            current_level,
            current_group
        )

        await interaction.response.send_message(
            f"✅ **{new_name}** bijgewerkt.",
            ephemeral=True
        )

    async def remove_item_command(
        self,
        interaction: discord.Interaction,
        section: str,
        role: discord.Role
    ):

        if not await self.validate_admin(
            interaction
        ):
            return

        assert interaction.guild is not None

        async with aiosqlite.connect(
            DATABASE_PATH
        ) as db:

            cursor = await db.execute(
                """
                DELETE FROM react_items
                WHERE guild_id = ?
                AND section = ?
                AND role_id = ?
                """,
                (
                    interaction.guild.id,
                    section,
                    role.id,
                )
            )

            removed = (
                cursor.rowcount > 0
            )

            await db.commit()

        if not removed:

            await interaction.response.send_message(
                "❌ Die rol stond niet in dit menu.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🗑️ **{clean_role_name(role)}** uit het menu verwijderd.\n"
            "└ De Discord-rol zelf blijft bestaan.",
            ephemeral=True
        )

    async def list_item_command(
        self,
        interaction: discord.Interaction,
        section: str
    ):

        if not await self.validate_admin(
            interaction
        ):
            return

        assert interaction.guild is not None

        entries = await self.get_entries(
            interaction.guild,
            section
        )

        title, emoji, _ = section_info(
            section
        )

        if not entries:

            await interaction.response.send_message(
                f"{emoji} Er staan nog geen **{title.lower()}** in het menu.",
                ephemeral=True
            )
            return

        lines = []

        for index, entry in enumerate(
            entries,
            start=1
        ):

            role = interaction.guild.get_role(
                entry["role_id"]
            )

            if role is None:
                continue

            extra = ""
            if section == "role":
                extra = f" • lvl {entry.get('min_level', 1)} • `{entry.get('group', 'roles')}`"
            lines.append(
                f"`{index:02}` {entry['emoji']} "
                f"**{entry['button_name']}** → {role.mention}{extra}"
            )

        text = "\n".join(
            lines
        )

        if len(text) > 1800:
            text = (
                text[:1750]
                + "\n…"
            )

        await interaction.response.send_message(
            f"{emoji} **{title} React Roles**\n\n{text}",
            ephemeral=True
        )

    # =====================================================
    # /REACT SETUP / REFRESH
    # =====================================================

    @react.command(
        name="setup",
        description="Kies het kanaal voor het React Roles-menu"
    )
    @app_commands.describe(
        channel="Kanaal waarin het React Roles-bericht moet staan"
    )
    async def react_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel
    ):

        if not await self.validate_admin(
            interaction
        ):
            return

        assert interaction.guild is not None

        old_config = await self.get_config(
            interaction.guild.id
        )

        await interaction.response.defer(
            ephemeral=True
        )

        # Verwijder oud hoofdmenu als het naar een ander kanaal verhuist.
        if old_config:
            old_channel_id, old_message_id = old_config

            if (
                old_message_id
                and
                old_channel_id != channel.id
            ):

                old_channel = interaction.guild.get_channel(
                    old_channel_id
                )

                if isinstance(
                    old_channel,
                    discord.TextChannel
                ):

                    try:
                        old_message = await old_channel.fetch_message(
                            old_message_id
                        )
                        await old_message.delete()
                    except discord.HTTPException:
                        pass

        await self.set_config(
            interaction.guild.id,
            channel.id,
            None
        )

        message = await self.refresh_main_message(
            interaction.guild
        )

        await interaction.followup.send(
            f"✅ React Roles staat nu in {channel.mention}.\n"
            f"└ Hoofdmenu: {message.jump_url}\n"
            "└ Beheercommands werken vanuit ieder kanaal.",
            ephemeral=True
        )

    @react.command(
        name="refresh",
        description="Herstel of vernieuw het openbare React Roles-menu"
    )
    async def react_refresh(
        self,
        interaction: discord.Interaction
    ):

        if not await self.validate_admin(
            interaction
        ):
            return

        assert interaction.guild is not None

        await interaction.response.defer(
            ephemeral=True
        )

        message = await self.refresh_main_message(
            interaction.guild
        )

        await interaction.followup.send(
            f"🔄 React Roles-menu vernieuwd.\n"
            f"└ {message.jump_url}",
            ephemeral=True
        )

    @react.command(
        name="divider",
        description="Geef de Divider-rol aan iedereen op de server"
    )
    async def react_divider(
        self,
        interaction: discord.Interaction
    ):

        if not await self.validate_admin(
            interaction
        ):
            return

        assert interaction.guild is not None

        role = interaction.guild.get_role(
            DIVIDER_ROLE_ID
        )

        if role is None:
            await interaction.response.send_message(
                f"❌ Divider-rol `{DIVIDER_ROLE_ID}` niet gevonden.",
                ephemeral=True
            )
            return

        bot_member = interaction.guild.me

        if (
            bot_member is None
            or role >= bot_member.top_role
        ):
            await interaction.response.send_message(
                "❌ Mijn botrol moet boven de Divider-rol staan.",
                ephemeral=True
            )
            return

        await interaction.response.defer(
            ephemeral=True
        )

        added = 0
        already_had = 0
        failed = 0

        for member in interaction.guild.members:
            if role in member.roles:
                already_had += 1
                continue

            try:
                await member.add_roles(
                    role,
                    reason=f"Divider toegewezen via /react divider door {interaction.user}"
                )
                added += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

        await interaction.followup.send(
            "✅ **Divider uitgedeeld.**\n"
            f"└ Toegevoegd: **{added}**\n"
            f"└ Had hem al: **{already_had}**\n"
            f"└ Mislukt: **{failed}**",
            ephemeral=True
        )

    # =====================================================
    # /REACT GAME
    # =====================================================

    @game.command(
        name="add",
        description="Voeg een game-role toe"
    )
    async def game_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        buttonnaam: str,
        emoji: str | None = None
    ):
        await self.add_item_command(
            interaction,
            "game",
            role,
            buttonnaam,
            emoji
        )

    @game.command(
        name="edit",
        description="Pas een game-role aan"
    )
    async def game_edit(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        buttonnaam: str | None = None,
        emoji: str | None = None
    ):
        await self.edit_item_command(
            interaction,
            "game",
            role,
            buttonnaam,
            emoji
        )

    @game.command(
        name="remove",
        description="Verwijder een game-role uit het menu"
    )
    async def game_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role
    ):
        await self.remove_item_command(
            interaction,
            "game",
            role
        )

    @game.command(
        name="list",
        description="Bekijk alle game-rollen"
    )
    async def game_list(
        self,
        interaction: discord.Interaction
    ):
        await self.list_item_command(
            interaction,
            "game"
        )

    # =====================================================
    # /REACT GENRE
    # =====================================================

    @genre.command(
        name="add",
        description="Voeg een genre-role toe"
    )
    async def genre_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        buttonnaam: str,
        emoji: str | None = None
    ):
        await self.add_item_command(
            interaction,
            "genre",
            role,
            buttonnaam,
            emoji
        )

    @genre.command(
        name="edit",
        description="Pas een genre-role aan"
    )
    async def genre_edit(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        buttonnaam: str | None = None,
        emoji: str | None = None
    ):
        await self.edit_item_command(
            interaction,
            "genre",
            role,
            buttonnaam,
            emoji
        )

    @genre.command(
        name="remove",
        description="Verwijder een genre-role uit het menu"
    )
    async def genre_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role
    ):
        await self.remove_item_command(
            interaction,
            "genre",
            role
        )

    @genre.command(
        name="list",
        description="Bekijk alle genre-rollen"
    )
    async def genre_list(
        self,
        interaction: discord.Interaction
    ):
        await self.list_item_command(
            interaction,
            "genre"
        )

    # =====================================================
    # /REACT ROLE
    # =====================================================

    @role_group.command(
        name="add",
        description="Voeg een algemene role toe"
    )
    async def role_add(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        buttonnaam: str,
        emoji: str | None = None,
        level: app_commands.Range[int, 1, 999] = 1,
        group: str = "roles"
    ):
        await self.add_item_command(
            interaction,
            "role",
            role,
            buttonnaam,
            emoji,
            int(level),
            group
        )

    @role_group.command(
        name="edit",
        description="Pas een algemene role aan"
    )
    async def role_edit(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        buttonnaam: str | None = None,
        emoji: str | None = None,
        level: app_commands.Range[int, 1, 999] | None = None,
        group: str | None = None
    ):
        await self.edit_item_command(
            interaction,
            "role",
            role,
            buttonnaam,
            emoji,
            int(level) if level is not None else None,
            group
        )

    @role_group.command(
        name="remove",
        description="Verwijder een algemene role uit het menu"
    )
    async def role_remove(
        self,
        interaction: discord.Interaction,
        role: discord.Role
    ):
        await self.remove_item_command(
            interaction,
            "role",
            role
        )

    @role_group.command(
        name="list",
        description="Bekijk alle algemene rollen"
    )
    async def role_list(
        self,
        interaction: discord.Interaction
    ):
        await self.list_item_command(
            interaction,
            "role"
        )

    # =====================================================
    # COMPONENT INTERACTIONS
    # =====================================================

    @commands.Cog.listener()
    async def on_interaction(
        self,
        interaction: discord.Interaction
    ):

        if (
            interaction.type
            is not discord.InteractionType.component
        ):
            return

        data = interaction.data

        if not isinstance(
            data,
            dict
        ):
            return

        custom_id = str(
            data.get(
                "custom_id",
                ""
            )
        )

        # ---------------------------------------------
        # Open Games / Genres / Roles
        # ---------------------------------------------

        if custom_id.startswith(
            "react_open:"
        ):

            section = custom_id.split(
                ":",
                1
            )[1]

            if section not in {
                "game",
                "genre",
                "role",
            }:
                return

            if section == "role":
                await self.send_role_hub(interaction)
            else:
                await self.send_selector(interaction, section, 0)

            return

        # ---------------------------------------------
        # Open Roles -> Kleuren / Rollen
        # ---------------------------------------------

        if custom_id.startswith("react_subopen:"):
            section = custom_id.split(":", 1)[1]
            if section not in {"color", "roles"}:
                return
            await self.send_selector(interaction, section, 0)
            return

        # ---------------------------------------------
        # Pagination
        # ---------------------------------------------

        if custom_id.startswith(
            "react_page:"
        ):

            try:
                _, section, page = custom_id.split(
                    ":",
                    2
                )

                page = int(
                    page
                )

            except (
                ValueError,
                IndexError
            ):
                return

            if section not in {
                "game",
                "genre",
                "role",
                "color",
                "roles",
            }:
                return

            await self.edit_selector(
                interaction,
                section,
                page
            )

            return

        # ---------------------------------------------
        # Toggle role
        # ---------------------------------------------

        if custom_id.startswith(
            "react_toggle:"
        ):

            try:
                _, section, role_id, page = custom_id.split(
                    ":",
                    3
                )

                role_id = int(
                    role_id
                )

                page = int(
                    page
                )

            except (
                ValueError,
                IndexError
            ):
                return

            if section not in {
                "game",
                "genre",
                "role",
                "color",
                "roles",
            }:
                return

            if interaction.guild is None:
                return

            member = interaction.user

            if not isinstance(
                member,
                discord.Member
            ):
                return

            # Virtuele "Geen kleur"-knop.
            if section == "color" and role_id == 0:
                color_entries = await self.get_entries(interaction.guild, "color")
                removable = []
                for entry in color_entries:
                    color_role = interaction.guild.get_role(entry["role_id"])
                    if color_role is not None and color_role in member.roles:
                        removable.append(color_role)
                if removable:
                    try:
                        await member.remove_roles(*removable, reason="React Roles - Geen kleur")
                    except (discord.Forbidden, discord.HTTPException):
                        await interaction.response.send_message(
                            "❌ Ik kon je huidige kleur niet verwijderen.", ephemeral=True
                        )
                        return
                await self.edit_selector(interaction, section, page)
                return

            role = interaction.guild.get_role(role_id)
            if role is None:
                await interaction.response.send_message(
                    "❌ Deze rol bestaat niet meer.", ephemeral=True
                )
                return

            # Lees de ingestelde level/group rechtstreeks uit het menu-item.
            db_section = "role" if section in {"color", "roles"} else section
            async with aiosqlite.connect(DATABASE_PATH) as db:
                cur = await db.execute(
                    "SELECT min_level,item_group,button_name FROM react_items "
                    "WHERE guild_id=? AND section=? AND role_id=?",
                    (interaction.guild.id, db_section, role_id)
                )
                item = await cur.fetchone()
            if item is None:
                await interaction.response.send_message(
                    "❌ Deze optie staat niet meer in React Roles.", ephemeral=True
                )
                return

            min_level, item_group, button_name = int(item[0] or 1), (item[1] or "roles").lower(), item[2]
            is_admin_color = str(button_name).strip().lower() == "admin kleur"

            if is_admin_color:
                admin_access_role = interaction.guild.get_role(ADMIN_ACCESS_ROLE_ID)
                if admin_access_role is None or admin_access_role not in member.roles:
                    await interaction.response.send_message(
                        "❌ Admin Kleur is alleen beschikbaar voor admins.", ephemeral=True
                    )
                    return
            elif section in {"color", "roles"}:
                member_level = await self.get_member_level(member)
                if member_level < min_level:
                    await interaction.response.send_message(
                        f"🔒 Hiervoor heb je **level {min_level}** nodig. Je bent nu level **{member_level}**.",
                        ephemeral=True
                    )
                    return

            bot_member = interaction.guild.me

            if (
                bot_member is None
                or
                role >= bot_member.top_role
            ):

                await interaction.response.send_message(
                    "❌ Ik kan deze rol momenteel niet beheren.",
                    ephemeral=True
                )

                return

            try:

                if role in member.roles:

                    await member.remove_roles(
                        role,
                        reason="React Roles"
                    )

                else:
                    # Alle color-items, inclusief Admin Kleur, zijn exclusief.
                    if section == "color" or item_group == "color":
                        color_entries = await self.get_entries(interaction.guild, "color")
                        old_colors = []
                        for entry in color_entries:
                            other = interaction.guild.get_role(entry["role_id"])
                            if other is not None and other != role and other in member.roles:
                                old_colors.append(other)
                        if old_colors:
                            await member.remove_roles(*old_colors, reason="React Roles - kleur wisselen")

                    await member.add_roles(role, reason="React Roles")

            except discord.Forbidden:

                await interaction.response.send_message(
                    "❌ Ik heb geen toestemming om deze rol te beheren.",
                    ephemeral=True
                )

                return

            except discord.HTTPException as exc:

                await interaction.response.send_message(
                    f"❌ Discord kon de rol niet aanpassen: `{exc}`",
                    ephemeral=True
                )

                return

            # Update direct dezelfde ephemeral pagina.
            await self.edit_selector(
                interaction,
                section,
                page
            )


# =========================================================
# SETUP
# =========================================================

async def setup(
    bot: commands.Bot
):

    await bot.add_cog(
        ReactRoles(bot)
    )
