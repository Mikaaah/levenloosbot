from __future__ import annotations

import random
from dataclasses import dataclass

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands


DATABASE_PATH = "levenloos.db"
BRAND_COLOR = 0x6C27DA

DEFAULT_WELCOME_CHANNEL_ID = 940328182471602256
DEFAULT_INFO_CHANNEL_ID = 940328225568079923
DEFAULT_REACT_ROLES_CHANNEL_ID = 940376085865037884

DEFAULT_JOIN_ROLE_IDS = (
    1080845388384309268,
    1080845392150798377,
    1080845390313697330,
    940331028344619110,
)

WELCOME_BADGE_URL = "https://i.imgur.com/YXFAOyd.png"

DEFAULT_WELCOME_TITLE = "WELKOM BIJ LEVENLOOS"
DEFAULT_WELCOME_MESSAGE = (
    "Welkom {member}! Je bent onze **{member_count}e speler**."
)
DEFAULT_INFO_MESSAGE = "Lees {info_channel} voor de serverinfo en regels."
DEFAULT_ROLES_MESSAGE = (
    "Ga naar {roles_channel} om je games, genres en rollen te kiezen."
)
DEFAULT_LEAVE_MESSAGE = "👋 **{member_name}** heeft LEVENLOOS verlaten."

WAVE_MESSAGES = [
    "👋 {user} zwaait naar {member}!",
    "👋 {user} heet {member} welkom!",
    "👋 {user} komt even hallo zeggen tegen {member}.",
    "👋 {user} zwaait vrolijk naar {member}!",
    "👋 {user} verwelkomt {member} bij LEVENLOOS.",
    "👋 {user} kwam even langs om {member} welkom te heten.",
    "👋 {user} zegt hoi tegen {member}!",
    "👋 {user} heeft {member} gespot.",
    "👋 {user} sluit zich aan bij het welkomstcomité.",
    "👋 {user} is er ook even bij.",
    "👋 {user} heet onze nieuwe speler {member} welkom!",
    "👋 {user} komt even buurten bij {member}!",
    "👋 {user} geeft {member} een zwaai.",
    "👋 {user} verwelkomt {member} op de server.",
    "👋 {user} zegt even welkom tegen {member}.",
    "👋 {user} zag een nieuwe naam verschijnen: {member}.",
    "🧟 {user} checkt voor de zekerheid of {member} gebeten is.",
    "⛏️ {user} laat alvast wat diamonds over voor {member}.",
    "🦖 {user} maakt alvast plek voor de dino van {member}.",
    "⚡ {user} vraagt zich af welke starter {member} kiest.",
    "🚗 {user} stuurt de bal alvast richting {member}.",
    "🏭 {user} zet {member} alvast aan het werk in de fabriek.",
    "🌊 {user} hoopt dat {member} genoeg zuurstof heeft meegenomen.",
    "🔦 {user} heeft voor de zekerheid een zaklamp meegenomen voor {member}.",
    "👀 {user} vindt {member} een beetje sus.",
    "🪂 {user} laat {member} voor deze keer Jumpmaster zijn.",
    "🚀 {user} meldt {member} aan voor de volgende extractie.",
    "🛡️ {user} heeft nog wel een plekje in de squad voor {member}.",
    "🌾 {user} houdt een stukje farmland vrij voor {member}.",
    "🎣 {user} vist {member} uit de rivier.",
    "⚽ {user} zet {member} alvast in de basis.",
    "🔫 {user} bewaart wat ammo voor {member}.",
    "🧱 {user} zet alvast een crafting table neer voor {member}.",
    "🗺️ {user} zet {member} alvast op de map.",
    "🎮 {user} heeft alvast een plek in de party voor {member}.",
    "🧰 {user} bewaart wat loot voor {member}.",
    "🎲 {user} rolt een NAT 20 om {member} welkom te heten.",
    "🎲 {user} rolt initiative voor {member}.",
    "🐉 {user} houdt alvast een stoel aan tafel vrij voor {member}.",
    "🧙 {user} geeft {member} voor deze keer advantage.",
    "📜 {user} zet {member} alvast op de character sheet.",
    "🗡️ {user} schuift {member} een extra set dobbelstenen toe.",
    "🃏 {user} schudt de kaarten alvast voor {member}.",
    "♟️ {user} maakt alvast ruimte op het bord voor {member}.",
    "🎲 {user} hoopt dat de eerste roll van {member} beter is dan een NAT 1.",
    "🏰 {user} reserveert alvast een plek in de party voor {member}.",
    "📖 {user} slaat een nieuwe pagina open voor {member}.",
    "🧩 {user} legt alvast een extra speelstuk klaar voor {member}.",
]


@dataclass(slots=True)
class WelcomeConfig:
    guild_id: int
    welcome_channel_id: int
    info_channel_id: int
    react_roles_channel_id: int
    welcome_title: str
    welcome_message: str
    info_message: str
    roles_message: str
    leave_message: str


def owner_only(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and interaction.guild.owner_id == interaction.user.id
    )


class WaveButton(discord.ui.Button):
    def __init__(self, cog: "Welcome", member_id: int):
        super().__init__(
            label="Zwaai!",
            emoji="👋",
            style=discord.ButtonStyle.success,
            custom_id=f"welcome_wave:{member_id}",
        )
        self.cog = cog
        self.member_id = member_id

    async def callback(self, interaction: discord.Interaction):
        await self.cog.handle_wave(interaction, self.member_id)


class WelcomeView(discord.ui.View):
    def __init__(self, cog: "Welcome", member_id: int):
        super().__init__(timeout=None)
        self.add_item(WaveButton(cog, member_id))


class WelcomeTextModal(discord.ui.Modal, title="Welkomstteksten aanpassen"):
    welcome_title = discord.ui.TextInput(
        label="Titel",
        max_length=80,
        required=True,
    )
    welcome_message = discord.ui.TextInput(
        label="Welkomstbericht",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )
    info_message = discord.ui.TextInput(
        label="Info & regels tekst",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )
    roles_message = discord.ui.TextInput(
        label="React Roles tekst",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, cog: "Welcome", guild_id: int, config: WelcomeConfig):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id

        self.welcome_title.default = config.welcome_title
        self.welcome_message.default = config.welcome_message
        self.info_message.default = config.info_message
        self.roles_message.default = config.roles_message

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.update_config(
            self.guild_id,
            welcome_title=str(self.welcome_title),
            welcome_message=str(self.welcome_message),
            info_message=str(self.info_message),
            roles_message=str(self.roles_message),
        )
        await interaction.response.send_message(
            "✅ Welkomstteksten zijn opgeslagen.",
            ephemeral=True,
        )


class LeaveTextModal(discord.ui.Modal, title="Vertrekbericht aanpassen"):
    leave_message = discord.ui.TextInput(
        label="Vertrekbericht",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, cog: "Welcome", guild_id: int, current: str):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.leave_message.default = current

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.update_config(
            self.guild_id,
            leave_message=str(self.leave_message),
        )
        await interaction.response.send_message(
            "✅ Vertrekbericht is opgeslagen.",
            ephemeral=True,
        )


class WelcomeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog: "Welcome", owner_id: int, guild_id: int, kind: str):
        labels = {
            "welcome": "Kies het welkomstkanaal...",
            "info": "Kies Info & Regels...",
            "roles": "Kies React Roles...",
        }
        super().__init__(
            placeholder=labels[kind],
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.kind = kind

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Dit menu is niet van jou.",
                ephemeral=True,
            )
            return

        channel = self.values[0]
        column = {
            "welcome": "welcome_channel_id",
            "info": "info_channel_id",
            "roles": "react_roles_channel_id",
        }[self.kind]

        await self.cog.update_config(
            self.guild_id,
            **{column: channel.id},
        )
        await interaction.response.send_message(
            f"✅ Kanaal aangepast naar {channel.mention}.",
            ephemeral=True,
        )


class ChannelConfigView(discord.ui.View):
    def __init__(self, cog: "Welcome", owner_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.add_item(
            WelcomeChannelSelect(cog, owner_id, guild_id, "welcome")
        )
        self.add_item(
            WelcomeChannelSelect(cog, owner_id, guild_id, "info")
        )
        self.add_item(
            WelcomeChannelSelect(cog, owner_id, guild_id, "roles")
        )


class JoinRoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog: "Welcome", owner_id: int, guild_id: int):
        super().__init__(
            placeholder="Kies een join-rol om toe te voegen/verwijderen...",
            min_values=1,
            max_values=1,
        )
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Dit menu is niet van jou.",
                ephemeral=True,
            )
            return

        role = self.values[0]

        if role.is_default() or role.managed:
            await interaction.response.send_message(
                "❌ Deze rol kan niet als automatische join-rol worden gebruikt.",
                ephemeral=True,
            )
            return

        added = await self.cog.toggle_join_role(
            self.guild_id,
            role.id,
        )
        state = "toegevoegd aan" if added else "verwijderd uit"

        await interaction.response.send_message(
            f"✅ {role.mention} is {state} de automatische join-rollen.",
            ephemeral=True,
        )


class JoinRolesView(discord.ui.View):
    def __init__(self, cog: "Welcome", owner_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.add_item(JoinRoleSelect(cog, owner_id, guild_id))


class WelcomeConfigView(discord.ui.View):
    def __init__(self, cog: "Welcome", owner_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Dit configpanel is niet van jou.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(
        label="Teksten",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
    )
    async def texts(self, interaction: discord.Interaction, _):
        config = await self.cog.get_config(self.guild_id)
        await interaction.response.send_modal(
            WelcomeTextModal(self.cog, self.guild_id, config)
        )

    @discord.ui.button(
        label="Kanalen",
        emoji="📍",
        style=discord.ButtonStyle.secondary,
    )
    async def channels(self, interaction: discord.Interaction, _):
        await interaction.response.send_message(
            "Kies hieronder welk kanaal je wilt aanpassen.",
            view=ChannelConfigView(
                self.cog,
                self.owner_id,
                self.guild_id,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Join rollen",
        emoji="🎭",
        style=discord.ButtonStyle.secondary,
    )
    async def roles(self, interaction: discord.Interaction, _):
        roles = await self.cog.get_join_roles(self.guild_id)
        role_text = (
            "\n".join(f"• <@&{role_id}>" for role_id in roles)
            if roles
            else "Geen join-rollen ingesteld."
        )
        await interaction.response.send_message(
            f"**Automatische join-rollen**\n\n{role_text}\n\n"
            "Selecteer een rol om hem toe te voegen of te verwijderen.",
            view=JoinRolesView(
                self.cog,
                self.owner_id,
                self.guild_id,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Vertrektekst",
        emoji="👋",
        style=discord.ButtonStyle.secondary,
    )
    async def leave_text(self, interaction: discord.Interaction, _):
        config = await self.cog.get_config(self.guild_id)
        await interaction.response.send_modal(
            LeaveTextModal(
                self.cog,
                self.guild_id,
                config.leave_message,
            )
        )

    @discord.ui.button(
        label="Preview",
        emoji="👁️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def preview(self, interaction: discord.Interaction, _):
        if interaction.guild is None:
            return

        embed = await self.cog.build_welcome_embed(
            interaction.guild,
            interaction.user,
        )
        await interaction.response.send_message(
            embed=embed,
            view=WelcomeView(self.cog, interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Reset standaard",
        emoji="♻️",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def reset(self, interaction: discord.Interaction, _):
        await self.cog.reset_config(self.guild_id)
        await interaction.response.send_message(
            "✅ Welkomstconfig is teruggezet naar de standaardwaarden.",
            ephemeral=True,
        )


class Welcome(commands.Cog):
    welkom = app_commands.Group(
        name="welkom",
        description="Welkomstsysteem van LEVENLOOS.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await self.init_db()

    async def init_db(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS welcome_waves (
                    guild_id INTEGER NOT NULL,
                    welcomed_member_id INTEGER NOT NULL,
                    waving_user_id INTEGER NOT NULL,
                    PRIMARY KEY (
                        guild_id,
                        welcomed_member_id,
                        waving_user_id
                    )
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS welcome_config (
                    guild_id INTEGER PRIMARY KEY,
                    welcome_channel_id INTEGER NOT NULL,
                    info_channel_id INTEGER NOT NULL,
                    react_roles_channel_id INTEGER NOT NULL,
                    welcome_title TEXT NOT NULL,
                    welcome_message TEXT NOT NULL,
                    info_message TEXT NOT NULL,
                    roles_message TEXT NOT NULL,
                    leave_message TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS welcome_join_roles (
                    guild_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, role_id)
                )
                """
            )
            await db.commit()

    async def ensure_config(self, guild_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO welcome_config (
                    guild_id,
                    welcome_channel_id,
                    info_channel_id,
                    react_roles_channel_id,
                    welcome_title,
                    welcome_message,
                    info_message,
                    roles_message,
                    leave_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    DEFAULT_WELCOME_CHANNEL_ID,
                    DEFAULT_INFO_CHANNEL_ID,
                    DEFAULT_REACT_ROLES_CHANNEL_ID,
                    DEFAULT_WELCOME_TITLE,
                    DEFAULT_WELCOME_MESSAGE,
                    DEFAULT_INFO_MESSAGE,
                    DEFAULT_ROLES_MESSAGE,
                    DEFAULT_LEAVE_MESSAGE,
                ),
            )

            cursor = await db.execute(
                """
                SELECT COUNT(*)
                FROM welcome_join_roles
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            count = (await cursor.fetchone())[0]

            if count == 0:
                await db.executemany(
                    """
                    INSERT OR IGNORE INTO welcome_join_roles (
                        guild_id,
                        role_id
                    )
                    VALUES (?, ?)
                    """,
                    [
                        (guild_id, role_id)
                        for role_id in DEFAULT_JOIN_ROLE_IDS
                    ],
                )

            await db.commit()

    async def get_config(self, guild_id: int) -> WelcomeConfig:
        await self.ensure_config(guild_id)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                """
                SELECT
                    guild_id,
                    welcome_channel_id,
                    info_channel_id,
                    react_roles_channel_id,
                    welcome_title,
                    welcome_message,
                    info_message,
                    roles_message,
                    leave_message
                FROM welcome_config
                WHERE guild_id = ?
                """,
                (guild_id,),
            )
            row = await cursor.fetchone()

        return WelcomeConfig(*row)

    async def update_config(self, guild_id: int, **changes):
        allowed = {
            "welcome_channel_id",
            "info_channel_id",
            "react_roles_channel_id",
            "welcome_title",
            "welcome_message",
            "info_message",
            "roles_message",
            "leave_message",
        }
        clean = {
            key: value
            for key, value in changes.items()
            if key in allowed
        }

        if not clean:
            return

        await self.ensure_config(guild_id)
        assignments = ", ".join(f"{key} = ?" for key in clean)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                f"""
                UPDATE welcome_config
                SET {assignments}
                WHERE guild_id = ?
                """,
                (*clean.values(), guild_id),
            )
            await db.commit()

    async def reset_config(self, guild_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "DELETE FROM welcome_config WHERE guild_id = ?",
                (guild_id,),
            )
            await db.execute(
                "DELETE FROM welcome_join_roles WHERE guild_id = ?",
                (guild_id,),
            )
            await db.commit()

        await self.ensure_config(guild_id)

    async def get_join_roles(self, guild_id: int) -> list[int]:
        await self.ensure_config(guild_id)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                """
                SELECT role_id
                FROM welcome_join_roles
                WHERE guild_id = ?
                ORDER BY role_id
                """,
                (guild_id,),
            )
            return [row[0] for row in await cursor.fetchall()]

    async def toggle_join_role(self, guild_id: int, role_id: int) -> bool:
        await self.ensure_config(guild_id)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                """
                SELECT 1
                FROM welcome_join_roles
                WHERE guild_id = ? AND role_id = ?
                """,
                (guild_id, role_id),
            )

            if await cursor.fetchone():
                await db.execute(
                    """
                    DELETE FROM welcome_join_roles
                    WHERE guild_id = ? AND role_id = ?
                    """,
                    (guild_id, role_id),
                )
                added = False
            else:
                await db.execute(
                    """
                    INSERT INTO welcome_join_roles (guild_id, role_id)
                    VALUES (?, ?)
                    """,
                    (guild_id, role_id),
                )
                added = True

            await db.commit()

        return added

    @staticmethod
    def format_template(
        template: str,
        *,
        member: discord.abc.User,
        member_count: int,
        info_channel_id: int,
        roles_channel_id: int,
    ) -> str:
        replacements = {
            "{member}": member.mention,
            "{member_name}": member.display_name,
            "{member_count}": str(member_count),
            "{info_channel}": f"<#{info_channel_id}>",
            "{roles_channel}": f"<#{roles_channel_id}>",
        }

        for key, value in replacements.items():
            template = template.replace(key, value)

        return template

    async def build_welcome_embed(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
    ) -> discord.Embed:
        config = await self.get_config(guild.id)
        member_count = guild.member_count or 0

        welcome_text = self.format_template(
            config.welcome_message,
            member=member,
            member_count=member_count,
            info_channel_id=config.info_channel_id,
            roles_channel_id=config.react_roles_channel_id,
        )
        info_text = self.format_template(
            config.info_message,
            member=member,
            member_count=member_count,
            info_channel_id=config.info_channel_id,
            roles_channel_id=config.react_roles_channel_id,
        )
        roles_text = self.format_template(
            config.roles_message,
            member=member,
            member_count=member_count,
            info_channel_id=config.info_channel_id,
            roles_channel_id=config.react_roles_channel_id,
        )

        embed = discord.Embed(
            title=config.welcome_title,
            description=welcome_text,
            color=BRAND_COLOR,
        )
        embed.add_field(
            name="INFO & REGELS",
            value=info_text,
            inline=False,
        )
        embed.add_field(
            name="REACT ROLES",
            value=roles_text,
            inline=False,
        )
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="LEVENLOOS • Welkom!")

        return embed

    def build_badge_embed(self) -> discord.Embed:
        badge_embed = discord.Embed(color=BRAND_COLOR)
        badge_embed.set_image(url=WELCOME_BADGE_URL)
        return badge_embed

    async def handle_wave(
        self,
        interaction: discord.Interaction,
        member_id: int,
    ):
        guild = interaction.guild

        if guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "❌ Dit werkt alleen in de server.",
                ephemeral=True,
            )
            return

        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute(
                    """
                    INSERT INTO welcome_waves (
                        guild_id,
                        welcomed_member_id,
                        waving_user_id
                    )
                    VALUES (?, ?, ?)
                    """,
                    (guild.id, member_id, interaction.user.id),
                )
                await db.commit()
        except aiosqlite.IntegrityError:
            await interaction.response.send_message(
                f"👋 Je hebt <@{member_id}> al welkom geheten!",
                ephemeral=True,
            )
            return

        member = guild.get_member(member_id)
        member_text = member.mention if member else f"<@{member_id}>"
        wave_message = random.choice(WAVE_MESSAGES).format(
            user=interaction.user.mention,
            member=member_text,
        )

        await interaction.response.defer()
        await interaction.channel.send(
            wave_message,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        levels = self.bot.get_cog("Levels")
        if levels is not None:
            try:
                await levels.award_source(
                    guild,
                    interaction.user.id,
                    "wave",
                    f"welcome_wave:{member_id}:{interaction.user.id}",
                    member_id,
                )
            except Exception as error:
                print(f"[WELCOME XP] {error}")

    @welkom.command(
        name="config",
        description="Open het welkomstconfiguratiepaneel.",
    )
    async def config_command(self, interaction: discord.Interaction):
        if not owner_only(interaction):
            await interaction.response.send_message(
                "❌ Alleen de server owner kan dit configureren.",
                ephemeral=True,
            )
            return

        config = await self.get_config(interaction.guild.id)
        roles = await self.get_join_roles(interaction.guild.id)

        embed = discord.Embed(
            title="⚙️ Welkom Config",
            description=(
                "Beheer hier het welkomstsysteem.\n\n"
                f"**Welkomstkanaal:** <#{config.welcome_channel_id}>\n"
                f"**Info & Regels:** <#{config.info_channel_id}>\n"
                f"**React Roles:** <#{config.react_roles_channel_id}>\n"
                f"**Join-rollen:** {len(roles)}\n\n"
                "**Beschikbare placeholders**\n"
                "`{member}` • `{member_name}` • `{member_count}`\n"
                "`{info_channel}` • `{roles_channel}`"
            ),
            color=BRAND_COLOR,
        )

        await interaction.response.send_message(
            embed=embed,
            view=WelcomeConfigView(
                self,
                interaction.user.id,
                interaction.guild.id,
            ),
            ephemeral=True,
        )

    @welkom.command(
        name="test",
        description="Stuur een test van het welkomstbericht.",
    )
    async def test_command(self, interaction: discord.Interaction):
        if not owner_only(interaction):
            await interaction.response.send_message(
                "❌ Alleen de server owner kan dit testen.",
                ephemeral=True,
            )
            return

        config = await self.get_config(interaction.guild.id)
        channel = interaction.guild.get_channel(
            config.welcome_channel_id
        )

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Het welkomstkanaal kon niet worden gevonden.",
                ephemeral=True,
            )
            return

        embed = await self.build_welcome_embed(
            interaction.guild,
            interaction.user,
        )

        badge_embed = self.build_badge_embed()

        await channel.send(
            embeds=[badge_embed, embed],
            view=WelcomeView(self, interaction.user.id),
        )

        await interaction.response.send_message(
            f"✅ Welkomsttest verstuurd in {channel.mention}.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        config = await self.get_config(member.guild.id)
        role_ids = await self.get_join_roles(member.guild.id)
        roles = [
            role
            for role_id in role_ids
            if (role := member.guild.get_role(role_id)) is not None
            and not role.managed
        ]

        if roles:
            try:
                await member.add_roles(
                    *roles,
                    reason="Automatische LEVENLOOS welkomstrollen",
                )
            except discord.Forbidden:
                print(
                    f"[WELCOME] Kan join-rollen niet geven aan "
                    f"{member} ({member.id}): onvoldoende rechten/rolpositie."
                )
            except discord.HTTPException as error:
                print(f"[WELCOME] Join-rollen fout: {error}")

        channel = member.guild.get_channel(
            config.welcome_channel_id
        )

        if not isinstance(channel, discord.TextChannel):
            return

        embed = await self.build_welcome_embed(
            member.guild,
            member,
        )

        badge_embed = self.build_badge_embed()

        await channel.send(
            embeds=[badge_embed, embed],
            view=WelcomeView(self, member.id),
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        config = await self.get_config(member.guild.id)
        channel = member.guild.get_channel(
            config.welcome_channel_id
        )

        if not isinstance(channel, discord.TextChannel):
            return

        text = self.format_template(
            config.leave_message,
            member=member,
            member_count=member.guild.member_count or 0,
            info_channel_id=config.info_channel_id,
            roles_channel_id=config.react_roles_channel_id,
        )

        await channel.send(text)

    @app_commands.command(
        name="roleall",
        description="Geef één rol aan alle serverleden.",
    )
    @app_commands.describe(
        rolnaam="De rol die je aan alle leden wilt geven.",
    )
    async def roleall_command(
        self,
        interaction: discord.Interaction,
        rolnaam: discord.Role,
    ):
        if not owner_only(interaction):
            await interaction.response.send_message(
                "❌ Alleen de server owner kan `/roleall` gebruiken.",
                ephemeral=True,
            )
            return

        if rolnaam.is_default():
            await interaction.response.send_message(
                "❌ @everyone kan niet worden uitgedeeld.",
                ephemeral=True,
            )
            return

        if rolnaam.managed:
            await interaction.response.send_message(
                "❌ Deze rol wordt door Discord/integraties beheerd.",
                ephemeral=True,
            )
            return

        bot_member = interaction.guild.me
        if bot_member is None or rolnaam >= bot_member.top_role:
            await interaction.response.send_message(
                "❌ Mijn botrol staat niet hoog genoeg om deze rol uit te delen.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        added = 0
        already_had = 0
        failed = 0

        for member in interaction.guild.members:
            if rolnaam in member.roles:
                already_had += 1
                continue

            try:
                await member.add_roles(
                    rolnaam,
                    reason=f"/roleall door server owner {interaction.user}",
                )
                added += 1
            except (discord.Forbidden, discord.HTTPException):
                failed += 1

        await interaction.followup.send(
            (
                f"✅ **/roleall voltooid voor {rolnaam.mention}**\n\n"
                f"Toegevoegd: **{added}**\n"
                f"Hadden de rol al: **{already_had}**\n"
                f"Mislukt: **{failed}**"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
