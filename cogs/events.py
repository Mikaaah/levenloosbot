from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

try:
    from cogs.games import ACTIVE_GAME_CATEGORY_ID, management_access
except ImportError:
    ACTIVE_GAME_CATEGORY_ID = 940987098427707412

    def management_access(member: discord.Member) -> bool:
        if member.guild.owner_id == member.id:
            return True
        if member.guild_permissions.administrator:
            return True
        names = {role.name.lower() for role in member.roles}
        return "admin" in names or "owner" in names


DATABASE_PATH = "levenloos.db"
BRAND_COLOR = discord.Color.from_str("#6C27DA")
EVENT_CREATOR_ROLE_ID = 985989826950086717
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
DEFAULT_DURATION_MINUTES = 120
DEFAULT_POLL_HOURS = 24
VOICE_OPEN_MINUTES = 15
MAX_SLOTS_LIMIT = 99
EVENT_BANNER = "<:E1:1546897508738007081><:E2:1546897546964639754><:E3:1546897578719973377><:E4:1546897598022029368><:E5:1546897622453854238><:E6:1546897645786890374><:E7:1546897666355494963>"

STATUS_PLANNING = "planning"
STATUS_CONFIRMED = "confirmed"
STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"
STATUS_CANCELLED = "cancelled"


# =========================================================
# HELPERS
# =========================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_db(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def from_db(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def discord_ts(dt: datetime | None, style: str = "F") -> str:
    if not dt:
        return "Nog niet bepaald"
    return f"<t:{int(dt.timestamp())}:{style}>"


def parse_date(value: str) -> datetime:
    raw = value.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    raise ValueError("Gebruik voor de datum bijvoorbeeld `12-09-2026`.")


def parse_time(value: str) -> tuple[int, int]:
    raw = value.strip().replace(".", ":")
    for fmt in ("%H:%M", "%H"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.hour, parsed.minute
        except ValueError:
            pass
    raise ValueError(f"Ongeldige tijd `{value}`. Gebruik bijvoorbeeld `20:00`.")


def combine_amsterdam(date_value: str, time_value: str) -> datetime:
    date = parse_date(date_value)
    hour, minute = parse_time(time_value)
    local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=AMSTERDAM)
    return local.astimezone(timezone.utc)


def clean_voice_name(name: str) -> str:
    text = re.sub(r"[^\w\- ]+", "", name, flags=re.UNICODE).strip()
    text = re.sub(r"\s+", "-", text).lower()
    return (text or "event")[:80]


def truncate(text: str, length: int) -> str:
    text = text.strip()
    return text if len(text) <= length else text[: length - 1].rstrip() + "…"


def human_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}u {mins}m"
    if hours:
        return f"{hours} uur"
    return f"{mins} min"


def status_label(status: str) -> str:
    return {
        STATUS_PLANNING: "Planning",
        STATUS_CONFIRMED: "Bevestigd",
        STATUS_ACTIVE: "Bezig",
        STATUS_ENDED: "Afgelopen",
        STATUS_CANCELLED: "Geannuleerd",
    }.get(status, status.title())


@dataclass(slots=True)
class WizardState:
    guild_id: int
    author_id: int
    channel_id: int
    game_name: str | None = None
    game_role_id: int | None = None
    description: str = ""
    duration_minutes: int = DEFAULT_DURATION_MINUTES
    max_slots: int = 0
    time_options: list[datetime] = field(default_factory=list)
    temporary_voice: bool = True


# =========================================================
# SETUP WIZARD
# =========================================================


class ActivitySelect(discord.ui.Select):
    def __init__(self, cog: "Events", state: WizardState, guild: discord.Guild):
        self.cog = cog
        self.state = state
        options: list[discord.SelectOption] = []
        for name, role_id in cog.active_games(guild)[:24]:
            options.append(discord.SelectOption(label=truncate(name, 100), value=f"game:{role_id}"))
        options.append(discord.SelectOption(label="Aangepaste activiteit", value="custom", description="Gebruik een eigen naam."))
        super().__init__(placeholder="Kies een game of activiteit", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.values[0] == "custom":
            await interaction.response.send_modal(CustomActivityModal(self.cog, self.state))
            return
        role_id = int(self.values[0].split(":", 1)[1])
        role = interaction.guild.get_role(role_id) if interaction.guild else None
        if role is None:
            await interaction.response.send_message("Deze game bestaat niet meer.", ephemeral=True)
            return
        self.state.game_name = role.name.removeprefix("◆").strip()
        self.state.game_role_id = role.id
        await interaction.response.edit_message(embed=self.cog.wizard_embed(self.state), view=SetupWizardView(self.cog, self.state))


class CustomActivityModal(discord.ui.Modal, title="Aangepaste activiteit"):
    name = discord.ui.TextInput(label="Naam", placeholder="Bijv. Filmavond", max_length=100)
    description = discord.ui.TextInput(label="Beschrijving", placeholder="Optioneel", style=discord.TextStyle.paragraph, required=False, max_length=1000)

    def __init__(self, cog: "Events", state: WizardState):
        super().__init__()
        self.cog = cog
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.state.game_name = str(self.name).strip()
        self.state.game_role_id = None
        if str(self.description).strip():
            self.state.description = str(self.description).strip()
        await interaction.response.edit_message(embed=self.cog.wizard_embed(self.state), view=SetupWizardView(self.cog, self.state))


class EventInfoModal(discord.ui.Modal, title="Event instellingen"):
    description = discord.ui.TextInput(label="Beschrijving", required=False, style=discord.TextStyle.paragraph, max_length=1000)
    duration = discord.ui.TextInput(label="Duur in minuten", default=str(DEFAULT_DURATION_MINUTES), max_length=4)
    max_slots = discord.ui.TextInput(label="Max. deelnemers", placeholder="0 = onbeperkt", default="0", max_length=2)

    def __init__(self, cog: "Events", state: WizardState):
        super().__init__()
        self.cog = cog
        self.state = state
        self.description.default = state.description or None
        self.duration.default = str(state.duration_minutes)
        self.max_slots.default = str(state.max_slots)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            duration = int(str(self.duration).strip())
            max_slots = int(str(self.max_slots).strip())
        except ValueError:
            await interaction.response.send_message("Duur en maximaal aantal deelnemers moeten getallen zijn.", ephemeral=True)
            return
        if not 15 <= duration <= 1440:
            await interaction.response.send_message("De duur moet tussen 15 en 1440 minuten liggen.", ephemeral=True)
            return
        if not 0 <= max_slots <= MAX_SLOTS_LIMIT:
            await interaction.response.send_message(f"Max. deelnemers moet 0 t/m {MAX_SLOTS_LIMIT} zijn.", ephemeral=True)
            return
        self.state.description = str(self.description).strip()
        self.state.duration_minutes = duration
        self.state.max_slots = max_slots
        await interaction.response.edit_message(embed=self.cog.wizard_embed(self.state), view=SetupWizardView(self.cog, self.state))


class PlanningModal(discord.ui.Modal, title="Planning instellen"):
    date = discord.ui.TextInput(label="Datum", placeholder="12-09-2026", max_length=10)
    time_1 = discord.ui.TextInput(label="Tijd 1", placeholder="20:00", max_length=5)
    time_2 = discord.ui.TextInput(label="Tijd 2", placeholder="Optioneel, bijv. 21:00", required=False, max_length=5)
    time_3 = discord.ui.TextInput(label="Tijd 3", placeholder="Optioneel, bijv. 22:00", required=False, max_length=5)

    def __init__(self, cog: "Events", state: WizardState):
        super().__init__()
        self.cog = cog
        self.state = state
        if state.time_options:
            first = state.time_options[0].astimezone(AMSTERDAM)
            self.date.default = first.strftime("%d-%m-%Y")
            times = [dt.astimezone(AMSTERDAM).strftime("%H:%M") for dt in state.time_options[:3]]
            if times:
                self.time_1.default = times[0]
            if len(times) > 1:
                self.time_2.default = times[1]
            if len(times) > 2:
                self.time_3.default = times[2]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        date_value = str(self.date).strip()
        times = [str(self.time_1).strip(), str(self.time_2).strip(), str(self.time_3).strip()]
        try:
            parsed = sorted({combine_amsterdam(date_value, value) for value in times if value})
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if not parsed:
            await interaction.response.send_message("Vul minimaal één tijd in.", ephemeral=True)
            return
        if any(dt <= utc_now() + timedelta(minutes=5) for dt in parsed):
            await interaction.response.send_message("Elke optie moet minimaal 5 minuten in de toekomst liggen.", ephemeral=True)
            return
        self.state.time_options = parsed
        await interaction.response.edit_message(embed=self.cog.wizard_embed(self.state), view=SetupWizardView(self.cog, self.state))


class SetupWizardView(discord.ui.View):
    def __init__(self, cog: "Events", state: WizardState):
        super().__init__(timeout=900)
        self.cog = cog
        self.state = state
        guild = cog.bot.get_guild(state.guild_id)
        if guild:
            self.add_item(ActivitySelect(cog, state, guild))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.state.author_id:
            await interaction.response.send_message("Deze setup is niet van jou.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Planning", style=discord.ButtonStyle.secondary, row=1)
    async def planning(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PlanningModal(self.cog, self.state))

    @discord.ui.button(label="Instellingen", style=discord.ButtonStyle.secondary, row=1)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EventInfoModal(self.cog, self.state))

    @discord.ui.button(label="Voice aan/uit", style=discord.ButtonStyle.secondary, row=1)
    async def voice_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.state.temporary_voice = not self.state.temporary_voice
        await interaction.response.edit_message(embed=self.cog.wizard_embed(self.state), view=SetupWizardView(self.cog, self.state))

    @discord.ui.button(label="Preview", style=discord.ButtonStyle.secondary, row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.state.game_name:
            await interaction.response.send_message("Kies eerst een activiteit.", ephemeral=True)
            return
        await interaction.response.send_message(embed=self.cog.preview_embed(self.state), ephemeral=True)

    @discord.ui.button(label="Publiceren", style=discord.ButtonStyle.success, row=2)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.state.game_name:
            await interaction.response.send_message("Kies eerst een activiteit.", ephemeral=True)
            return
        if not self.state.time_options:
            await interaction.response.send_message("Stel eerst minimaal één datum en tijd in.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            event_id = await self.cog.publish_from_wizard(interaction, self.state)
        except discord.Forbidden:
            await interaction.followup.send("Ik mis permissies om dit event te publiceren. Controleer Manage Events, Manage Channels, View Channel en Send Messages.", ephemeral=True)
            return
        except (discord.HTTPException, RuntimeError) as exc:
            await interaction.followup.send(f"Publiceren is mislukt: `{truncate(str(exc), 300)}`", ephemeral=True)
            return
        await interaction.edit_original_response(embed=self.cog.wizard_embed(self.state, published=True), view=None)
        await interaction.followup.send(f"Event #{event_id} is gepubliceerd.", ephemeral=True)


# =========================================================
# PUBLIC DASHBOARD
# =========================================================


class JoinTimeModal(discord.ui.Modal):
    """Native Discord modal with a required RadioGroup for planning votes."""

    def __init__(self, cog: "Events", event_id: int, options: list[dict]):
        super().__init__(title="Deelnemen")
        self.cog = cog
        self.event_id = event_id

        radio_options: list[discord.RadioGroupOption] = []
        for index, option in enumerate(options[:10], start=1):
            starts = from_db(option["starts_at"])
            if starts:
                local = starts.astimezone(AMSTERDAM)
                label = local.strftime("%H:%M")
                description = local.strftime("%A %d-%m-%Y")
            else:
                label = f"Optie {index}"
                description = None

            radio_options.append(
                discord.RadioGroupOption(
                    label=label,
                    value=str(option["id"]),
                    description=description,
                )
            )

        self.time_choice = discord.ui.RadioGroup(
            custom_id=f"levenloos:event:{event_id}:time_choice",
            required=True,
            options=radio_options,
        )
        self.add_item(
            discord.ui.Label(
                text="Kies wanneer je kunt",
                description="Je keuze telt meteen als stem en je doet direct mee.",
                component=self.time_choice,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.time_choice.value:
            await interaction.response.send_message("Kies één tijd om deel te nemen.", ephemeral=True)
            return

        option_id = int(self.time_choice.value)
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            message, interested_view = await self.cog.join_with_vote(
                interaction, self.event_id, option_id
            )
        except Exception as exc:
            print(f"[events] Deelname via RadioGroup mislukt voor event {self.event_id}: {exc}")
            await interaction.edit_original_response(
                content="Je deelname kon niet worden opgeslagen. Probeer het opnieuw.",
                view=None,
            )
            return

        await interaction.edit_original_response(
            content=message,
            view=interested_view,
        )


class InterestedView(discord.ui.View):
    """Prominent link to Discord's own RSVP/Interested screen.

    Discord does not allow bots to set another user's Scheduled Event RSVP,
    so this deliberately makes the required user action as obvious as possible.
    """

    def __init__(self, event_url: str):
        super().__init__(timeout=180)
        self.add_item(
            discord.ui.Button(
                label="⭐  ZET MIJ OP INTERESTED  ⭐",
                style=discord.ButtonStyle.link,
                url=event_url,
                row=0,
            )
        )


class EventDashboardView(discord.ui.View):
    def __init__(self, cog: "Events", event_id: int, *, disabled: bool = False):
        super().__init__(timeout=None)
        self.cog = cog
        self.event_id = event_id
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.custom_id = f"levenloos:event:{event_id}:{item.custom_id}"
                if disabled:
                    item.disabled = True

    @discord.ui.button(label="Deelnemen", style=discord.ButtonStyle.success, custom_id="join", row=0)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        event = await self.cog.get_event(self.event_id)
        if not event or event["status"] in {STATUS_ENDED, STATUS_CANCELLED}:
            await interaction.response.send_message("Dit event neemt geen aanmeldingen meer aan.", ephemeral=True)
            return
        if event["status"] == STATUS_PLANNING:
            options = await self.cog.get_time_options(self.event_id)
            if not options:
                await interaction.response.send_message("Er zijn geen tijdopties beschikbaar.", ephemeral=True)
                return
            if len(options) == 1:
                await interaction.response.defer(ephemeral=True, thinking=True)
                message, interested_view = await self.cog.join_with_vote(
                    interaction, self.event_id, int(options[0]["id"])
                )
                await interaction.edit_original_response(content=message, view=interested_view)
                return
            if len(options) > 10:
                await interaction.response.send_message(
                    "Dit event heeft te veel tijdopties voor de radio-keuze. Vraag de host om maximaal 10 opties te gebruiken.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_modal(JoinTimeModal(self.cog, self.event_id, options))
            return
        await self.cog.set_participant(interaction, self.event_id, "going")

    @discord.ui.button(label="Afmelden", style=discord.ButtonStyle.secondary, custom_id="leave", row=0)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.set_participant(interaction, self.event_id, "leave")

    @discord.ui.button(label="Beheren", style=discord.ButtonStyle.secondary, custom_id="manage", row=0)
    async def manage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.cog.can_manage_event(interaction, self.event_id):
            await interaction.response.send_message("Alleen de host, co-host of beheerder kan dit event beheren.", ephemeral=True)
            return
        await interaction.response.send_message(embed=await self.cog.management_embed(self.event_id), view=EventManagementView(self.cog, self.event_id), ephemeral=True)


class EventManagementView(discord.ui.View):
    def __init__(self, cog: "Events", event_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.event_id = event_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        allowed = await self.cog.can_manage_event(interaction, self.event_id)
        if not allowed:
            await interaction.response.send_message("Geen toegang.", ephemeral=True)
        return allowed

    @discord.ui.button(label="Stemming sluiten", style=discord.ButtonStyle.primary)
    async def close_poll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.cog.close_poll(self.event_id, interaction.guild)
        await interaction.followup.send(result, ephemeral=True)

    @discord.ui.button(label="Dashboard vernieuwen", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.cog.refresh_dashboard(self.event_id)
        await interaction.followup.send("Dashboard vernieuwd.", ephemeral=True)

    @discord.ui.button(label="Annuleren", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message("Weet je zeker dat je dit event wilt annuleren?", view=CancelConfirmView(self.cog, self.event_id), ephemeral=True)


class CancelConfirmView(discord.ui.View):
    def __init__(self, cog: "Events", event_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.event_id = event_id

    @discord.ui.button(label="Event annuleren", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self.cog.can_manage_event(interaction, self.event_id):
            await interaction.response.send_message("Geen toegang.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.cog.cancel_event(self.event_id, interaction.guild)
        await interaction.followup.send("Event geannuleerd.", ephemeral=True)


# =========================================================
# CONFIG VIEW
# =========================================================


class EventChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, cog: "Events", guild_id: int):
        super().__init__(placeholder="Dashboardkanaal", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=0)
        self.cog = cog
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        await self.cog.set_config_value(self.guild_id, "event_channel_id", channel.id)
        await interaction.response.send_message(f"Dashboardkanaal ingesteld op {channel.mention}.", ephemeral=True)


class VoiceCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, cog: "Events", guild_id: int):
        super().__init__(placeholder="Voicecategorie", channel_types=[discord.ChannelType.category], min_values=1, max_values=1, row=1)
        self.cog = cog
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction) -> None:
        category = self.values[0]
        await self.cog.set_config_value(self.guild_id, "voice_category_id", category.id)
        await interaction.response.send_message(f"Voicecategorie ingesteld op **{category.name}**.", ephemeral=True)


class EventConfigView(discord.ui.View):
    def __init__(self, cog: "Events", guild_id: int):
        super().__init__(timeout=600)
        self.add_item(EventChannelSelect(cog, guild_id))
        self.add_item(VoiceCategorySelect(cog, guild_id))


# =========================================================
# COG
# =========================================================


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}
        self._views_restored = False
        self.maintenance_loop.start()

    def cog_unload(self) -> None:
        self.maintenance_loop.cancel()

    event = app_commands.Group(name="event", description="Plan en beheer events op LEVENLOOS.")

    async def cog_load(self) -> None:
        await self.init_db()

    # -----------------------------------------------------
    # DATABASE
    # -----------------------------------------------------

    async def init_db(self) -> None:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    host_id INTEGER NOT NULL,
                    game_name TEXT NOT NULL,
                    game_role_id INTEGER,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'planning',
                    max_slots INTEGER NOT NULL DEFAULT 0,
                    duration_minutes INTEGER NOT NULL DEFAULT 120,
                    starts_at TEXT,
                    ends_at TEXT,
                    poll_closes_at TEXT,
                    discord_event_id INTEGER,
                    dashboard_channel_id INTEGER,
                    dashboard_message_id INTEGER,
                    voice_channel_id INTEGER,
                    voice_opened INTEGER NOT NULL DEFAULT 0,
                    temporary_voice INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_time_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id INTEGER NOT NULL,
                    starts_at TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    UNIQUE(event_id, starts_at),
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS event_time_votes (
                    event_id INTEGER NOT NULL,
                    option_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, user_id),
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
                    FOREIGN KEY(option_id) REFERENCES event_time_options(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS event_participants (
                    event_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    queue_position INTEGER,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, user_id),
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS event_hosts (
                    event_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    PRIMARY KEY(event_id, user_id),
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS event_reminders (
                    event_id INTEGER NOT NULL,
                    offset_minutes INTEGER NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(event_id, offset_minutes),
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS event_config (
                    guild_id INTEGER PRIMARY KEY,
                    event_channel_id INTEGER,
                    voice_category_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_events_guild_status ON events(guild_id, status);
                CREATE INDEX IF NOT EXISTS idx_event_participants_status ON event_participants(event_id, status);
                """
            )
            await db.commit()

    async def fetchone(self, query: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return await cursor.fetchone()

    async def fetchall(self, query: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return await cursor.fetchall()

    async def get_event(self, event_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM events WHERE id = ?", (event_id,))

    async def get_time_options(self, event_id: int) -> list[dict]:
        rows = await self.fetchall(
            """
            SELECT o.id, o.starts_at, o.sort_order, COUNT(v.user_id) AS votes
            FROM event_time_options o
            LEFT JOIN event_time_votes v ON v.option_id = o.id
            WHERE o.event_id = ?
            GROUP BY o.id
            ORDER BY o.sort_order ASC
            """,
            (event_id,),
        )
        return [dict(row) for row in rows]

    async def get_config(self, guild_id: int) -> aiosqlite.Row | None:
        return await self.fetchone("SELECT * FROM event_config WHERE guild_id = ?", (guild_id,))

    async def set_config_value(self, guild_id: int, column: str, value: int) -> None:
        if column not in {"event_channel_id", "voice_category_id"}:
            raise ValueError("Ongeldige configkolom")
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO event_config(guild_id) VALUES (?)", (guild_id,))
            await db.execute(f"UPDATE event_config SET {column} = ? WHERE guild_id = ?", (value, guild_id))
            await db.commit()

    # -----------------------------------------------------
    # GAME INTEGRATION
    # -----------------------------------------------------

    def active_games(self, guild: discord.Guild) -> list[tuple[str, int]]:
        category = guild.get_channel(ACTIVE_GAME_CATEGORY_ID)
        active_slugs: set[str] = set()
        if isinstance(category, discord.CategoryChannel):
            active_slugs = {channel.name.removeprefix("▏").lower() for channel in category.text_channels}
        games: list[tuple[str, int]] = []
        for role in guild.roles:
            if not role.name.startswith("◆ "):
                continue
            name = role.name[2:].strip()
            slug = clean_voice_name(name)
            if not active_slugs or slug in active_slugs:
                games.append((name, role.id))
        games.sort(key=lambda item: item[0].casefold())
        return games

    @commands.Cog.listener()
    async def on_game_catalog_changed(self, guild: discord.Guild, game_name: str, action: str) -> None:
        if action == "removed":
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE events SET game_role_id = NULL, updated_at = ? WHERE guild_id = ? AND game_name = ?", (to_db(utc_now()), guild.id, game_name))
                await db.commit()

    # -----------------------------------------------------
    # EMBEDS
    # -----------------------------------------------------

    def wizard_embed(self, state: WizardState, *, published: bool = False) -> discord.Embed:
        title = "EVENT GEPUBLICEERD" if published else "EVENT MAKEN"
        intro = "De setup is afgerond." if published else "Stel hieronder je event samen. Er wordt pas iets gepubliceerd wanneer je bevestigt."
        planning = "\n".join(
            f"**{i} • {discord_ts(dt, 'f')}**\n> {discord_ts(dt, 'R')}"
            for i, dt in enumerate(state.time_options, 1)
        ) if state.time_options else "> Nog geen datum en tijd ingesteld."

        embed = discord.Embed(
            description=(
                f"# {EVENT_BANNER}\n"
                f"## 🎉 {title}\n"
                f"> {intro}\n\n"
                f"## 🎮 Activiteit\n"
                f"> **{state.game_name or 'Niet gekozen'}**\n"
                + (f"> {state.description}\n" if state.description else "")
            ),
            color=BRAND_COLOR,
        )
        embed.add_field(name="📅 Planning", value=planning, inline=False)
        embed.add_field(name="⏱️ Duur", value=f"> **{human_duration(state.duration_minutes)}**")
        embed.add_field(name="👥 Deelnemers", value=f"> **{state.max_slots if state.max_slots else 'Onbeperkt'}**")
        embed.add_field(
            name="🔊 Voice",
            value="> **15 min vooraf**\n> Na afloop verwijderd zodra leeg" if state.temporary_voice else "> Uit",
            inline=False,
        )
        return embed

    def preview_embed(self, state: WizardState) -> discord.Embed:
        planning = "\n".join(
            f"**{i} • {discord_ts(dt, 'f')}**\n> {discord_ts(dt, 'R')}"
            for i, dt in enumerate(state.time_options, 1)
        ) if state.time_options else "> Nog niet ingesteld."
        embed = discord.Embed(
            description=(
                f"# {EVENT_BANNER}\n"
                f"## 🎮 {state.game_name or 'Event'}\n"
                f"> {state.description or 'Geen beschrijving opgegeven.'}\n\n"
                f"## 📋 Voorbeeld\n"
                f"> **Status:** Planning\n"
                f"> **Duur:** {human_duration(state.duration_minutes)}\n"
                f"> **Plaatsen:** {state.max_slots if state.max_slots else 'Onbeperkt'}"
            ),
            color=BRAND_COLOR,
        )
        embed.add_field(name="📅 Planning", value=planning, inline=False)
        return embed

    async def dashboard_embed(self, event_id: int) -> discord.Embed:
        event = await self.get_event(event_id)
        if event is None:
            raise RuntimeError("Event niet gevonden")

        starts = from_db(event["starts_at"])
        status = status_label(event["status"])
        description = event["description"] or "Geen extra beschrijving opgegeven."

        embed = discord.Embed(
            description=(
                f"# {EVENT_BANNER}\n"
                f"## 🎮 {event['game_name']}\n"
                f"> {description}\n\n"
                f"## 📌 Event informatie\n"
                f"> **Status:** {status}\n"
                f"> **Wanneer:** {discord_ts(starts, 'F') if starts else 'Wordt bepaald'}\n"
                + (f"> **Start:** {discord_ts(starts, 'R')}\n" if starts else "")
                + f"> **Duur:** {human_duration(int(event['duration_minutes']))}"
            ),
            color=BRAND_COLOR,
        )

        going = await self.fetchall(
            "SELECT user_id FROM event_participants WHERE event_id = ? AND status = 'going' ORDER BY joined_at ASC",
            (event_id,),
        )
        waitlist = await self.fetchall(
            "SELECT user_id FROM event_participants WHERE event_id = ? AND status = 'waitlist' ORDER BY queue_position ASC, joined_at ASC",
            (event_id,),
        )
        capacity = event["max_slots"]
        participant_title = f"👥 Deelnemers · {len(going)}/{capacity}" if capacity else f"👥 Deelnemers · {len(going)}"
        participant_value = self.mention_list([row["user_id"] for row in going])
        embed.add_field(
            name=participant_title,
            value=f"> {participant_value}" if participant_value != "Nog niemand." else "> Nog niemand. Klik op **Deelnemen** om mee te doen.",
            inline=False,
        )

        if waitlist:
            waitlist_value = self.mention_list([row["user_id"] for row in waitlist], numbered=True)
            embed.add_field(name=f"⏳ Wachtlijst · {len(waitlist)}", value=f"> {waitlist_value}", inline=False)

        if event["status"] == STATUS_PLANNING:
            options = await self.get_time_options(event_id)
            lines: list[str] = []
            for index, option in enumerate(options, start=1):
                voters = await self.fetchall(
                    "SELECT user_id FROM event_time_votes WHERE option_id = ? ORDER BY created_at ASC",
                    (option["id"],),
                )
                voter_mentions = " ".join(f"<@{row['user_id']}>" for row in voters[:12])
                count = int(option["votes"])
                vote_word = "stem" if count == 1 else "stemmen"
                line = (
                    f"**{index} • {discord_ts(from_db(option['starts_at']), 'f')}**\n"
                    f"> **{count} {vote_word}**"
                )
                if voter_mentions:
                    line += f" · {voter_mentions}"
                lines.append(line)
            embed.add_field(name="🗳️ Planning", value="\n\n".join(lines) or "> Geen opties.", inline=False)
            closes = from_db(event["poll_closes_at"])
            if closes:
                embed.add_field(
                    name="⏰ Stemming sluit",
                    value=f"> {discord_ts(closes, 'F')}\n> {discord_ts(closes, 'R')}",
                    inline=False,
                )

        if event["discord_event_id"]:
            event_url = f"https://discord.com/events/{event['guild_id']}/{event['discord_event_id']}"
            embed.add_field(
                name="⭐ Discord Event",
                value=f"> [**Open het officiële event en zet jezelf op Interested**]({event_url})",
                inline=False,
            )

        host_name = await self.user_display_name(int(event["guild_id"]), int(event["host_id"]))
        embed.set_footer(text=f"Event #{event_id}  •  Host: {host_name}")
        return embed

    async def management_embed(self, event_id: int) -> discord.Embed:
        event = await self.get_event(event_id)
        if not event:
            return discord.Embed(title="EVENT BEHEREN", description="Event niet gevonden.")
        host_name = await self.user_display_name(int(event["guild_id"]), int(event["host_id"]))
        return discord.Embed(
            description=(
                f"# {EVENT_BANNER}\n"
                f"## ⚙️ Event beheren\n"
                f"> **{event['game_name']}**\n\n"
                f"## 📌 Details\n"
                f"> **Event:** #{event_id}\n"
                f"> **Status:** {status_label(event['status'])}\n"
                f"> **Host:** {host_name}"
            ),
            color=BRAND_COLOR,
        )

    async def user_display_name(self, guild_id: int, user_id: int) -> str:
        """Resolve a Discord user ID to a readable server/player name."""
        guild = self.bot.get_guild(guild_id)
        if guild is not None:
            member = guild.get_member(user_id)
            if member is not None:
                return member.display_name
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                member = None
            if member is not None:
                return member.display_name

        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except (discord.NotFound, discord.HTTPException):
                user = None
        if user is not None:
            return user.global_name or user.name
        return "Onbekend"

    @staticmethod
    def mention_list(user_ids: Iterable[int], *, numbered: bool = False) -> str:
        ids = list(user_ids)
        if not ids:
            return "Nog niemand."
        shown = ids[:20]
        text = "\n".join(f"`{i}` <@{uid}>" for i, uid in enumerate(shown, 1)) if numbered else " ".join(f"<@{uid}>" for uid in shown)
        if len(ids) > len(shown):
            text += f"\n+ {len(ids) - len(shown)} meer"
        return text

    # -----------------------------------------------------
    # PUBLISH / SCHEDULED EVENT
    # -----------------------------------------------------

    async def publish_from_wizard(self, interaction: discord.Interaction, state: WizardState) -> int:
        guild = interaction.guild
        if guild is None:
            raise RuntimeError("Dit werkt alleen in een server.")
        config = await self.get_config(guild.id)
        channel_id = config["event_channel_id"] if config else None
        dashboard_channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(dashboard_channel, discord.TextChannel):
            dashboard_channel = guild.get_channel(state.channel_id)
        if not isinstance(dashboard_channel, discord.TextChannel):
            raise RuntimeError("Geen geldig dashboardkanaal gevonden.")

        now = utc_now()
        status = STATUS_PLANNING if len(state.time_options) > 1 else STATUS_CONFIRMED
        starts_at = state.time_options[0] if len(state.time_options) == 1 else None
        ends_at = starts_at + timedelta(minutes=state.duration_minutes) if starts_at else None
        poll_closes = None
        if len(state.time_options) > 1:
            poll_closes = min(now + timedelta(hours=DEFAULT_POLL_HOURS), min(state.time_options) - timedelta(minutes=30))
            if poll_closes <= now:
                poll_closes = now + timedelta(minutes=5)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            cursor = await db.execute(
                """
                INSERT INTO events(guild_id, host_id, game_name, game_role_id, description,
                    status, max_slots, duration_minutes, starts_at, ends_at, poll_closes_at,
                    dashboard_channel_id, temporary_voice, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (guild.id, state.author_id, state.game_name, state.game_role_id, state.description,
                 status, state.max_slots, state.duration_minutes, to_db(starts_at), to_db(ends_at),
                 to_db(poll_closes), dashboard_channel.id, 1 if state.temporary_voice else 0,
                 to_db(now), to_db(now)),
            )
            event_id = int(cursor.lastrowid)
            for index, dt in enumerate(state.time_options):
                await db.execute("INSERT INTO event_time_options(event_id, starts_at, sort_order) VALUES (?, ?, ?)", (event_id, to_db(dt), index))
            await db.execute("INSERT INTO event_hosts(event_id, user_id) VALUES (?, ?)", (event_id, state.author_id))
            # With one fixed time, the host is immediately a participant.
            # With multiple planning options, nobody can be a participant without choosing
            # an option first: the host uses Deelnemen just like everyone else.
            if len(state.time_options) == 1:
                await db.execute(
                    "INSERT INTO event_participants(event_id, user_id, status, queue_position, joined_at) VALUES (?, ?, 'going', NULL, ?)",
                    (event_id, state.author_id, to_db(now)),
                )
            for offset in (1440, 120, 30, 0):
                await db.execute("INSERT INTO event_reminders(event_id, offset_minutes, sent) VALUES (?, ?, 0)", (event_id, offset))
            await db.commit()

        try:
            if status == STATUS_CONFIRMED:
                await self.create_official_event(event_id, guild)
            message = await dashboard_channel.send(embed=await self.dashboard_embed(event_id), view=EventDashboardView(self, event_id))
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE events SET dashboard_message_id = ?, updated_at = ? WHERE id = ?", (message.id, to_db(utc_now()), event_id))
                await db.commit()
            self.bot.add_view(EventDashboardView(self, event_id), message_id=message.id)
            return event_id
        except Exception:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("DELETE FROM events WHERE id = ?", (event_id,))
                await db.commit()
            raise

    async def create_official_event(self, event_id: int, guild: discord.Guild) -> discord.ScheduledEvent:
        event = await self.get_event(event_id)
        if event is None:
            raise RuntimeError("Event niet gevonden.")
        if event["discord_event_id"]:
            existing = guild.get_scheduled_event(event["discord_event_id"])
            if existing:
                return existing
        starts = from_db(event["starts_at"])
        ends = from_db(event["ends_at"])
        if starts is None or ends is None:
            raise RuntimeError("Het event heeft nog geen definitieve tijd.")
        # Important: no voice channel is created here. It appears 15 minutes before start.
        scheduled = await guild.create_scheduled_event(
            name=truncate(event["game_name"], 100),
            description=truncate(event["description"] or "LEVENLOOS event", 1000),
            start_time=starts,
            end_time=ends,
            privacy_level=discord.PrivacyLevel.guild_only,
            entity_type=discord.EntityType.external,
            location="LEVENLOOS Discord",
            reason=f"LEVENLOOS event #{event_id}",
        )
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE events SET discord_event_id = ?, voice_channel_id = NULL, voice_opened = 0, updated_at = ? WHERE id = ?", (scheduled.id, to_db(utc_now()), event_id))
            await db.commit()
        return scheduled

    async def ensure_voice_channel(self, event_id: int) -> None:
        event = await self.get_event(event_id)
        if not event or not event["temporary_voice"] or event["voice_channel_id"]:
            return
        starts = from_db(event["starts_at"])
        if not starts or utc_now() < starts - timedelta(minutes=VOICE_OPEN_MINUTES):
            return
        guild = self.bot.get_guild(event["guild_id"])
        if not guild:
            return
        config = await self.get_config(guild.id)
        category_id = config["voice_category_id"] if config else None
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        voice_channel = await guild.create_voice_channel(
            f"Event | {clean_voice_name(event['game_name'])}",
            category=category,
            overwrites={guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True)},
            reason=f"Tijdelijk voicekanaal voor event #{event_id}",
        )
        if event["discord_event_id"]:
            scheduled = guild.get_scheduled_event(event["discord_event_id"])
            if scheduled:
                try:
                    await scheduled.edit(channel=voice_channel, entity_type=discord.EntityType.voice, reason=f"Voicekanaal koppelen aan LEVENLOOS event #{event_id}")
                except Exception:
                    try:
                        await voice_channel.delete(reason=f"Koppelen Scheduled Event #{event_id} mislukt")
                    except Exception:
                        pass
                    raise
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE events SET voice_channel_id = ?, voice_opened = 1, updated_at = ? WHERE id = ?", (voice_channel.id, to_db(utc_now()), event_id))
            await db.commit()
        await self.refresh_dashboard(event_id)

    # -----------------------------------------------------
    # PARTICIPANTS / VOTING
    # -----------------------------------------------------

    async def _register_going(self, event_id: int, user_id: int) -> str:
        event = await self.get_event(event_id)
        if not event:
            return "Event niet gevonden."
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT status FROM event_participants WHERE event_id = ? AND user_id = ?", (event_id, user_id))
            existing = await cursor.fetchone()
            cursor = await db.execute("SELECT COUNT(*) FROM event_participants WHERE event_id = ? AND status = 'going'", (event_id,))
            going_count = (await cursor.fetchone())[0]
            max_slots = int(event["max_slots"])
            already_going = existing and existing["status"] == "going"
            if max_slots and going_count >= max_slots and not already_going:
                cursor = await db.execute("SELECT COALESCE(MAX(queue_position), 0) + 1 FROM event_participants WHERE event_id = ? AND status = 'waitlist'", (event_id,))
                position = (await cursor.fetchone())[0]
                await db.execute(
                    """
                    INSERT INTO event_participants(event_id, user_id, status, queue_position, joined_at)
                    VALUES (?, ?, 'waitlist', ?, ?)
                    ON CONFLICT(event_id, user_id)
                    DO UPDATE SET status = 'waitlist', queue_position = excluded.queue_position
                    """,
                    (event_id, user_id, position, to_db(utc_now())),
                )
                await db.commit()
                return f"Het event zit vol. Je staat op wachtlijstpositie {position}."
            await db.execute(
                """
                INSERT INTO event_participants(event_id, user_id, status, queue_position, joined_at)
                VALUES (?, ?, 'going', NULL, ?)
                ON CONFLICT(event_id, user_id)
                DO UPDATE SET status = 'going', queue_position = NULL
                """,
                (event_id, user_id, to_db(utc_now())),
            )
            await db.commit()
        return "Je doet mee."

    async def join_with_vote(
        self, interaction: discord.Interaction, event_id: int, option_id: int
    ) -> tuple[str, discord.ui.View | None]:
        """Register a planning vote and participation as one persistent action.

        The vote and participant row are committed in the same transaction so
        a successful confirmation can never mean that only half of the action
        was saved. The public dashboard is refreshed before we return.
        """
        event = await self.get_event(event_id)
        if not event or event["status"] != STATUS_PLANNING:
            return "De stemming is gesloten.", None

        valid = await self.fetchone(
            "SELECT id FROM event_time_options WHERE id = ? AND event_id = ?",
            (option_id, event_id),
        )
        if not valid:
            return "Ongeldige optie.", None

        lock = self._locks.setdefault(event_id, asyncio.Lock())
        async with lock:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                db.row_factory = aiosqlite.Row

                # Work out whether this user should be going or waitlisted.
                cursor = await db.execute(
                    "SELECT status FROM event_participants WHERE event_id = ? AND user_id = ?",
                    (event_id, interaction.user.id),
                )
                existing = await cursor.fetchone()

                cursor = await db.execute(
                    "SELECT COUNT(*) AS c FROM event_participants WHERE event_id = ? AND status = 'going'",
                    (event_id,),
                )
                going_count = int((await cursor.fetchone())["c"])
                max_slots = int(event["max_slots"] or 0)
                already_going = bool(existing and existing["status"] == "going")

                if max_slots and going_count >= max_slots and not already_going:
                    cursor = await db.execute(
                        "SELECT COALESCE(MAX(queue_position), 0) + 1 AS p FROM event_participants WHERE event_id = ? AND status = 'waitlist'",
                        (event_id,),
                    )
                    position = int((await cursor.fetchone())["p"])
                    participant_status = "waitlist"
                    queue_position = position
                    participant_message = f"Het event zit vol. Je staat op wachtlijstpositie {position}."
                else:
                    participant_status = "going"
                    queue_position = None
                    participant_message = "Je doet mee."

                now = to_db(utc_now())
                await db.execute(
                    """
                    INSERT INTO event_time_votes(event_id, option_id, user_id, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(event_id, user_id)
                    DO UPDATE SET option_id = excluded.option_id, created_at = excluded.created_at
                    """,
                    (event_id, option_id, interaction.user.id, now),
                )
                await db.execute(
                    """
                    INSERT INTO event_participants(event_id, user_id, status, queue_position, joined_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(event_id, user_id)
                    DO UPDATE SET
                        status = excluded.status,
                        queue_position = excluded.queue_position,
                        joined_at = excluded.joined_at
                    """,
                    (
                        event_id,
                        interaction.user.id,
                        participant_status,
                        queue_position,
                        now,
                    ),
                )
                await db.commit()

                # Verify what was actually persisted before telling the user it worked.
                cursor = await db.execute(
                    "SELECT status FROM event_participants WHERE event_id = ? AND user_id = ?",
                    (event_id, interaction.user.id),
                )
                saved_participant = await cursor.fetchone()
                cursor = await db.execute(
                    "SELECT option_id FROM event_time_votes WHERE event_id = ? AND user_id = ?",
                    (event_id, interaction.user.id),
                )
                saved_vote = await cursor.fetchone()

                if not saved_participant or not saved_vote or int(saved_vote["option_id"]) != option_id:
                    raise RuntimeError(
                        f"Event {event_id}: deelname/stem van gebruiker {interaction.user.id} kon niet worden bevestigd in de database"
                    )

        # Refresh the public card immediately after the committed transaction.
        await self.refresh_dashboard(event_id)

        event = await self.get_event(event_id)
        view: discord.ui.View | None = None
        extra = "Je tijdkeuze is meteen als stem opgeslagen."
        if event and event["discord_event_id"]:
            event_url = f"https://discord.com/events/{event['guild_id']}/{event['discord_event_id']}"
            view = InterestedView(event_url)
            extra += "\n\nKlik hieronder ook op de grote knop en zet jezelf in Discord op **Interested**."
        else:
            extra += "\n\nZodra de winnende tijd vaststaat verschijnt het officiële Discord-event; daar kun je jezelf op **Interested** zetten."

        return f"{participant_message}\n{extra}", view

    async def set_participant(self, interaction: discord.Interaction, event_id: int, action: str) -> None:
        event = await self.get_event(event_id)
        if not event or event["status"] in {STATUS_ENDED, STATUS_CANCELLED}:
            await interaction.response.send_message("Dit event neemt geen aanmeldingen meer aan.", ephemeral=True)
            return
        lock = self._locks.setdefault(event_id, asyncio.Lock())
        async with lock:
            if action == "leave":
                async with aiosqlite.connect(DATABASE_PATH) as db:
                    await db.execute("DELETE FROM event_participants WHERE event_id = ? AND user_id = ?", (event_id, interaction.user.id))
                    await db.execute("DELETE FROM event_time_votes WHERE event_id = ? AND user_id = ?", (event_id, interaction.user.id))
                    await db.commit()
                message = "Je bent afgemeld. Je eventuele stem is ook verwijderd."
                promoted = await self.promote_waitlist(event_id)
                if promoted:
                    await self.notify_promoted(event, promoted)
            else:
                message = await self._register_going(event_id, interaction.user.id)
        view = None
        if action != "leave" and event["discord_event_id"]:
            event_url = f"https://discord.com/events/{event['guild_id']}/{event['discord_event_id']}"
            view = InterestedView(event_url)
            message += "\n\nKlik hieronder ook op de grote knop en zet jezelf in Discord op **Interested**."
        await interaction.response.send_message(message, view=view, ephemeral=True)
        await self.refresh_dashboard(event_id)

    async def promote_waitlist(self, event_id: int) -> int | None:
        event = await self.get_event(event_id)
        if not event or not event["max_slots"]:
            return None
        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT COUNT(*) AS c FROM event_participants WHERE event_id = ? AND status = 'going'", (event_id,))
            going = (await cursor.fetchone())["c"]
            if going >= event["max_slots"]:
                return None
            cursor = await db.execute("SELECT user_id FROM event_participants WHERE event_id = ? AND status = 'waitlist' ORDER BY queue_position ASC, joined_at ASC LIMIT 1", (event_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            await db.execute("UPDATE event_participants SET status = 'going', queue_position = NULL WHERE event_id = ? AND user_id = ?", (event_id, user_id))
            cursor = await db.execute("SELECT user_id FROM event_participants WHERE event_id = ? AND status = 'waitlist' ORDER BY queue_position ASC, joined_at ASC", (event_id,))
            rows = await cursor.fetchall()
            for position, wait_row in enumerate(rows, 1):
                await db.execute("UPDATE event_participants SET queue_position = ? WHERE event_id = ? AND user_id = ?", (position, event_id, wait_row["user_id"]))
            await db.commit()
            return int(user_id)

    async def notify_promoted(self, event: aiosqlite.Row, user_id: int) -> None:
        channel = self.bot.get_channel(event["dashboard_channel_id"])
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(f"<@{user_id}> er is een plek vrijgekomen voor **{event['game_name']}**. Je staat nu bij de deelnemers.", allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            except discord.HTTPException:
                pass

    async def close_poll(self, event_id: int, guild: discord.Guild | None) -> str:
        if guild is None:
            return "Server niet gevonden."
        lock = self._locks.setdefault(event_id, asyncio.Lock())
        async with lock:
            event = await self.get_event(event_id)
            if not event:
                return "Event niet gevonden."
            if event["status"] != STATUS_PLANNING:
                return "De stemming is al gesloten."
            options = await self.get_time_options(event_id)
            if not options:
                return "Er zijn geen datumopties."
            winner = sorted(options, key=lambda item: (-int(item["votes"]), from_db(item["starts_at"])))[0]
            starts = from_db(winner["starts_at"])
            if starts is None or starts <= utc_now():
                return "De winnende datum ligt niet meer in de toekomst."
            ends = starts + timedelta(minutes=event["duration_minutes"])
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE events SET status = ?, starts_at = ?, ends_at = ?, poll_closes_at = NULL, updated_at = ? WHERE id = ?", (STATUS_CONFIRMED, to_db(starts), to_db(ends), to_db(utc_now()), event_id))
                await db.commit()
            try:
                await self.create_official_event(event_id, guild)
            except (discord.Forbidden, discord.HTTPException) as exc:
                await self.refresh_dashboard(event_id)
                return f"Stemming gesloten, maar het officiële Discord Event kon niet worden gemaakt: `{truncate(str(exc), 250)}`"
            await self.refresh_dashboard(event_id)
            return f"Stemming gesloten. Gekozen: {discord_ts(starts)}."

    # -----------------------------------------------------
    # MANAGEMENT / DASHBOARD
    # -----------------------------------------------------

    async def can_manage_event(self, interaction: discord.Interaction, event_id: int) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        if management_access(interaction.user):
            return True
        event = await self.get_event(event_id)
        if not event:
            return False
        if event["host_id"] == interaction.user.id:
            return True
        host = await self.fetchone("SELECT 1 FROM event_hosts WHERE event_id = ? AND user_id = ?", (event_id, interaction.user.id))
        return host is not None

    async def cancel_event(self, event_id: int, guild: discord.Guild | None) -> None:
        event = await self.get_event(event_id)
        if not event:
            return
        if guild and event["discord_event_id"]:
            scheduled = guild.get_scheduled_event(event["discord_event_id"])
            if scheduled:
                try:
                    await scheduled.cancel(reason=f"LEVENLOOS event #{event_id} geannuleerd")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE events SET status = ?, updated_at = ? WHERE id = ?", (STATUS_CANCELLED, to_db(utc_now()), event_id))
            await db.commit()
        await self.refresh_dashboard(event_id, disable=True)
        await self.cleanup_voice(event_id, force_if_empty=True)

    async def refresh_dashboard(self, event_id: int, *, disable: bool = False) -> None:
        event = await self.get_event(event_id)
        if not event or not event["dashboard_message_id"]:
            return

        channel_id = int(event["dashboard_channel_id"] or 0)
        channel = self.bot.get_channel(channel_id)

        # get_channel() only checks cache. Fall back to Discord's API so a
        # cache miss cannot make a successful join look as if nothing happened.
        if not isinstance(channel, discord.TextChannel):
            guild = self.bot.get_guild(int(event["guild_id"]))
            if guild is not None:
                try:
                    fetched = await guild.fetch_channel(channel_id)
                    if isinstance(fetched, discord.TextChannel):
                        channel = fetched
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[events] Kon dashboardkanaal voor event {event_id} niet ophalen: {exc}")
                    return

        if not isinstance(channel, discord.TextChannel):
            print(f"[events] Dashboardkanaal voor event {event_id} is niet beschikbaar ({channel_id}).")
            return

        try:
            message = await channel.fetch_message(int(event["dashboard_message_id"]))
            disabled = disable or event["status"] in {STATUS_ENDED, STATUS_CANCELLED}
            await message.edit(
                embed=await self.dashboard_embed(event_id),
                view=EventDashboardView(self, event_id, disabled=disabled),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            # Never hide dashboard update failures; they are essential when
            # debugging a join that was saved but not rendered.
            print(f"[events] Kon dashboard van event {event_id} niet verversen: {type(exc).__name__}: {exc}")

    async def restore_views(self) -> None:
        if self._views_restored:
            return
        rows = await self.fetchall("SELECT id, dashboard_message_id FROM events WHERE status IN ('planning', 'confirmed', 'active') AND dashboard_message_id IS NOT NULL")
        for row in rows:
            self.bot.add_view(EventDashboardView(self, row["id"]), message_id=row["dashboard_message_id"])
        self._views_restored = True

    # -----------------------------------------------------
    # VOICE / REMINDERS / MAINTENANCE
    # -----------------------------------------------------

    async def cleanup_voice(self, event_id: int, *, force_if_empty: bool = False) -> None:
        event = await self.get_event(event_id)
        if not event or not event["voice_channel_id"]:
            return
        guild = self.bot.get_guild(event["guild_id"])
        if not guild:
            return
        channel = guild.get_channel(event["voice_channel_id"])
        if not isinstance(channel, discord.VoiceChannel):
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE events SET voice_channel_id = NULL, updated_at = ? WHERE id = ?", (to_db(utc_now()), event_id))
                await db.commit()
            return
        if channel.members:
            return
        ends = from_db(event["ends_at"])
        # Normal cleanup: only after the event has ended. No fixed grace timer.
        # force_if_empty is used for cancelled/completed scheduled events.
        if not force_if_empty and (not ends or utc_now() < ends):
            return
        try:
            await channel.delete(reason=f"Tijdelijk eventkanaal #{event_id} opgeruimd")
        except (discord.Forbidden, discord.HTTPException):
            return
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE events SET voice_channel_id = NULL, updated_at = ? WHERE id = ?", (to_db(utc_now()), event_id))
            await db.commit()

    async def send_due_reminders(self, event_id: int) -> None:
        event = await self.get_event(event_id)
        if not event or event["status"] not in {STATUS_CONFIRMED, STATUS_ACTIVE}:
            return
        starts = from_db(event["starts_at"])
        if not starts:
            return
        rows = await self.fetchall("SELECT offset_minutes FROM event_reminders WHERE event_id = ? AND sent = 0 ORDER BY offset_minutes DESC", (event_id,))
        now = utc_now()
        for row in rows:
            offset = row["offset_minutes"]
            due = starts - timedelta(minutes=offset)
            if now < due:
                continue
            if now - due > timedelta(minutes=45) and offset != 0:
                await self.mark_reminder_sent(event_id, offset)
                continue
            channel = self.bot.get_channel(event["dashboard_channel_id"])
            if isinstance(channel, discord.TextChannel):
                participants = await self.fetchall("SELECT user_id FROM event_participants WHERE event_id = ? AND status IN ('going','waitlist') ORDER BY joined_at ASC", (event_id,))
                mentions = " ".join(f"<@{row['user_id']}>" for row in participants[:40])
                text = f"**{event['game_name']}** begint nu!" if offset == 0 else f"**{event['game_name']}** begint {discord_ts(starts, 'R')}."
                content = f"{mentions}\n{text}" if mentions else text
                try:
                    await channel.send(content, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
                except discord.HTTPException:
                    pass
            await self.mark_reminder_sent(event_id, offset)

    async def mark_reminder_sent(self, event_id: int, offset: int) -> None:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE event_reminders SET sent = 1 WHERE event_id = ? AND offset_minutes = ?", (event_id, offset))
            await db.commit()

    @tasks.loop(minutes=1)
    async def maintenance_loop(self) -> None:
        now = utc_now()
        rows = await self.fetchall("SELECT * FROM events WHERE status IN ('planning', 'confirmed', 'active', 'ended')")
        for event in rows:
            event_id = event["id"]
            try:
                if event["status"] == STATUS_PLANNING:
                    closes = from_db(event["poll_closes_at"])
                    if closes and now >= closes:
                        guild = self.bot.get_guild(event["guild_id"])
                        await self.close_poll(event_id, guild)
                    continue
                starts = from_db(event["starts_at"])
                ends = from_db(event["ends_at"])
                if not starts or not ends:
                    continue
                if event["status"] in {STATUS_CONFIRMED, STATUS_ACTIVE}:
                    if event["temporary_voice"] and now >= starts - timedelta(minutes=VOICE_OPEN_MINUTES) and not event["voice_channel_id"]:
                        await self.ensure_voice_channel(event_id)
                    await self.send_due_reminders(event_id)
                    if event["status"] == STATUS_CONFIRMED and now >= starts:
                        async with aiosqlite.connect(DATABASE_PATH) as db:
                            await db.execute("UPDATE events SET status = ?, updated_at = ? WHERE id = ?", (STATUS_ACTIVE, to_db(now), event_id))
                            await db.commit()
                        await self.refresh_dashboard(event_id)
                    if now >= ends:
                        async with aiosqlite.connect(DATABASE_PATH) as db:
                            await db.execute("UPDATE events SET status = ?, updated_at = ? WHERE id = ?", (STATUS_ENDED, to_db(now), event_id))
                            await db.commit()
                        await self.refresh_dashboard(event_id, disable=True)
                # Keep checking ended events until the VC becomes empty.
                if now >= ends:
                    await self.cleanup_voice(event_id)
            except Exception as exc:
                print(f"[EVENTS] Maintenance fout voor event #{event_id}: {exc}")

    @maintenance_loop.before_loop
    async def before_maintenance(self) -> None:
        await self.bot.wait_until_ready()
        await self.restore_views()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if before.channel is None or before.channel == after.channel:
            return
        row = await self.fetchone("SELECT id, ends_at, status FROM events WHERE voice_channel_id = ? ORDER BY id DESC LIMIT 1", (before.channel.id,))
        if not row or before.channel.members:
            return
        ends = from_db(row["ends_at"])
        if row["status"] in {STATUS_ENDED, STATUS_CANCELLED} or (ends and utc_now() >= ends):
            await self.cleanup_voice(row["id"], force_if_empty=True)

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent) -> None:
        row = await self.fetchone("SELECT id FROM events WHERE discord_event_id = ?", (after.id,))
        if not row:
            return
        if after.status == discord.EventStatus.active:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE events SET status = ?, updated_at = ? WHERE id = ?", (STATUS_ACTIVE, to_db(utc_now()), row["id"]))
                await db.commit()
            await self.refresh_dashboard(row["id"])
        elif after.status in {discord.EventStatus.completed, discord.EventStatus.cancelled}:
            new_status = STATUS_CANCELLED if after.status == discord.EventStatus.cancelled else STATUS_ENDED
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("UPDATE events SET status = ?, updated_at = ? WHERE id = ?", (new_status, to_db(utc_now()), row["id"]))
                await db.commit()
            await self.refresh_dashboard(row["id"], disable=True)
            await self.cleanup_voice(row["id"], force_if_empty=True)

    # -----------------------------------------------------
    # SLASH COMMANDS
    # -----------------------------------------------------

    @event.command(name="setup", description="Maak een nieuw event.")
    async def event_setup(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel_id is None:
            await interaction.response.send_message("Dit command werkt alleen in een server.", ephemeral=True)
            return
        member = interaction.user
        event_role = interaction.guild.get_role(EVENT_CREATOR_ROLE_ID)
        has_event_role = isinstance(member, discord.Member) and any(role.id == EVENT_CREATOR_ROLE_ID for role in member.roles)
        if not has_event_role:
            await interaction.response.send_message(f"Alleen leden met de {event_role.mention if event_role else '<@&985989826950086717>'} rol kunnen events aanmaken.", ephemeral=True)
            return
        state = WizardState(guild_id=interaction.guild.id, author_id=interaction.user.id, channel_id=interaction.channel_id)
        await interaction.response.send_message(embed=self.wizard_embed(state), view=SetupWizardView(self, state), ephemeral=True)

    @event.command(name="beheer", description="Open het beheer van een event.")
    @app_commands.describe(event_id="Het nummer onderaan het eventdashboard")
    async def event_manage(self, interaction: discord.Interaction, event_id: int) -> None:
        if not await self.can_manage_event(interaction, event_id):
            await interaction.response.send_message("Event niet gevonden of je hebt geen toegang.", ephemeral=True)
            return
        await interaction.response.send_message(embed=await self.management_embed(event_id), view=EventManagementView(self, event_id), ephemeral=True)

    @event.command(name="lijst", description="Bekijk geplande events.")
    async def event_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Dit command werkt alleen in een server.", ephemeral=True)
            return
        rows = await self.fetchall("SELECT id, game_name, status, starts_at FROM events WHERE guild_id = ? AND status IN ('planning', 'confirmed', 'active') ORDER BY CASE WHEN starts_at IS NULL THEN 1 ELSE 0 END, starts_at ASC, id DESC LIMIT 15", (interaction.guild.id,))
        if not rows:
            await interaction.response.send_message("Er zijn geen actieve events.", ephemeral=True)
            return
        lines = [f"`#{row['id']}` **{row['game_name']}** · {status_label(row['status'])} · {discord_ts(from_db(row['starts_at']), 'f')}" for row in rows]
        await interaction.response.send_message(embed=discord.Embed(title="EVENTS", description="\n".join(lines), color=BRAND_COLOR), ephemeral=True)

    @event.command(name="config", description="Configureer het eventsysteem.")
    async def event_config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Dit command werkt alleen in een server.", ephemeral=True)
            return
        if not management_access(interaction.user):
            await interaction.response.send_message("Geen toegang.", ephemeral=True)
            return
        config = await self.get_config(interaction.guild.id)
        event_channel = interaction.guild.get_channel(config["event_channel_id"]) if config and config["event_channel_id"] else None
        voice_category = interaction.guild.get_channel(config["voice_category_id"]) if config and config["voice_category_id"] else None
        embed = discord.Embed(title="EVENT CONFIG", description="Stel de standaardlocaties voor events in.", color=BRAND_COLOR)
        embed.add_field(name="Dashboardkanaal", value=event_channel.mention if isinstance(event_channel, discord.TextChannel) else "Niet ingesteld", inline=False)
        embed.add_field(name="Voicecategorie", value=voice_category.name if isinstance(voice_category, discord.CategoryChannel) else "Niet ingesteld", inline=False)
        await interaction.response.send_message(embed=embed, view=EventConfigView(self, interaction.guild.id), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
