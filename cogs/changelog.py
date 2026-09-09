import re

import discord
from discord import app_commands
from discord.ext import commands

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
    """Paste format: first '# ' line = title, '## ' lines = fields."""
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


class AnnouncementModal(discord.ui.Modal, title="Nieuwe embed"):
    content = discord.ui.TextInput(
        label="Inhoud",
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

    def __init__(
        self,
        cog: "Changelog",
        channel: discord.TextChannel,
        ping_role: discord.Role | None,
    ):
        super().__init__()
        self.cog = cog
        self.channel = channel
        self.ping_role = ping_role

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Dit werkt alleen in een server.", ephemeral=True)
            return

        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return

        # Resolve the channel again so stale/deleted channels are handled cleanly.
        channel = interaction.guild.get_channel(self.channel.id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Het gekozen kanaal bestaat niet meer.", ephemeral=True)
            return

        ping_role: discord.Role | None = None
        if self.ping_role is not None:
            ping_role = interaction.guild.get_role(self.ping_role.id)
            if ping_role is None:
                await interaction.response.send_message("❌ De gekozen pingrol bestaat niet meer.", ephemeral=True)
                return
            if ping_role.is_default() or ping_role.managed:
                await interaction.response.send_message("❌ Deze rol kan niet als pingrol worden gebruikt.", ephemeral=True)
                return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message("❌ Ik kan mijn serverrechten niet controleren.", ephemeral=True)
            return

        permissions = channel.permissions_for(me)
        if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
            await interaction.response.send_message(
                f"❌ Ik heb in {channel.mention} **Kanaal bekijken**, **Berichten verzenden** en **Links insluiten** nodig.",
                ephemeral=True,
            )
            return

        try:
            color = parse_color(str(self.color))
            embed = build_embed(str(self.content), color)
        except ValueError:
            await interaction.response.send_message(
                "❌ Gebruik een geldige hexkleur, bijvoorbeeld `#6C27DA`.",
                ephemeral=True,
            )
            return

        allowed_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[ping_role] if ping_role else False,
            replied_user=False,
        )
        message_content = ping_role.mention if ping_role else None

        try:
            message = await channel.send(
                content=message_content,
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ Ik kan niet in {channel.mention} sturen.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(f"❌ Versturen mislukt: `{exc}`", ephemeral=True)
            return

        ping_text = ping_role.mention if ping_role else "geen ping"
        await interaction.response.send_message(
            f"✅ Embed geplaatst in {channel.mention} · {ping_text}\n{message.jump_url}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, parent: "EmbedBuilderView"):
        super().__init__(
            placeholder="1. Kies het kanaal…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            row=0,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.parent_view.ensure_owner(interaction):
            return

        selected = self.values[0]
        channel = interaction.guild.get_channel(selected.id) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Kies een normaal tekst- of announcementkanaal.", ephemeral=True)
            return

        self.parent_view.channel = channel
        self.parent_view.update_components()
        await interaction.response.edit_message(embed=self.parent_view.build_status_embed(), view=self.parent_view)


class PingRolePicker(discord.ui.RoleSelect):
    def __init__(self, parent: "EmbedBuilderView"):
        super().__init__(
            placeholder="2. Kies optioneel een pingrol…",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.parent_view.ensure_owner(interaction):
            return

        role = self.values[0]
        if role.is_default():
            await interaction.response.send_message("❌ `@everyone` kan niet als pingrol worden gebruikt.", ephemeral=True)
            return
        if role.managed:
            await interaction.response.send_message("❌ Deze beheerde rol kan niet als pingrol worden gebruikt.", ephemeral=True)
            return

        self.parent_view.ping_role = role
        self.parent_view.update_components()
        await interaction.response.edit_message(embed=self.parent_view.build_status_embed(), view=self.parent_view)


class EmbedBuilderView(discord.ui.View):
    def __init__(self, cog: "Changelog", author_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.channel: discord.TextChannel | None = None
        self.ping_role: discord.Role | None = None

        self.channel_picker = ChannelPicker(self)
        self.role_picker = PingRolePicker(self)
        self.add_item(self.channel_picker)
        self.add_item(self.role_picker)
        self.update_components()

    async def ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Alleen degene die deze builder opende kan hem gebruiken.", ephemeral=True)
            return False
        return True

    def update_components(self) -> None:
        self.create_embed.disabled = self.channel is None
        self.clear_ping.disabled = self.ping_role is None

    def build_status_embed(self) -> discord.Embed:
        channel_text = self.channel.mention if self.channel else "*Nog niet gekozen*"
        ping_text = self.ping_role.mention if self.ping_role else "Geen ping"

        embed = discord.Embed(
            title="📝 Embed maken",
            description=(
                "Kies hieronder waar de embed geplaatst moet worden en eventueel welke rol gepingd wordt.\n"
                "Daarna opent **Embed maken** het invoervenster."
            ),
            color=discord.Color(DEFAULT_COLOR),
        )
        embed.add_field(name="📍 Kanaal", value=channel_text, inline=True)
        embed.add_field(name="🔔 Ping", value=ping_text, inline=True)
        embed.set_footer(text="Deze builder verloopt na 5 minuten.")
        return embed

    @discord.ui.button(label="Geen ping", emoji="🔕", style=discord.ButtonStyle.secondary, row=2)
    async def clear_ping(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ensure_owner(interaction):
            return
        self.ping_role = None
        self.update_components()
        await interaction.response.edit_message(embed=self.build_status_embed(), view=self)

    @discord.ui.button(label="Embed maken", emoji="✨", style=discord.ButtonStyle.primary, row=2)
    async def create_embed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ensure_owner(interaction):
            return
        if self.channel is None:
            await interaction.response.send_message("❌ Kies eerst een kanaal.", ephemeral=True)
            return

        await interaction.response.send_modal(
            AnnouncementModal(
                cog=self.cog,
                channel=self.channel,
                ping_role=self.ping_role,
            )
        )

    @discord.ui.button(label="Annuleren", emoji="✖️", style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.ensure_owner(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="❌ Embed-builder geannuleerd.", embed=None, view=self)
        self.stop()


class Changelog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    changelog = app_commands.Group(
        name="changelog",
        description="LEVENLOOS announcements en changelogs.",
    )

    async def open_builder(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return

        view = EmbedBuilderView(self, interaction.user.id)
        await interaction.response.send_message(
            embed=view.build_status_embed(),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="embed", description="Maak interactief een embed en kies kanaal en pingrol.")
    async def embed(self, interaction: discord.Interaction) -> None:
        await self.open_builder(interaction)

    @changelog.command(name="embed", description="Maak interactief een embed en kies kanaal en pingrol.")
    async def changelog_embed(self, interaction: discord.Interaction) -> None:
        await self.open_builder(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(Changelog(bot))
