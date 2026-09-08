import re

import discord
from discord import app_commands
from discord.ext import commands

ANNOUNCEMENT_CHANNEL_ID = 1546644553409495040
DEFAULT_COLOR = 0x6C27DA


def is_admin(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id or member.guild_permissions.administrator:
        return True
    return any(role.name.lower() in {"admin", "owner"} for role in member.roles)


def parse_color(value: str) -> discord.Color:
    value = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Ongeldige hexkleur")
    return discord.Color(int(value, 16))


def build_embed(raw: str, color: discord.Color) -> discord.Embed:
    """Simple paste format: first '# ' line = title, '## ' lines = fields."""
    lines = raw.replace("\r\n", "\n").strip().split("\n")
    title = "Mededeling"
    description_lines: list[str] = []
    fields: list[tuple[str, str]] = []
    current_field: str | None = None
    current_lines: list[str] = []

    def flush_field() -> None:
        nonlocal current_field, current_lines
        if current_field is not None:
            value = "\n".join(current_lines).strip() or "\u200b"
            fields.append((current_field, value))
            current_field = None
            current_lines = []

    for line in lines:
        if line.startswith("# ") and title == "Mededeling" and not description_lines and current_field is None:
            title = line[2:].strip() or title
        elif line.startswith("## "):
            flush_field()
            current_field = line[3:].strip() or "Info"
        elif current_field is not None:
            current_lines.append(line)
        else:
            description_lines.append(line)

    flush_field()

    description = "\n".join(description_lines).strip()
    embed = discord.Embed(
        title=title[:256],
        description=description[:4096] if description else None,
        color=color,
    )

    for name, value in fields[:25]:
        embed.add_field(name=name[:256], value=value[:1024], inline=False)

    return embed


class AnnouncementModal(discord.ui.Modal, title="Nieuwe announcement"):
    content = discord.ui.TextInput(
        label="Paste",
        style=discord.TextStyle.paragraph,
        placeholder="# Titel\n\nTekst...\n\n## Onderdeel\nMeer tekst...",
        required=True,
        max_length=4000,
    )

    color = discord.ui.TextInput(
        label="Embed kleur (hex)",
        placeholder="#6C27DA",
        default="#6C27DA",
        required=True,
        max_length=7,
    )

    def __init__(self, cog: "Changelog", ping_role: discord.Role | None):
        super().__init__()
        self.cog = cog
        self.ping_role = ping_role

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Dit werkt alleen in een server.", ephemeral=True)
            return

        try:
            color = parse_color(str(self.color))
            embed = build_embed(str(self.content), color)
        except ValueError:
            await interaction.response.send_message("❌ Gebruik een geldige hexkleur, bijvoorbeeld `#6C27DA`.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Het announcement-kanaal is niet gevonden.", ephemeral=True)
            return

        # Only the explicitly selected ping role may notify. Role mentions inside the embed never ping.
        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[self.ping_role] if self.ping_role else False,
            replied_user=False,
        )
        message_content = self.ping_role.mention if self.ping_role else None

        try:
            message = await channel.send(
                content=message_content,
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Ik kan niet in het announcement-kanaal sturen.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Versturen mislukt: `{exc}`", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Announcement geplaatst in {channel.mention}.\n{message.jump_url}",
            ephemeral=True,
        )


class Changelog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    changelog = app_commands.Group(name="changelog", description="LEVENLOOS announcements en changelogs.")

    @changelog.command(name="embed", description="Plaats een announcement-embed vanuit een paste.")
    @app_commands.describe(ping="Optionele rol die boven de embed wordt gepingd.")
    async def embed_command(self, interaction: discord.Interaction, ping: discord.Role | None = None):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return

        if ping is not None:
            if ping.is_default():
                await interaction.response.send_message("❌ `@everyone` kan niet als pingrol worden gebruikt.", ephemeral=True)
                return
            if ping.managed:
                await interaction.response.send_message("❌ Deze rol kan niet als pingrol worden gebruikt.", ephemeral=True)
                return

        await interaction.response.send_modal(AnnouncementModal(self, ping))


async def setup(bot: commands.Bot):
    await bot.add_cog(Changelog(bot))
