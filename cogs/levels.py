from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = ROOT / "levels.db"
IMPORT_PATH = ROOT / "activityrank_import.json"

LEADERBOARD_BADGE = (
    "<:l1:1546517649377333248><:l2:1546517693144895579>"
    "<:l3:1546517717081788426><:l4:1546517745137623133>"
    "<:l5:1546517778410774618><:l6:1546517822048309330>"
    "<:l7:1546517850452140032><:l8:1546517874875703417>"
    "<:l9:1546517902629539880><:l10:1546517922317598743>"
)
LEVEL_BADGE = (
    "<:le1:1546518160268853258><:le2:1546518185073709127>"
    "<:le3:1546518207429345371><:le4:1546518229239730217>"
    "<:le5:1546518249997475880>"
)
LEVEL_UP_ICON = "<:lvl:1546517601167999127>"

DEFAULTS = {
    "xp_text_enabled": "1",
    "xp_text": "10",
    "text_cooldown": "5",
    "xp_voice_enabled": "1",
    "xp_voice_per_minute": "5",
    "voice_solo": "0",
    "xp_invite_enabled": "1",
    "xp_invite": "250",
    "xp_reaction_enabled": "1",
    "xp_reaction": "10",
    "xp_wave_enabled": "1",
    "xp_wave": "150",
    "xp_congrats_enabled": "1",
    "xp_congrats": "100",
    "xp_birthday_enabled": "1",
    "xp_birthday": "500",
    "xp_game_vote_enabled": "1",
    "xp_game_vote": "100",
    "xp_game_suggestion_enabled": "1",
    "xp_game_suggestion": "75",
    "xp_game_success_enabled": "1",
    "xp_game_success": "500",
    "levelup_enabled": "0",
    "levelup_channel_id": "0",
    "level_roles_enabled": "0",
}

SOURCE_LABELS = {
    "text": "Tekstbericht",
    "voice": "Voice",
    "invite": "Invite",
    "reaction": "Reaction",
    "wave": "Zwaaien",
    "congrats": "Feliciteren",
    "birthday": "Jarig zijn",
    "game_vote": "Game vote",
    "game_suggestion": "Game suggestie",
    "game_success": "Succesvolle game",
    "manual": "Handmatige wijziging",
    "legacy_import": "ActivityRank import",
}

XP_KEYS = {
    "text": ("xp_text_enabled", "xp_text"),
    "voice": ("xp_voice_enabled", "xp_voice_per_minute"),
    "invite": ("xp_invite_enabled", "xp_invite"),
    "reaction": ("xp_reaction_enabled", "xp_reaction"),
    "wave": ("xp_wave_enabled", "xp_wave"),
    "congrats": ("xp_congrats_enabled", "xp_congrats"),
    "birthday": ("xp_birthday_enabled", "xp_birthday"),
    "game_vote": ("xp_game_vote_enabled", "xp_game_vote"),
    "game_suggestion": ("xp_game_suggestion_enabled", "xp_game_suggestion"),
    "game_success": ("xp_game_success_enabled", "xp_game_success"),
}


def xp_for_level(level: int) -> int:
    if level <= 1:
        return 0
    return 100 * ((level * (level + 1) // 2) - 1)


def level_from_xp(total_xp: int) -> int:
    total_xp = max(0, int(total_xp))
    # Solve L(L+1)/2 <= total/100 + 1
    n = total_xp / 100 + 1
    level = max(1, int((math.sqrt(1 + 8 * n) - 1) // 2))
    while xp_for_level(level + 1) <= total_xp:
        level += 1
    while level > 1 and xp_for_level(level) > total_xp:
        level -= 1
    return level


def fmt_num(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def fmt_hours(seconds: int) -> str:
    return f"{seconds / 3600:.1f}".replace(".", ",")


def is_admin(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id or member.guild_permissions.administrator:
        return True
    return any(role.name.lower() in {"admin", "owner"} for role in member.roles)


async def init_db() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS levels_users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                total_xp INTEGER NOT NULL DEFAULT 0,
                text_messages INTEGER NOT NULL DEFAULT 0,
                voice_seconds INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS levels_config (
                guild_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            );
            CREATE TABLE IF NOT EXISTS levels_roles (
                guild_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                keep_previous INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, level)
            );
            CREATE TABLE IF NOT EXISTS levels_xp_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                xp INTEGER NOT NULL,
                reference_id TEXT,
                actor_id INTEGER,
                created_at INTEGER NOT NULL,
                UNIQUE (guild_id, source, reference_id)
            );
            CREATE TABLE IF NOT EXISTS levels_exclusions (
                guild_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, kind, target_id)
            );
            CREATE TABLE IF NOT EXISTS levels_reaction_awards (
                guild_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                reactor_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, message_id, reactor_id)
            );
            CREATE TABLE IF NOT EXISTS levels_imports (
                guild_id INTEGER NOT NULL,
                import_name TEXT NOT NULL,
                imported_at INTEGER NOT NULL,
                rows_imported INTEGER NOT NULL,
                PRIMARY KEY (guild_id, import_name)
            );
            """
        )
        await db.commit()


class ValueModal(discord.ui.Modal):
    def __init__(self, cog: "Levels", owner_id: int, guild_id: int, source: str, current: int):
        super().__init__(title=f"{SOURCE_LABELS[source]} XP aanpassen")
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.source = source
        self.value = discord.ui.TextInput(
            label="Nieuwe XP-waarde",
            default=str(current),
            required=True,
            max_length=7,
        )
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dit menu is niet van jou.", ephemeral=True)
            return
        try:
            new = int(str(self.value.value).strip())
            if new < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Vul een geldig positief getal in.", ephemeral=True)
            return
        _, value_key = XP_KEYS[self.source]
        old = await self.cog.get_int(self.guild_id, value_key)
        await self.cog.set_config(self.guild_id, value_key, str(new))
        await interaction.response.send_message(
            f"✅ {SOURCE_LABELS[self.source]} is aangepast van **{old} XP** naar **{new} XP**.",
            ephemeral=True,
        )


class CooldownModal(discord.ui.Modal):
    def __init__(self, cog: "Levels", owner_id: int, guild_id: int, current: int):
        super().__init__(title="Message XP cooldown")
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.value = discord.ui.TextInput(label="Aantal seconden", default=str(current), max_length=5)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(str(self.value.value).strip())
            if value < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Vul een geldig aantal seconden in.", ephemeral=True)
            return
        await self.cog.set_config(self.guild_id, "text_cooldown", str(value))
        await interaction.response.send_message(
            f"✅ De message XP cooldown is ingesteld op **{value} seconden**.", ephemeral=True
        )


class ManualXpModal(discord.ui.Modal):
    def __init__(self, cog: "Levels", guild_id: int, user_id: int, mode: str):
        title = {"add": "XP geven", "remove": "XP afnemen", "set": "XP instellen"}[mode]
        super().__init__(title=title)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.mode = mode
        self.value = discord.ui.TextInput(label="Aantal XP", required=True, max_length=12)
        self.add_item(self.value)

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om het levelsysteem te beheren.", ephemeral=True)
            return
        try:
            amount = int(str(self.value.value).strip())
            if amount < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Vul een geldig positief getal in.", ephemeral=True)
            return
        row = await self.cog.get_user(self.guild_id, self.user_id)
        current = row[0] if row else 0
        if self.mode == "add":
            new_total = current + amount
            delta = amount
        elif self.mode == "remove":
            if amount > current:
                await interaction.response.send_message("❌ Een gebruiker kan niet onder 0 XP komen.", ephemeral=True)
                return
            new_total = current - amount
            delta = -amount
        else:
            new_total = amount
            delta = new_total - current
        await self.cog.set_total_xp(self.guild_id, self.user_id, new_total)
        if delta:
            await self.cog.log_xp(self.guild_id, self.user_id, "manual", delta, f"manual:{interaction.id}", interaction.user.id)
        member = interaction.guild.get_member(self.user_id) if interaction.guild else None
        mention = member.mention if member else f"<@{self.user_id}>"
        await interaction.response.send_message(
            f"✅ XP van {mention} staat nu op **{fmt_num(new_total)} XP**.", ephemeral=True
        )
        if member:
            await self.cog.apply_level_roles(member)


class ConfigView(discord.ui.View):
    def __init__(self, cog: "Levels", owner_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dit menu is niet van jou.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✨ XP-functies", style=discord.ButtonStyle.primary, row=0)
    async def xp_functions(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=await self.cog.render_toggle_panel(self.guild_id), view=XpToggleView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="🔢 XP-waardes", style=discord.ButtonStyle.secondary, row=0)
    async def xp_values(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=await self.cog.render_values_panel(self.guild_id), view=XpValuesView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="💬 Tekst", style=discord.ButtonStyle.secondary, row=1)
    async def text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=await self.cog.render_text_panel(self.guild_id), view=TextConfigView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="🔊 Voice", style=discord.ButtonStyle.secondary, row=1)
    async def voice(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=await self.cog.render_voice_panel(self.guild_id), view=VoiceConfigView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="🏅 Levelrollen", style=discord.ButtonStyle.secondary, row=1)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        enabled = await self.cog.get_bool(self.guild_id, "level_roles_enabled")
        roles = await self.cog.get_level_roles(self.guild_id)
        lines = ["🏅 **LEVELROLLEN**", "", f"Automatisch uitdelen: {'🟢 AAN' if enabled else '⚫ UIT'}"]
        if roles:
            lines += ["", *[f"Level **{lvl}** → <@&{rid}>" for lvl, rid, _ in roles]]
        else:
            lines += ["", "Nog geen levelrollen opgeslagen."]
        await interaction.response.edit_message(content="\n".join(lines), view=RolesConfigView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="📢 Meldingen", style=discord.ButtonStyle.secondary, row=2)
    async def notifications(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=await self.cog.render_notifications(self.guild_id), view=NotificationsView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="🚫 Uitsluitingen", style=discord.ButtonStyle.secondary, row=2)
    async def exclusions(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=await self.cog.render_exclusions(self.guild_id), view=ExclusionsView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="👤 Gebruiker", style=discord.ButtonStyle.secondary, row=3)
    async def user_manage(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="👤 **GEBRUIKER BEHEREN**\n\nKies een gebruiker.",
            view=UserPickerView(self.cog, self.owner_id, self.guild_id),
        )

    @discord.ui.button(label="📊 Status", style=discord.ButtonStyle.secondary, row=2)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content=await self.cog.render_status(self.guild_id), view=BackView(self.cog, self.owner_id, self.guild_id))


class BackView(discord.ui.View):
    def __init__(self, cog, owner_id, guild_id):
        super().__init__(timeout=300)
        self.cog, self.owner_id, self.guild_id = cog, owner_id, guild_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dit menu is niet van jou.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="↩️ Terug", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))


class XpToggleView(BackView):
    def __init__(self, cog, owner_id, guild_id):
        super().__init__(cog, owner_id, guild_id)
        self.clear_items()
        for idx, source in enumerate(XP_KEYS):
            b = discord.ui.Button(label=SOURCE_LABELS[source], style=discord.ButtonStyle.secondary, row=idx // 5)
            async def callback(interaction: discord.Interaction, src=source):
                key, _ = XP_KEYS[src]
                current = await self.cog.get_bool(self.guild_id, key)
                await self.cog.set_config(self.guild_id, key, "0" if current else "1")
                await interaction.response.edit_message(content=await self.cog.render_toggle_panel(self.guild_id), view=XpToggleView(self.cog, self.owner_id, self.guild_id))
            b.callback = callback
            self.add_item(b)
        back = discord.ui.Button(label="↩️ Terug", style=discord.ButtonStyle.secondary, row=2)
        async def back_cb(interaction):
            await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))
        back.callback = back_cb
        self.add_item(back)


class XpValuesView(BackView):
    def __init__(self, cog, owner_id, guild_id):
        super().__init__(cog, owner_id, guild_id)
        self.clear_items()
        for idx, source in enumerate(XP_KEYS):
            b = discord.ui.Button(label=SOURCE_LABELS[source], style=discord.ButtonStyle.secondary, row=idx // 5)
            async def callback(interaction: discord.Interaction, src=source):
                _, value_key = XP_KEYS[src]
                current = await self.cog.get_int(self.guild_id, value_key)
                await interaction.response.send_modal(ValueModal(self.cog, self.owner_id, self.guild_id, src, current))
            b.callback = callback
            self.add_item(b)
        back = discord.ui.Button(label="↩️ Terug", style=discord.ButtonStyle.secondary, row=2)
        async def back_cb(interaction):
            await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))
        back.callback = back_cb
        self.add_item(back)


class TextConfigView(BackView):
    @discord.ui.button(label="🔢 XP aanpassen", style=discord.ButtonStyle.primary)
    async def xp(self, interaction, button):
        current = await self.cog.get_int(self.guild_id, "xp_text")
        await interaction.response.send_modal(ValueModal(self.cog, self.owner_id, self.guild_id, "text", current))

    @discord.ui.button(label="⏱️ Cooldown aanpassen", style=discord.ButtonStyle.secondary)
    async def cooldown(self, interaction, button):
        current = await self.cog.get_int(self.guild_id, "text_cooldown")
        await interaction.response.send_modal(CooldownModal(self.cog, self.owner_id, self.guild_id, current))

    @discord.ui.button(label="↩️ Terug", style=discord.ButtonStyle.secondary)
    async def back2(self, interaction, button):
        await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))


class VoiceConfigView(BackView):
    @discord.ui.button(label="🔢 XP aanpassen", style=discord.ButtonStyle.primary)
    async def xp(self, interaction, button):
        current = await self.cog.get_int(self.guild_id, "xp_voice_per_minute")
        await interaction.response.send_modal(ValueModal(self.cog, self.owner_id, self.guild_id, "voice", current))

    @discord.ui.button(label="👤 Solo voice aan/uit", style=discord.ButtonStyle.secondary)
    async def solo(self, interaction, button):
        current = await self.cog.get_bool(self.guild_id, "voice_solo")
        await self.cog.set_config(self.guild_id, "voice_solo", "0" if current else "1")
        await self.cog.refresh_all_voice(self.guild_id)
        await interaction.response.edit_message(content=await self.cog.render_voice_panel(self.guild_id), view=VoiceConfigView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="↩️ Terug", style=discord.ButtonStyle.secondary)
    async def back2(self, interaction, button):
        await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))


class RolesConfigView(BackView):
    @discord.ui.button(label="🔒 Auto-rollen aan/uit", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction, button):
        current = await self.cog.get_bool(self.guild_id, "level_roles_enabled")
        await self.cog.set_config(self.guild_id, "level_roles_enabled", "0" if current else "1")
        await interaction.response.send_message(f"✅ Automatische levelrollen zijn nu **{'UIT' if current else 'AAN'}**.", ephemeral=True)

    @discord.ui.button(label="🔄 Rollen controleren", style=discord.ButtonStyle.primary)
    async def sync(self, interaction, button):
        if interaction.guild:
            changed = 0
            for member in interaction.guild.members:
                if not member.bot:
                    changed += await self.cog.apply_level_roles(member)
            await interaction.response.send_message(f"✅ Levelrollen gecontroleerd. **{changed}** wijziging(en).", ephemeral=True)

    @discord.ui.button(label="↩️ Terug", style=discord.ButtonStyle.secondary)
    async def back2(self, interaction, button):
        await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))


class NotificationsView(BackView):
    @discord.ui.button(label="🔔 Level-up aan/uit", style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction, button):
        current = await self.cog.get_bool(self.guild_id, "levelup_enabled")
        await self.cog.set_config(self.guild_id, "levelup_enabled", "0" if current else "1")
        await interaction.response.edit_message(content=await self.cog.render_notifications(self.guild_id), view=NotificationsView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="📍 Gebruik dit kanaal", style=discord.ButtonStyle.primary)
    async def channel(self, interaction, button):
        await self.cog.set_config(self.guild_id, "levelup_channel_id", str(interaction.channel_id))
        await interaction.response.edit_message(content=await self.cog.render_notifications(self.guild_id), view=NotificationsView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="↩️ Terug", style=discord.ButtonStyle.secondary)
    async def back2(self, interaction, button):
        await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))


class ExclusionsView(BackView):
    def __init__(self, cog, owner_id, guild_id):
        super().__init__(cog, owner_id, guild_id)
        self.add_item(ExclusionRoleSelect(cog, owner_id, guild_id))

    @discord.ui.button(label="💬 Dit tekstkanaal", style=discord.ButtonStyle.secondary)
    async def text_channel(self, interaction, button):
        await self.cog.toggle_exclusion(self.guild_id, "text_channel", interaction.channel_id)
        await interaction.response.edit_message(content=await self.cog.render_exclusions(self.guild_id), view=ExclusionsView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="🔊 Mijn voicekanaal", style=discord.ButtonStyle.secondary)
    async def voice_channel(self, interaction, button):
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Je zit niet in een voicekanaal.", ephemeral=True)
            return
        await self.cog.toggle_exclusion(self.guild_id, "voice_channel", interaction.user.voice.channel.id)
        await interaction.response.edit_message(content=await self.cog.render_exclusions(self.guild_id), view=ExclusionsView(self.cog, self.owner_id, self.guild_id))

    @discord.ui.button(label="↩️ Terug", style=discord.ButtonStyle.secondary)
    async def back2(self, interaction, button):
        await interaction.response.edit_message(content=self.cog.config_home_text(), view=ConfigView(self.cog, self.owner_id, self.guild_id))


class UserPicker(discord.ui.UserSelect):
    def __init__(self, cog, owner_id, guild_id):
        super().__init__(placeholder="Kies een gebruiker...", min_values=1, max_values=1, row=0)
        self.cog, self.owner_id, self.guild_id = cog, owner_id, guild_id

    async def callback(self, interaction: discord.Interaction):
        user = self.values[0]
        if getattr(user, "bot", False):
            await interaction.response.send_message("❌ Bots nemen niet deel aan het levelsysteem.", ephemeral=True)
            return
        member = interaction.guild.get_member(user.id) if interaction.guild else None
        if member is None:
            await interaction.response.send_message("❌ Deze gebruiker kon niet worden gevonden.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=await self.cog.render_user_manage(member),
            view=UserManageView(self.cog, self.owner_id, self.guild_id, member.id),
        )


class UserPickerView(BackView):
    def __init__(self, cog, owner_id, guild_id):
        super().__init__(cog, owner_id, guild_id)
        self.clear_items()
        self.add_item(UserPicker(cog, owner_id, guild_id))
        back = discord.ui.Button(label="↩️ Terug", style=discord.ButtonStyle.secondary, row=1)
        async def back_cb(interaction):
            await interaction.response.edit_message(content=cog.config_home_text(), view=ConfigView(cog, owner_id, guild_id))
        back.callback = back_cb
        self.add_item(back)


class UserManageView(BackView):
    def __init__(self, cog, owner_id, guild_id, user_id):
        super().__init__(cog, owner_id, guild_id)
        self.user_id = user_id

    @discord.ui.button(label="➕ XP geven", style=discord.ButtonStyle.success)
    async def add(self, interaction, button):
        await interaction.response.send_modal(ManualXpModal(self.cog, self.guild_id, self.user_id, "add"))

    @discord.ui.button(label="➖ XP afnemen", style=discord.ButtonStyle.danger)
    async def remove(self, interaction, button):
        await interaction.response.send_modal(ManualXpModal(self.cog, self.guild_id, self.user_id, "remove"))

    @discord.ui.button(label="✏️ XP instellen", style=discord.ButtonStyle.primary)
    async def set(self, interaction, button):
        await interaction.response.send_modal(ManualXpModal(self.cog, self.guild_id, self.user_id, "set"))

    @discord.ui.button(label="📋 XP geschiedenis", style=discord.ButtonStyle.secondary, row=1)
    async def history(self, interaction, button):
        await interaction.response.send_message(await self.cog.render_history(self.guild_id, self.user_id), ephemeral=True)

    @discord.ui.button(label="↩️ Terug", style=discord.ButtonStyle.secondary, row=1)
    async def back_user(self, interaction, button):
        await interaction.response.edit_message(content="👤 **GEBRUIKER BEHEREN**\n\nKies een gebruiker.", view=UserPickerView(self.cog, self.owner_id, self.guild_id))


class ExclusionRoleSelect(discord.ui.RoleSelect):
    def __init__(self, cog, owner_id, guild_id):
        super().__init__(placeholder="Rol uitsluiten / weer toestaan...", min_values=1, max_values=1, row=1)
        self.cog, self.owner_id, self.guild_id = cog, owner_id, guild_id

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await self.cog.toggle_exclusion(self.guild_id, "role", role.id)
        await interaction.response.edit_message(content=await self.cog.render_exclusions(self.guild_id), view=ExclusionsView(self.cog, self.owner_id, self.guild_id))


class LeaderboardView(discord.ui.View):
    def __init__(self, cog: "Levels", guild_id: int, user_id: int, sort_key: str = "xp", page: int = 0):
        super().__init__(timeout=180)
        self.cog, self.guild_id, self.user_id = cog, guild_id, user_id
        self.sort_key, self.page = sort_key, page

    async def refresh(self, interaction: discord.Interaction):
        embed, pages = await self.cog.render_leaderboard(self.guild_id, self.sort_key, self.page)
        self.page = min(self.page, max(0, pages - 1))

        # Laat direct zien welk filter actief is.
        self.xp.style = discord.ButtonStyle.primary if self.sort_key == "xp" else discord.ButtonStyle.secondary
        self.messages.style = discord.ButtonStyle.primary if self.sort_key == "messages" else discord.ButtonStyle.secondary
        self.voice.style = discord.ButtonStyle.primary if self.sort_key == "voice" else discord.ButtonStyle.secondary

        await interaction.response.edit_message(content=None, embed=embed, view=self)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction, button):
        self.page = max(0, self.page - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="🏆 XP", style=discord.ButtonStyle.primary)
    async def xp(self, interaction, button):
        self.sort_key, self.page = "xp", 0
        await self.refresh(interaction)

    @discord.ui.button(label="💬 Berichten", style=discord.ButtonStyle.secondary)
    async def messages(self, interaction, button):
        self.sort_key, self.page = "messages", 0
        await self.refresh(interaction)

    @discord.ui.button(label="🔊 Voice", style=discord.ButtonStyle.secondary)
    async def voice(self, interaction, button):
        self.sort_key, self.page = "voice", 0
        await self.refresh(interaction)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        _, pages = await self.cog.render_leaderboard(self.guild_id, self.sort_key, self.page)
        self.page = min(max(0, pages - 1), self.page + 1)
        await self.refresh(interaction)

    @discord.ui.button(label="👤 Mijn positie", style=discord.ButtonStyle.secondary, row=1)
    async def mine(self, interaction, button):
        page = await self.cog.page_for_user(self.guild_id, self.user_id, self.sort_key)
        self.page = page
        await self.refresh(interaction)


class ImportPreviewView(discord.ui.View):
    PAGE_SIZE = 20

    def __init__(self, cog: "Levels", owner_id: int, guild_id: int, matched, unmatched, duplicates, page: int = 0):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.matched = matched
        self.unmatched = unmatched
        self.duplicates = duplicates
        self.page = page

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dit menu is niet van jou.", ephemeral=True)
            return False
        return True

    def render(self):
        total_pages = max(1, math.ceil(len(self.matched) / self.PAGE_SIZE))
        self.page = max(0, min(self.page, total_pages - 1))
        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE

        lines = [
            "📥 **ACTIVITYRANK IMPORT PREVIEW**",
            "",
            f"✅ {len(self.matched)} leden gekoppeld",
            f"⚠️ {len(self.unmatched)} niet gevonden",
            f"❌ {len(self.duplicates)} onduidelijke matches",
            "",
            "Er is nog niets aangepast.",
            "",
            f"**Matches {start + 1 if self.matched else 0}-{min(end, len(self.matched))} van {len(self.matched)}:**",
        ]

        for row, member in self.matched[start:end]:
            lines.append(f"✅ {row['username']} → {member.mention}")

        if self.unmatched:
            lines += ["", "**Niet gevonden:**"]
            lines.extend(f"⚠️ {row['username']}" for row in self.unmatched)

        if self.duplicates:
            lines += ["", "**Onduidelijke matches:**"]
            for row, candidates in self.duplicates:
                names = ", ".join(member.mention for member in candidates[:5])
                lines.append(f"❌ {row['username']} → {names}")

        lines += ["", f"Pagina **{self.page + 1}/{total_pages}**"]
        return "\n".join(lines)[:1990]

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = max(1, math.ceil(len(self.matched) / self.PAGE_SIZE))
        self.page = min(total_pages - 1, self.page + 1)
        await interaction.response.edit_message(content=self.render(), view=self)


class ImportConfirmView(discord.ui.View):
    def __init__(self, cog: "Levels", owner_id: int, guild_id: int):
        super().__init__(timeout=60)
        self.cog, self.owner_id, self.guild_id = cog, owner_id, guild_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Dit menu is niet van jou.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Importeren", style=discord.ButtonStyle.success)
    async def go(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        result = await self.cog.run_import(interaction.guild)
        await interaction.followup.send(result, ephemeral=True)
        self.stop()

    @discord.ui.button(label="❌ Annuleren", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(content="❌ Import geannuleerd.", view=None)
        self.stop()


class Levels(commands.Cog):
    levels = app_commands.Group(name="levels", description="Beheer het LEVENLOOS levelsysteem.")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.message_last_xp: dict[tuple[int, int], float] = {}
        self.voice_sessions: dict[tuple[int, int], tuple[int, float]] = {}
        self.invite_cache: dict[int, dict[str, tuple[int, int]]] = {}
        self._voice_lock = asyncio.Lock()

    async def cog_load(self):
        await init_db()
        for guild in self.bot.guilds:
            await self.ensure_defaults(guild.id)
        asyncio.create_task(self._delayed_ready())

    async def _delayed_ready(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self.ensure_defaults(guild.id)
            await self.seed_default_roles(guild)
            await self.cache_invites(guild)
        await self.refresh_all_voice()

    async def ensure_defaults(self, guild_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            for key, value in DEFAULTS.items():
                await db.execute("INSERT OR IGNORE INTO levels_config (guild_id,key,value) VALUES (?,?,?)", (guild_id, key, value))
            await db.commit()

    async def get_config(self, guild_id: int, key: str) -> str:
        await self.ensure_defaults(guild_id)
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT value FROM levels_config WHERE guild_id=? AND key=?", (guild_id, key))
            row = await cur.fetchone()
        return row[0] if row else DEFAULTS.get(key, "0")

    async def set_config(self, guild_id: int, key: str, value: str):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("INSERT INTO levels_config (guild_id,key,value) VALUES (?,?,?) ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value", (guild_id, key, value))
            await db.commit()

    async def get_int(self, guild_id: int, key: str) -> int:
        try:
            return int(await self.get_config(guild_id, key))
        except ValueError:
            return 0

    async def get_bool(self, guild_id: int, key: str) -> bool:
        return (await self.get_config(guild_id, key)) == "1"

    async def ensure_user(self, guild_id: int, user_id: int):
        now = int(time.time())
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO levels_users (guild_id,user_id,created_at,updated_at) VALUES (?,?,?,?)", (guild_id, user_id, now, now))
            await db.commit()

    async def get_user(self, guild_id: int, user_id: int):
        await self.ensure_user(guild_id, user_id)
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT total_xp,text_messages,voice_seconds FROM levels_users WHERE guild_id=? AND user_id=?", (guild_id, user_id))
            return await cur.fetchone()

    async def set_total_xp(self, guild_id: int, user_id: int, total: int):
        await self.ensure_user(guild_id, user_id)
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE levels_users SET total_xp=?,updated_at=? WHERE guild_id=? AND user_id=?", (max(0, total), int(time.time()), guild_id, user_id))
            await db.commit()

    async def log_xp(self, guild_id, user_id, source, xp, reference_id=None, actor_id=None) -> bool:
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("INSERT INTO levels_xp_log (guild_id,user_id,source,xp,reference_id,actor_id,created_at) VALUES (?,?,?,?,?,?,?)", (guild_id, user_id, source, xp, reference_id, actor_id, int(time.time())))
                await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def award_source(self, guild: discord.Guild, user_id: int, source: str, reference_id: Optional[str] = None, actor_id: Optional[int] = None) -> int:
        if source not in XP_KEYS:
            return 0
        enabled_key, value_key = XP_KEYS[source]
        if not await self.get_bool(guild.id, enabled_key):
            return 0
        amount = await self.get_int(guild.id, value_key)
        return await self.award_xp(guild, user_id, amount, source, reference_id, actor_id)

    async def award_xp(self, guild: discord.Guild, user_id: int, amount: int, source: str, reference_id: Optional[str] = None, actor_id: Optional[int] = None) -> int:
        if amount == 0:
            return 0
        member = guild.get_member(user_id)
        if member is not None and member.bot:
            return 0
        if reference_id is not None:
            if not await self.log_xp(guild.id, user_id, source, amount, reference_id, actor_id):
                return 0
        elif source not in {"text", "voice"}:
            await self.log_xp(guild.id, user_id, source, amount, None, actor_id)
        await self.ensure_user(guild.id, user_id)
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT total_xp FROM levels_users WHERE guild_id=? AND user_id=?", (guild.id, user_id))
            old_total = (await cur.fetchone())[0]
            new_total = max(0, old_total + amount)
            await db.execute("UPDATE levels_users SET total_xp=?,updated_at=? WHERE guild_id=? AND user_id=?", (new_total, int(time.time()), guild.id, user_id))
            await db.commit()
        if member and level_from_xp(new_total) > level_from_xp(old_total):
            await self.on_level_up(member, level_from_xp(old_total), level_from_xp(new_total))
        return amount

    async def on_level_up(self, member: discord.Member, old_level: int, new_level: int):
        await self.apply_level_roles(member)
        if not await self.get_bool(member.guild.id, "levelup_enabled"):
            return
        channel_id = await self.get_int(member.guild.id, "levelup_channel_id")
        channel = member.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await channel.send(f"{LEVEL_UP_ICON} {member.mention} heeft level **{new_level}** bereikt!")

    async def excluded(self, guild_id: int, kind: str, target_id: int) -> bool:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT 1 FROM levels_exclusions WHERE guild_id=? AND kind=? AND target_id=?", (guild_id, kind, target_id))
            return await cur.fetchone() is not None

    async def toggle_exclusion(self, guild_id: int, kind: str, target_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT 1 FROM levels_exclusions WHERE guild_id=? AND kind=? AND target_id=?", (guild_id, kind, target_id))
            if await cur.fetchone():
                await db.execute("DELETE FROM levels_exclusions WHERE guild_id=? AND kind=? AND target_id=?", (guild_id, kind, target_id))
            else:
                await db.execute("INSERT INTO levels_exclusions (guild_id,kind,target_id) VALUES (?,?,?)", (guild_id, kind, target_id))
            await db.commit()

    async def member_excluded(self, member: discord.Member) -> bool:
        for role in member.roles:
            if await self.excluded(member.guild.id, "role", role.id):
                return True
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not isinstance(message.author, discord.Member):
            return
        await self.ensure_user(message.guild.id, message.author.id)
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE levels_users SET text_messages=text_messages+1,updated_at=? WHERE guild_id=? AND user_id=?", (int(time.time()), message.guild.id, message.author.id))
            await db.commit()
        if not await self.get_bool(message.guild.id, "xp_text_enabled"):
            return
        if await self.excluded(message.guild.id, "text_channel", message.channel.id) or await self.member_excluded(message.author):
            return
        cooldown = await self.get_int(message.guild.id, "text_cooldown")
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self.message_last_xp.get(key, 0) < cooldown:
            return
        self.message_last_xp[key] = now
        await self.award_xp(message.guild, message.author.id, await self.get_int(message.guild.id, "xp_text"), "text")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or self.bot.user and payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild or not await self.get_bool(guild.id, "xp_reaction_enabled"):
            return
        channel = guild.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel) or await self.excluded(guild.id, "text_channel", channel.id):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
        if message.author.bot or message.author.id == payload.user_id:
            return
        reactor = guild.get_member(payload.user_id)
        if reactor is None or reactor.bot or await self.member_excluded(reactor):
            return
        try:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                await db.execute("INSERT INTO levels_reaction_awards (guild_id,message_id,reactor_id,author_id,created_at) VALUES (?,?,?,?,?)", (guild.id, message.id, payload.user_id, message.author.id, int(time.time())))
                await db.commit()
        except aiosqlite.IntegrityError:
            return
        await self.award_source(guild, message.author.id, "reaction", f"reaction:{message.id}:{payload.user_id}", payload.user_id)

    async def settle_voice_channels(self, guild: discord.Guild, channel_ids: set[int]):
        async with self._voice_lock:
            now = time.monotonic()
            affected = [(key, data) for key, data in list(self.voice_sessions.items()) if key[0] == guild.id and data[0] in channel_ids]
            for (gid, uid), (cid, started) in affected:
                delta = max(0, int(now - started))
                if delta:
                    await self.add_voice_seconds(guild, uid, delta)
                self.voice_sessions.pop((gid, uid), None)
            for cid in channel_ids:
                channel = guild.get_channel(cid)
                if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                    continue
                valid = [m for m in channel.members if not m.bot and not await self.member_excluded(m)]
                if await self.excluded(guild.id, "voice_channel", cid):
                    valid = []
                solo = await self.get_bool(guild.id, "voice_solo")
                if not solo and len(valid) < 2:
                    valid = []
                for member in valid:
                    self.voice_sessions[(guild.id, member.id)] = (cid, now)

    async def add_voice_seconds(self, guild: discord.Guild, user_id: int, seconds: int):
        await self.ensure_user(guild.id, user_id)
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT voice_seconds,total_xp FROM levels_users WHERE guild_id=? AND user_id=?", (guild.id, user_id))
            old_seconds, old_xp = await cur.fetchone()
            new_seconds = old_seconds + seconds
            xp_add = 0
            if await self.get_bool(guild.id, "xp_voice_enabled"):
                rate = await self.get_int(guild.id, "xp_voice_per_minute")
                xp_add = (new_seconds // 60 - old_seconds // 60) * rate
            new_xp = old_xp + xp_add
            await db.execute("UPDATE levels_users SET voice_seconds=?,total_xp=?,updated_at=? WHERE guild_id=? AND user_id=?", (new_seconds, new_xp, int(time.time()), guild.id, user_id))
            await db.commit()
        member = guild.get_member(user_id)
        if member and level_from_xp(new_xp) > level_from_xp(old_xp):
            await self.on_level_up(member, level_from_xp(old_xp), level_from_xp(new_xp))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        ids = set()
        if before.channel:
            ids.add(before.channel.id)
        if after.channel:
            ids.add(after.channel.id)
        if ids:
            await self.settle_voice_channels(member.guild, ids)

    async def refresh_all_voice(self, guild_id: Optional[int] = None):
        guilds = [self.bot.get_guild(guild_id)] if guild_id else self.bot.guilds
        for guild in guilds:
            if not guild:
                continue
            ids = {c.id for c in guild.voice_channels}
            if ids:
                await self.settle_voice_channels(guild, ids)

    async def cache_invites(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return
        self.invite_cache[guild.id] = {i.code: (i.uses or 0, i.inviter.id if i.inviter else 0) for i in invites}

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        before = self.invite_cache.get(member.guild.id, {})
        try:
            invites = await member.guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return
        used_inviter = None
        for invite in invites:
            old_uses = before.get(invite.code, (0, 0))[0]
            if (invite.uses or 0) > old_uses:
                used_inviter = invite.inviter
                break
        self.invite_cache[member.guild.id] = {i.code: (i.uses or 0, i.inviter.id if i.inviter else 0) for i in invites}
        if used_inviter and not used_inviter.bot:
            await self.award_source(member.guild, used_inviter.id, "invite", f"invite_join:{member.id}", member.id)

    async def seed_default_roles(self, guild: discord.Guild):
        defaults = [(10, "Verslaafd", 0), (25, "Slaaploos", 0), (50, "Levenloos", 1), (100, "Needs Help", 1)]
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT COUNT(*) FROM levels_roles WHERE guild_id=?", (guild.id,))
            if (await cur.fetchone())[0] == 0:
                for lvl, name, keep in defaults:
                    role = discord.utils.find(lambda r: r.name.lower() == name.lower(), guild.roles)
                    if role:
                        await db.execute("INSERT OR IGNORE INTO levels_roles (guild_id,level,role_id,keep_previous) VALUES (?,?,?,?)", (guild.id, lvl, role.id, keep))
                await db.commit()

    async def get_level_roles(self, guild_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT level,role_id,keep_previous FROM levels_roles WHERE guild_id=? ORDER BY level", (guild_id,))
            return await cur.fetchall()

    async def apply_level_roles(self, member: discord.Member) -> int:
        if not await self.get_bool(member.guild.id, "level_roles_enabled"):
            return 0
        row = await self.get_user(member.guild.id, member.id)
        level = level_from_xp(row[0])
        roles_cfg = await self.get_level_roles(member.guild.id)
        if not roles_cfg:
            return 0
        eligible = [(lvl, rid, keep) for lvl, rid, keep in roles_cfg if lvl <= level]
        wanted_ids = set()
        if eligible:
            highest = eligible[-1]
            wanted_ids.add(highest[1])
            for lvl, rid, keep in eligible:
                if keep:
                    wanted_ids.add(rid)
        all_ids = {rid for _, rid, _ in roles_cfg}
        add = [member.guild.get_role(rid) for rid in wanted_ids if member.guild.get_role(rid) and rid not in {r.id for r in member.roles}]
        remove = [r for r in member.roles if r.id in all_ids and r.id not in wanted_ids]
        changed = len(add) + len(remove)
        try:
            if add:
                await member.add_roles(*add, reason="LEVENLOOS levelrollen")
            if remove:
                await member.remove_roles(*remove, reason="LEVENLOOS levelrollen")
        except (discord.Forbidden, discord.HTTPException):
            return 0
        return changed

    def config_home_text(self) -> str:
        return (
            "⚙️ **LEVELS CONFIG**\n\n"
            "Beheer hier het volledige levelsysteem.\n\n"
            "✨ XP-functies\n🔢 XP-waardes\n💬 Tekst\n🔊 Voice\n"
            "🏅 Levelrollen\n📢 Meldingen\n🚫 Uitsluitingen\n📊 Status"
        )

    async def render_toggle_panel(self, guild_id):
        lines = ["✨ **XP-FUNCTIES**", "", "Klik op een functie om deze aan of uit te zetten.", ""]
        for src, (enabled_key, _) in XP_KEYS.items():
            lines.append(f"{'🟢' if await self.get_bool(guild_id, enabled_key) else '⚫'} {SOURCE_LABELS[src]}")
        return "\n".join(lines)

    async def render_values_panel(self, guild_id):
        lines = ["🔢 **XP-WAARDES**", ""]
        for src, (_, value_key) in XP_KEYS.items():
            value = await self.get_int(guild_id, value_key)
            suffix = "/min" if src == "voice" else ""
            lines.append(f"{SOURCE_LABELS[src]}: **{value} XP{suffix}**")
        return "\n".join(lines)

    async def render_text_panel(self, guild_id):
        return (
            "💬 **TEKST XP**\n\n"
            f"XP per bericht: **{await self.get_int(guild_id, 'xp_text')} XP**\n"
            f"Cooldown: **{await self.get_int(guild_id, 'text_cooldown')} seconden**\n\n"
            "Berichten blijven altijd meetellen voor de statistieken, ook wanneer er door de cooldown geen XP wordt gegeven."
        )

    async def render_voice_panel(self, guild_id):
        return (
            "🔊 **VOICE XP**\n\n"
            f"XP per minuut: **{await self.get_int(guild_id, 'xp_voice_per_minute')} XP**\n"
            f"Solo voice: **{'AAN' if await self.get_bool(guild_id, 'voice_solo') else 'UIT'}**\n\n"
            "Standaard telt voice alleen wanneer minimaal twee geldige gebruikers samen in voice zitten."
        )

    async def render_notifications(self, guild_id):
        cid = await self.get_int(guild_id, "levelup_channel_id")
        return f"📢 **MELDINGEN**\n\nLevel-up meldingen: **{'AAN' if await self.get_bool(guild_id, 'levelup_enabled') else 'UIT'}**\nKanaal: {f'<#{cid}>' if cid else 'niet ingesteld'}"

    async def render_exclusions(self, guild_id):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT kind,COUNT(*) FROM levels_exclusions WHERE guild_id=? GROUP BY kind", (guild_id,))
            counts = dict(await cur.fetchall())
        return f"🚫 **XP-UITSLUITINGEN**\n\nTekstkanalen: **{counts.get('text_channel',0)}**\nVoicekanalen: **{counts.get('voice_channel',0)}**\nRollen: **{counts.get('role',0)}**"

    async def render_status(self, guild_id):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT COUNT(*),COALESCE(SUM(total_xp),0),COALESCE(SUM(text_messages),0),COALESCE(SUM(voice_seconds),0) FROM levels_users WHERE guild_id=?", (guild_id,))
            users, xp, msgs, voice = await cur.fetchone()
        return (
            "📊 **LEVELS STATUS**\n\n"
            "Status: 🟢 Actief\nDatabase: `levels.db`\n\n"
            f"Spelers geregistreerd: **{users}**\nTotale XP: **{fmt_num(xp)}**\n"
            f"💬 Berichten geregistreerd: **{fmt_num(msgs)}**\n🔊 Voice geregistreerd: **{fmt_hours(voice)} uur**\n\n"
            "ActivityRank migratie: **testfase**\n"
            f"Levelrollen eigen bot: **{'AAN' if await self.get_bool(guild_id, 'level_roles_enabled') else 'UIT'}**"
        )

    async def render_user_manage(self, member: discord.Member):
        total, messages, voice = await self.get_user(member.guild.id, member.id)
        return (
            f"👤 **{member.display_name}**\n\n"
            f"<:lvl:1546517601167999127> Level **{level_from_xp(total)}**\n"
            f"🏆 Rank **#{await self.rank_for_user(member.guild.id, member.id)}**\n"
            f"✨ **{fmt_num(total)} XP**\n\n"
            f"💬 {fmt_num(messages)} berichten\n"
            f"🔊 {fmt_hours(voice)} uur voice"
        )

    async def render_history(self, guild_id: int, user_id: int):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute(
                "SELECT source,xp,actor_id,created_at FROM levels_xp_log WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 15",
                (guild_id, user_id),
            )
            rows = await cur.fetchall()
        if not rows:
            return "Er zijn nog geen bijzondere XP-transacties voor deze gebruiker."
        lines = ["📋 **XP-GESCHIEDENIS**", ""]
        for source, xp, actor_id, created_at in rows:
            sign = "+" if xp >= 0 else ""
            label = SOURCE_LABELS.get(source, source.replace("_", " ").title())
            actor = f" • door <@{actor_id}>" if actor_id and source == "manual" else ""
            lines.append(f"{sign}{fmt_num(xp)} XP • {label}{actor}")
        return "\n".join(lines)

    async def rank_for_user(self, guild_id, user_id):
        rows = await self.leaderboard_rows(guild_id, "xp")
        for index, row in enumerate(rows, start=1):
            if row[0] == user_id:
                return index
        return 0

    async def render_profile(self, member: discord.Member):
        total, messages, voice = await self.get_user(member.guild.id, member.id)
        level = level_from_xp(total)
        next_xp = xp_for_level(level + 1)
        needed = max(0, next_xp - total)
        rank = await self.rank_for_user(member.guild.id, member.id)
        return (
            f"# {LEVEL_BADGE}\n\n"
            f"> **{member.display_name}**\n"
            f"> └ <:lvl:1546517601167999127> Level **{level}** • Rank **#{rank}**\n"
            f"> └ ✨ **{fmt_num(total)} XP** • {fmt_num(needed)} XP tot level {level + 1}\n\n"
            f"> **ACTIVITEIT**\n"
            f"> └ 💬 {fmt_num(messages)} berichten\n"
            f"> └ 🔊 {fmt_hours(voice)} uur voice"
        )

    async def leaderboard_rows(self, guild_id, sort_key):
        order = {"xp": "total_xp", "messages": "text_messages", "voice": "voice_seconds"}.get(sort_key, "total_xp")
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute(f"SELECT user_id,total_xp,text_messages,voice_seconds FROM levels_users WHERE guild_id=? ORDER BY {order} DESC,total_xp DESC,user_id ASC", (guild_id,))
            rows = await cur.fetchall()
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return rows
        return [row for row in rows if guild.get_member(row[0]) is not None]

    async def render_leaderboard(self, guild_id, sort_key="xp", page=0):
        rows = await self.leaderboard_rows(guild_id, sort_key)
        pages = max(1, math.ceil(len(rows) / 10))
        page = max(0, min(page, pages - 1))
        start = page * 10
        end = min(start + 10, len(rows))

        titles = {
            "xp": "TOTALE XP",
            "messages": "BERICHTEN",
            "voice": "VOICE",
        }

        guild = self.bot.get_guild(guild_id)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"**{titles.get(sort_key, 'TOTALE XP')}**",
            f"└ Top {start + 1 if rows else 0}-{end} van **{len(rows)} spelers**",
        ]

        for idx, row in enumerate(rows[start:end], start=start + 1):
            uid, total, msgs, voice = row
            member = guild.get_member(uid) if guild else None

            # Geen mentions in het leaderboard: zo pingt /top nooit gebruikers.
            name = discord.utils.escape_markdown(member.display_name) if member else f"Onbekende gebruiker ({uid})"
            rank = medals[idx - 1] if idx <= 3 else f"**#{idx}**"
            level = level_from_xp(total)

            if sort_key == "messages":
                lines.append(
                    f"{rank} **{name}** • 💬 **{fmt_num(msgs)} berichten**\n"
                    f"└ <:lvl:1546517601167999127> Level **{level}** • "
                    f"✨ {fmt_num(total)} XP • 🔊 {fmt_hours(voice)}u"
                )
            elif sort_key == "voice":
                lines.append(
                    f"{rank} **{name}** • 🔊 **{fmt_hours(voice)} uur**\n"
                    f"└ <:lvl:1546517601167999127> Level **{level}** • "
                    f"✨ {fmt_num(total)} XP • 💬 {fmt_num(msgs)}"
                )
            else:
                lines.append(
                    f"{rank} **{name}** • <:lvl:1546517601167999127> Level **{level}**\n"
                    f"└ ✨ **{fmt_num(total)} XP** • 💬 {fmt_num(msgs)} • 🔊 {fmt_hours(voice)}u"
                )

        if not rows:
            lines.append("Er zijn nog geen levelgegevens beschikbaar.")

        embed = discord.Embed(
            description=f"# {LEADERBOARD_BADGE}\n\n> " + "\n> ".join(lines),
            color=discord.Color.from_str("#6C27DA"),
        )
        embed.set_footer(text=f"Pagina {page + 1}/{pages} • {len(rows)} spelers")
        return embed, pages

    async def page_for_user(self, guild_id, user_id, sort_key):
        rows = await self.leaderboard_rows(guild_id, sort_key)
        for i, row in enumerate(rows):
            if row[0] == user_id:
                return i // 10
        return 0

    def load_import_json(self):
        if not IMPORT_PATH.exists():
            raise FileNotFoundError(str(IMPORT_PATH))
        return json.loads(IMPORT_PATH.read_text(encoding="utf-8"))

    def match_import(self, guild: discord.Guild):
        data = self.load_import_json()
        matched, unmatched, duplicates = [], [], []
        for row in data:
            username = str(row.get("username", "")).strip().lower()
            candidates = []
            for member in guild.members:
                if member.bot:
                    continue
                names = {member.name.lower(), member.display_name.lower()}
                if getattr(member, "global_name", None):
                    names.add(member.global_name.lower())
                if username in names:
                    candidates.append(member)
            if len(candidates) == 1:
                matched.append((row, candidates[0]))
            elif not candidates:
                unmatched.append(row)
            else:
                duplicates.append((row, candidates))
        return matched, unmatched, duplicates

    async def run_import(self, guild: discord.Guild):
        async with aiosqlite.connect(DATABASE_PATH) as db:
            cur = await db.execute("SELECT 1 FROM levels_imports WHERE guild_id=? AND import_name='activityrank'", (guild.id,))
            if await cur.fetchone():
                return "⚠️ De ActivityRank-import is al uitgevoerd."
        matched, unmatched, duplicates = self.match_import(guild)
        now = int(time.time())
        async with aiosqlite.connect(DATABASE_PATH) as db:
            for row, member in matched:
                total = int(row["total_xp"])
                messages = int(row.get("messages", 0))
                voice_seconds = int(round(float(row.get("voice_hours", 0)) * 3600))
                await db.execute(
                    "INSERT INTO levels_users (guild_id,user_id,total_xp,text_messages,voice_seconds,created_at,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET total_xp=excluded.total_xp,text_messages=excluded.text_messages,voice_seconds=excluded.voice_seconds,updated_at=excluded.updated_at",
                    (guild.id, member.id, total, messages, voice_seconds, now, now),
                )
                await db.execute("INSERT OR IGNORE INTO levels_xp_log (guild_id,user_id,source,xp,reference_id,created_at) VALUES (?,?,?,?,?,?)", (guild.id, member.id, "legacy_import", total, f"activityrank:{member.id}", now))
            await db.execute("INSERT INTO levels_imports (guild_id,import_name,imported_at,rows_imported) VALUES (?,?,?,?)", (guild.id, "activityrank", now, len(matched)))
            await db.commit()
        return f"✅ **ACTIVITYRANK IMPORT VOLTOOID**\n\nGeïmporteerd: **{len(matched)}** leden\nOvergeslagen: **{len(unmatched)}**\nOnduidelijk: **{len(duplicates)}**\n\nHistorische XP is opgeslagen in `levels.db`."

    @app_commands.command(name="level", description="Bekijk je level of dat van iemand anders.")
    async def level_cmd(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if not interaction.guild:
            return
        member = member or interaction.user
        if not isinstance(member, discord.Member) or member.bot:
            await interaction.response.send_message("❌ Bots nemen niet deel aan het levelsysteem.", ephemeral=True)
            return
        await interaction.response.send_message(await self.render_profile(member))

    @app_commands.command(name="top", description="Bekijk het LEVENLOOS leaderboard.")
    async def top_cmd(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        embed, _ = await self.render_leaderboard(interaction.guild.id)
        await interaction.response.send_message(
            embed=embed,
            view=LeaderboardView(self, interaction.guild.id, interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="leaderboard", description="Bekijk het LEVENLOOS leaderboard.")
    async def leaderboard_cmd(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        embed, _ = await self.render_leaderboard(interaction.guild.id)
        await interaction.response.send_message(
            embed=embed,
            view=LeaderboardView(self, interaction.guild.id, interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @levels.command(name="config", description="Open het levels configuratiepaneel.")
    async def levels_config(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om het levelsysteem te beheren.", ephemeral=True)
            return
        await interaction.response.send_message(self.config_home_text(), view=ConfigView(self, interaction.user.id, interaction.guild.id), ephemeral=True)

    @levels.command(name="status", description="Bekijk de status van het levelsysteem.")
    async def levels_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        await interaction.response.send_message(await self.render_status(interaction.guild.id), ephemeral=True)

    @levels.command(name="import-preview", description="Controleer de ActivityRank import zonder iets te wijzigen.")
    async def import_preview(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        try:
            matched, unmatched, duplicates = self.match_import(interaction.guild)
        except FileNotFoundError:
            await interaction.response.send_message("❌ `activityrank_import.json` staat niet in de botmap.", ephemeral=True)
            return
        view = ImportPreviewView(
            self, interaction.user.id, interaction.guild.id, matched, unmatched, duplicates
        )
        await interaction.response.send_message(
            view.render(),
            view=view,
            ephemeral=True,
        )

    @levels.command(name="import", description="Importeer de ActivityRank historie naar levels.db.")
    async def import_cmd(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        await interaction.response.send_message(
            "⚠️ **ACTIVITYRANK IMPORT**\n\nDeze actie importeert historische XP, berichten en voice-tijd naar `levels.db`.\nBestaande ActivityRank XP wordt exact overgenomen en niet opnieuw berekend.",
            view=ImportConfirmView(self, interaction.user.id, interaction.guild.id), ephemeral=True
        )

    @levels.command(name="xp", description="Beheer handmatig XP van een gebruiker.")
    @app_commands.describe(member="Gebruiker", actie="add, remove of set")
    async def manual_xp(self, interaction: discord.Interaction, member: discord.Member, actie: str):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        mode = actie.lower().strip()
        if mode not in {"add", "remove", "set"}:
            await interaction.response.send_message("❌ Gebruik `add`, `remove` of `set`.", ephemeral=True)
            return
        await interaction.response.send_modal(ManualXpModal(self, interaction.guild.id, member.id, mode))

    @levels.command(name="role-set", description="Koppel een Discordrol aan een level.")
    async def role_set(self, interaction: discord.Interaction, level: app_commands.Range[int, 2, 999], role: discord.Role, keep_previous: bool = False):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("INSERT INTO levels_roles (guild_id,level,role_id,keep_previous) VALUES (?,?,?,?) ON CONFLICT(guild_id,level) DO UPDATE SET role_id=excluded.role_id,keep_previous=excluded.keep_previous", (interaction.guild.id, level, role.id, int(keep_previous)))
            await db.commit()
        await interaction.response.send_message(f"✅ {role.mention} is ingesteld voor level **{level}**.", ephemeral=True)

    @levels.command(name="role-remove", description="Verwijder een levelrol.")
    async def role_remove(self, interaction: discord.Interaction, level: int):
        if not interaction.guild or not isinstance(interaction.user, discord.Member) or not is_admin(interaction.user):
            await interaction.response.send_message("❌ Je hebt geen toestemming om dit te doen.", ephemeral=True)
            return
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("DELETE FROM levels_roles WHERE guild_id=? AND level=?", (interaction.guild.id, level))
            await db.commit()
        await interaction.response.send_message(f"✅ De levelrol voor level **{level}** is verwijderd.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Levels(bot))
