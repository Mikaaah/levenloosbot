import random

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# CONFIG
# =========================================================

WELCOME_CHANNEL_ID = 940328182471602256
INFO_RULES_CHANNEL_ID = 940328225568079923
REACT_ROLES_CHANNEL_ID = 940376085865037884
DATABASE_PATH = "levenloos.db"

# Basisrollen die iedere nieuwe gebruiker automatisch krijgt
WELCOME_ROLE_IDS = [
    1080845388384309268,
    1080845392150798377,
    1080845390313697330,
    940331028344619110,
]

# WELKOM badge - custom Discord emoji's w1 t/m w8
WELCOME_BADGE = (
    "<:w1:1546398627293962311>"
    "<:w2:1546398847981330432>"
    "<:w3:1546398880441045014>"
    "<:w4:1546398906638934136>"
    "<:w5:1546398925165166763>"
    "<:w6:1546398948728504330>"
    "<:w7:1546398977421877269>"
    "<:w8:1546399002126450718>"
)


# =========================================================
# WAVE MESSAGES
# =========================================================

WAVE_MESSAGES = [
    # Gewoon normaal
    '👋 {user} zwaait naar {member}!',
    '👋 {user} heet {member} welkom!',
    '👋 {user} komt even hallo zeggen tegen {member}.',
    '👋 {user} zwaait vrolijk naar {member}!',
    '👋 {user} verwelkomt {member} bij LEVENLOOS.',
    '👋 {user} kwam even langs om {member} welkom te heten.',
    '👋 {user} zegt hoi tegen {member}!',
    '👋 {user} heeft {member} gespot.',
    '👋 {user} sluit zich aan bij het welkomstcomité.',
    '👋 {user} is er ook even bij.',
    '👋 {user} heet onze nieuwe speler {member} welkom!',
    '👋 {user} komt even buurten bij {member}!',
    '👋 {user} geeft {member} een zwaai.',
    '👋 {user} verwelkomt {member} op de server.',
    '👋 {user} zegt even welkom tegen {member}.',
    '👋 {user} zag een nieuwe naam verschijnen: {member}.',

    # Games - subtiele references
    '🧟 {user} checkt voor de zekerheid of {member} gebeten is.',
    '⛏️ {user} laat alvast wat diamonds over voor {member}.',
    '🦖 {user} maakt alvast plek voor de dino van {member}.',
    '⚡ {user} vraagt zich af welke starter {member} kiest.',
    '🚗 {user} stuurt de bal alvast richting {member}.',
    '🏭 {user} zet {member} alvast aan het werk in de fabriek.',
    '🌊 {user} hoopt dat {member} genoeg zuurstof heeft meegenomen.',
    '🔦 {user} heeft voor de zekerheid een zaklamp meegenomen voor {member}.',
    '👀 {user} vindt {member} een beetje sus.',
    '🪂 {user} laat {member} voor deze keer Jumpmaster zijn.',
    '🚀 {user} meldt {member} aan voor de volgende extractie.',
    '🛡️ {user} heeft nog wel een plekje in de squad voor {member}.',
    '🌾 {user} houdt een stukje farmland vrij voor {member}.',
    '🎣 {user} vist {member} uit de rivier.',
    '⚽ {user} zet {member} alvast in de basis.',
    '🔫 {user} bewaart wat ammo voor {member}.',
    '🧱 {user} zet alvast een crafting table neer voor {member}.',
    '🗺️ {user} zet {member} alvast op de map.',
    '🎮 {user} heeft alvast een plek in de party voor {member}.',
    '🧰 {user} bewaart wat loot voor {member}.',

    # Tabletop / D&D / bordspellen
    '🎲 {user} rolt een NAT 20 om {member} welkom te heten.',
    '🎲 {user} rolt initiative voor {member}.',
    '🐉 {user} houdt alvast een stoel aan tafel vrij voor {member}.',
    '🧙 {user} geeft {member} voor deze keer advantage.',
    '📜 {user} zet {member} alvast op de character sheet.',
    '🗡️ {user} schuift {member} een extra set dobbelstenen toe.',
    '🃏 {user} schudt de kaarten alvast voor {member}.',
    '♟️ {user} maakt alvast ruimte op het bord voor {member}.',
    '🎲 {user} hoopt dat de eerste roll van {member} beter is dan een NAT 1.',
    '🏰 {user} reserveert alvast een plek in de party voor {member}.',
    '📖 {user} slaat een nieuwe pagina open voor {member}.',
    '🧩 {user} legt alvast een extra speelstuk klaar voor {member}.',
]



# =========================================================
# HELPERS
# =========================================================

# =========================================================
# WAVE BUTTON
# =========================================================

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


# =========================================================
# COG
# =========================================================

class Welcome(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS welcome_waves (
                    guild_id INTEGER NOT NULL,
                    welcomed_member_id INTEGER NOT NULL,
                    waving_user_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, welcomed_member_id, waving_user_id)
                )
                """
            )
            await db.commit()

    async def handle_wave(self, interaction: discord.Interaction, member_id: int):
        guild = interaction.guild

        if guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "❌ Dit werkt alleen in de server.",
                ephemeral=True,
            )
            return

        # Reserveer de zwaai direct in SQLite. De UNIQUE/PRIMARY KEY voorkomt
        # dubbele zwaaien, ook wanneer twee klikken bijna tegelijk binnenkomen.
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute(
                    """
                    INSERT INTO welcome_waves (
                        guild_id, welcomed_member_id, waving_user_id
                    ) VALUES (?, ?, ?)
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

        # Eerst de knop-interactie stil bevestigen. Daarna een LOS bericht sturen,
        # zodat Discord geen preview/reply van het welkomstbericht toont.
        await interaction.response.defer()
        await interaction.channel.send(
            wave_message,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        # Levels-koppeling: alleen de eerste geldige zwaai levert XP op.
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

    welkom = app_commands.Group(
        name="welkom",
        description="Welkomstsysteem van LEVENLOOS.",
    )

    @welkom.command(name="test", description="Stuur een test van het welkomstbericht.")
    async def welkom_test(self, interaction: discord.Interaction):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Dit werkt alleen in de server.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(WELCOME_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Het welcome-kanaal kon niet worden gevonden.",
                ephemeral=True,
            )
            return

        member = interaction.user
        member_count = interaction.guild.member_count or 0

        message = (
            f"# {WELCOME_BADGE}\n\n"
            f"> **WELKOM BIJ LEVENLOOS**\n"
            f"> └ Welkom {member.mention}! Je bent onze "
            f"**{member_count}e speler**.\n\n"
            f"> **INFO & REGELS**\n"
            f"> └ Lees <#{INFO_RULES_CHANNEL_ID}> voor de serverinfo en regels.\n\n"
            f"> **REACT ROLES**\n"
            f"> └ Ga naar <#{REACT_ROLES_CHANNEL_ID}> om je games, genres en rollen te kiezen."
        )

        await channel.send(
            message,
            view=WelcomeView(self, member.id),
        )

        await interaction.response.send_message(
            f"✅ Welkomsttest verstuurd in {channel.mention}.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        roles = [
            role
            for role_id in WELCOME_ROLE_IDS
            if (role := member.guild.get_role(role_id)) is not None
        ]

        if roles:
            try:
                await member.add_roles(
                    *roles,
                    reason="Automatische LEVENLOOS welkomstrollen",
                )
            except discord.Forbidden:
                print(
                    f"[WELCOME] Kan basisrollen niet geven aan "
                    f"{member} ({member.id}): onvoldoende rechten/rolpositie."
                )
            except discord.HTTPException as error:
                print(
                    f"[WELCOME] Fout bij geven basisrollen aan "
                    f"{member} ({member.id}): {error}"
                )

        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)

        if not isinstance(channel, discord.TextChannel):
            return

        member_count = member.guild.member_count or 0
        badge = WELCOME_BADGE

        message = (
            f"# {badge}\n\n"
            f"> **WELKOM BIJ LEVENLOOS**\n"
            f"> └ Welkom {member.mention}! Je bent onze "
            f"**{member_count}e speler**.\n\n"
            f"> **INFO & REGELS**\n"
            f"> └ Lees <#{INFO_RULES_CHANNEL_ID}> voor de serverinfo en regels.\n\n"
            f"> **REACT ROLES**\n"
            f"> └ Ga naar <#{REACT_ROLES_CHANNEL_ID}> om je games, genres en rollen te kiezen."
        )

        await channel.send(
            message,
            view=WelcomeView(self, member.id),
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)

        if not isinstance(channel, discord.TextChannel):
            return

        await channel.send(
            f"👋 **{member.display_name}** heeft LEVENLOOS verlaten."
        )


# =========================================================
# SETUP
# =========================================================

async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
