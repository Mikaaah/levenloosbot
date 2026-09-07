import asyncio
from collections import OrderedDict
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# CONFIG
# =========================================================

ACTION_LOG_CHANNEL_ID = 1546623495952269342
PURPLE = discord.Color.from_rgb(108, 39, 218)

AUDIT_DELAY = 1.0
AUDIT_WINDOW_SECONDS = 12
MESSAGE_CACHE_LIMIT = 5000

# Categorieën waarvan kanaalwijzigingen niet gelogd worden.
IGNORED_CHANNEL_CATEGORY_IDS = {
    1546178592575062196,  # Server Stats
}


# =========================================================
# HELPERS
# =========================================================

def shorten(text: str | None, limit: int = 900, empty: str = "*Geen inhoud*") -> str:
    if text is None:
        return empty

    text = str(text).strip()

    if not text:
        return empty

    if len(text) <= limit:
        return text

    return text[: limit - 1] + "…"


def display_user(user) -> str:
    if user is None:
        return "`Onbekend`"

    mention = getattr(user, "mention", None)

    if mention:
        return mention

    return f"**{user}**"


def display_channel(channel) -> str:
    if channel is None:
        return "`Onbekend`"

    mention = getattr(channel, "mention", None)

    if mention:
        return mention

    return f"**{getattr(channel, 'name', str(channel))}**"


def display_role(role) -> str:
    if role is None:
        return "`Onbekend`"

    mention = getattr(role, "mention", None)

    if mention:
        return mention

    return f"**{getattr(role, 'name', str(role))}**"


def bool_text(value: bool) -> str:
    return "Ja" if value else "Nee"


def colour_text(colour: discord.Colour) -> str:
    return f"#{colour.value:06X}"


def overwrite_summary(overwrite: discord.PermissionOverwrite) -> str:
    allowed, denied = overwrite.pair()

    allowed_names = [
        name.replace("_", " ").title()
        for name, value in allowed
        if value
    ]

    denied_names = [
        name.replace("_", " ").title()
        for name, value in denied
        if value
    ]

    parts = []

    if allowed_names:
        parts.append("✅ " + ", ".join(allowed_names[:10]))

    if denied_names:
        parts.append("❌ " + ", ".join(denied_names[:10]))

    return " • ".join(parts) if parts else "Geen expliciete permissions"


# =========================================================
# COG
# =========================================================

class Actionlog(commands.Cog):

    actionlog_group = app_commands.Group(
        name="actionlog",
        description="Beheer de LEVENLOOS action log.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Eigen kleine berichtcache. Daardoor kunnen we verwijderde/gewijzigde
        # inhoud loggen, ook wanneer discord.py het bericht niet meer cached heeft.
        self.message_cache: OrderedDict[int, dict] = OrderedDict()

    # =====================================================
    # MESSAGE CACHE
    # =====================================================

    def cache_message(self, message: discord.Message):
        if message.guild is None:
            return

        if message.channel.id == ACTION_LOG_CHANNEL_ID:
            return

        data = {
            "guild_id": message.guild.id,
            "channel_id": message.channel.id,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "author_display_name": getattr(message.author, "display_name", str(message.author)),
            "author_avatar": message.author.display_avatar.url,
            "content": message.content or "",
            "attachments": [a.url for a in message.attachments[:5]],
        }

        self.message_cache[message.id] = data
        self.message_cache.move_to_end(message.id)

        while len(self.message_cache) > MESSAGE_CACHE_LIMIT:
            self.message_cache.popitem(last=False)

    def pop_cached_message(self, message_id: int):
        return self.message_cache.pop(message_id, None)

    def get_cached_message(self, message_id: int):
        return self.message_cache.get(message_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        self.cache_message(message)

    # =====================================================
    # OUTPUT
    # =====================================================

    async def send_log(
        self,
        guild: discord.Guild,
        title: str,
        *,
        description: str | None = None,
        colour: discord.Colour = PURPLE,
        thumbnail: str | None = None,
    ):
        channel = guild.get_channel(ACTION_LOG_CHANNEL_ID)

        if not isinstance(channel, discord.TextChannel):
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=colour,
            timestamp=datetime.now(timezone.utc),
        )

        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        embed.set_footer(text="LEVENLOOS • Action Log")

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as error:
            print(f"[ACTIONLOG SEND] {error}")

    # =====================================================
    # AUDIT LOG
    # =====================================================

    async def find_audit_entry(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        *,
        target_id: int | None = None,
        extra_check=None,
    ):
        # Audit-log entries kunnen iets later binnenkomen dan het event zelf.
        # Daarom een paar keer opnieuw proberen.
        for delay in (0.8, 1.2, 1.8):
            await asyncio.sleep(delay)

            now = datetime.now(timezone.utc)

            try:
                async for entry in guild.audit_logs(limit=12, action=action):
                    age = abs((now - entry.created_at).total_seconds())

                    if age > AUDIT_WINDOW_SECONDS:
                        continue

                    if target_id is not None:
                        if getattr(entry.target, "id", None) != target_id:
                            continue

                    if extra_check is not None:
                        try:
                            if not extra_check(entry):
                                continue
                        except Exception:
                            continue

                    return entry

            except (discord.Forbidden, discord.HTTPException):
                return None

        return None

    def executor_text(self, entry) -> str:
        if entry is None or entry.user is None:
            return "`Onbekend`"

        return display_user(entry.user)

    def message_delete_executor_text(
        self,
        guild: discord.Guild,
        entry,
        author,
    ) -> str:
        if entry is not None and entry.user is not None:
            return display_user(entry.user)

        me = guild.me

        # Als de bot Audit Log mag bekijken en er na meerdere pogingen
        # géén delete-entry bestaat, is het vrijwel zeker een self-delete.
        if (
            author is not None
            and me is not None
            and me.guild_permissions.view_audit_log
        ):
            return f"{display_user(author)} *(zelf verwijderd)*"

        return "`Onbekend`"

    # =====================================================
    # MESSAGE DELETE
    # =====================================================

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return

        if payload.channel_id == ACTION_LOG_CHANNEL_ID:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel is None:
            return

        cached = self.pop_cached_message(payload.message_id)
        discord_cached = payload.cached_message

        if discord_cached is not None:
            author = discord_cached.author
            author_id = author.id
            author_text = display_user(author)
            avatar = author.display_avatar.url
            content = discord_cached.content or ""
            attachments = [a.url for a in discord_cached.attachments[:5]]
        elif cached is not None:
            author = guild.get_member(cached["author_id"])
            author_id = cached["author_id"]
            author_text = (
                display_user(author)
                if author is not None
                else f"**{cached['author_display_name']}**"
            )
            avatar = cached["author_avatar"]
            content = cached["content"]
            attachments = cached["attachments"]
        else:
            # Berichten die al bestonden vóór de botstart kunnen niet na verwijdering
            # alsnog bij Discord worden opgehaald.
            author = None
            author_id = None
            author_text = "`Onbekend`"
            avatar = None
            content = ""
            attachments = []

        entry = None

        if author_id is not None:
            entry = await self.find_audit_entry(
                guild,
                discord.AuditLogAction.message_delete,
                target_id=author_id,
                extra_check=lambda item: (
                    getattr(item.extra, "channel", None) is not None
                    and item.extra.channel.id == payload.channel_id
                ),
            )

        delete_executor = self.message_delete_executor_text(
            guild,
            entry,
            author,
        )

        description = (
            f"{author_text} • {display_channel(channel)}\n"
            f"**Door:** {delete_executor}\n\n"
            f"**Bericht:**\n"
        )

        if content:
            description += shorten(content, 1000)
        else:
            description += "*Inhoud niet beschikbaar*"

        if attachments:
            description += "\n\n**Bijlagen:**\n" + "\n".join(attachments)

        await self.send_log(
            guild,
            "🗑️ BERICHT VERWIJDERD",
            description=description,
            colour=discord.Color.red(),
            thumbnail=avatar,
        )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
        self,
        payload: discord.RawBulkMessageDeleteEvent,
    ):
        if payload.guild_id is None:
            return

        if payload.channel_id == ACTION_LOG_CHANNEL_ID:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel is None:
            return

        previews = []

        for message_id in list(payload.message_ids)[:10]:
            cached = self.pop_cached_message(message_id)

            if cached is None:
                continue

            previews.append(
                f"**{cached['author_display_name']}:** "
                f"{shorten(cached['content'], 120, '*geen tekst*')}"
            )

        entry = await self.find_audit_entry(
            guild,
            discord.AuditLogAction.message_bulk_delete,
        )

        description = (
            f"{display_channel(channel)} • **{len(payload.message_ids)} berichten**\n"
            f"**Door:** {self.executor_text(entry)}"
        )

        if previews:
            description += "\n\n" + "\n".join(previews)

        await self.send_log(
            guild,
            "🧹 BERICHTEN GEPURGED",
            description=description,
            colour=discord.Color.red(),
        )

    # =====================================================
    # MESSAGE EDIT
    # =====================================================

    @commands.Cog.listener()
    async def on_raw_message_edit(
        self,
        payload: discord.RawMessageUpdateEvent,
    ):
        if payload.guild_id is None:
            return

        if payload.channel_id == ACTION_LOG_CHANNEL_ID:
            return

        # Discord stuurt ook update-events voor embeds/previews.
        if "content" not in payload.data:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        channel = guild.get_channel_or_thread(payload.channel_id)
        if channel is None:
            return

        cached = self.get_cached_message(payload.message_id)
        discord_cached = payload.cached_message

        before_content = None
        author = None
        avatar = None

        if discord_cached is not None:
            before_content = discord_cached.content
            author = discord_cached.author
            avatar = author.display_avatar.url
        elif cached is not None:
            before_content = cached["content"]
            author = guild.get_member(cached["author_id"])
            avatar = cached["author_avatar"]

        after_content = payload.data.get("content", "")

        if before_content == after_content:
            return

        after_message = None

        try:
            after_message = await channel.fetch_message(payload.message_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
            AttributeError,
        ):
            pass

        if after_message is not None:
            author = after_message.author
            avatar = author.display_avatar.url
            self.cache_message(after_message)

        if author is not None:
            author_text = display_user(author)
        elif cached is not None:
            author_text = (
                f"**{cached['author_display_name']}**"
            )
        else:
            author_text = "`Onbekend`"

        jump_url = (
            after_message.jump_url
            if after_message is not None
            else f"https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id}"
        )

        old_text = (
            shorten(before_content, 700)
            if before_content is not None
            else "*Oude inhoud niet beschikbaar*"
        )

        new_text = shorten(after_content, 700)

        await self.send_log(
            guild,
            "✏️ BERICHT AANGEPAST",
            description=(
                f"{author_text} • {display_channel(channel)} • "
                f"[Bericht]({jump_url})\n\n"
                f"**Voor:** {old_text}\n"
                f"**Na:** {new_text}"
            ),
            colour=discord.Color.orange(),
            thumbnail=avatar,
        )

    # =====================================================
    # BANS
    # =====================================================

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
    ):
        entry = await self.find_audit_entry(
            guild,
            discord.AuditLogAction.ban,
            target_id=user.id,
        )

        reason = (
            entry.reason
            if entry is not None and entry.reason
            else "Geen reden opgegeven"
        )

        await self.send_log(
            guild,
            "🔨 GEBRUIKER GEBANNED",
            description=(
                f"{display_user(user)}\n"
                f"**Door:** {self.executor_text(entry)}\n"
                f"**Reden:** {shorten(reason, 700)}"
            ),
            colour=discord.Color.red(),
            thumbnail=user.display_avatar.url,
        )

    @commands.Cog.listener()
    async def on_member_unban(
        self,
        guild: discord.Guild,
        user: discord.User,
    ):
        entry = await self.find_audit_entry(
            guild,
            discord.AuditLogAction.unban,
            target_id=user.id,
        )

        await self.send_log(
            guild,
            "🔓 BAN OPGEHEVEN",
            description=(
                f"{display_user(user)}\n"
                f"**Door:** {self.executor_text(entry)}"
            ),
            colour=discord.Color.green(),
            thumbnail=user.display_avatar.url,
        )

    # =====================================================
    # MEMBER UPDATES
    # Geen join/leave of voice logging.
    # =====================================================

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ):
        if before.nick != after.nick:
            entry = await self.find_audit_entry(
                after.guild,
                discord.AuditLogAction.member_update,
                target_id=after.id,
            )

            await self.send_log(
                after.guild,
                "🏷️ NICKNAME GEWIJZIGD",
                description=(
                    f"{display_user(after)}\n"
                    f"`{before.nick or before.name}` → `{after.nick or after.name}`\n"
                    f"**Door:** {self.executor_text(entry)}"
                ),
                colour=discord.Color.orange(),
                thumbnail=after.display_avatar.url,
            )

        before_roles = {
            role.id: role
            for role in before.roles
            if not role.is_default()
        }

        after_roles = {
            role.id: role
            for role in after.roles
            if not role.is_default()
        }

        added_ids = set(after_roles) - set(before_roles)
        removed_ids = set(before_roles) - set(after_roles)

        if added_ids or removed_ids:
            entry = await self.find_audit_entry(
                after.guild,
                discord.AuditLogAction.member_role_update,
                target_id=after.id,
            )

            lines = [
                display_user(after),
                f"**Door:** {self.executor_text(entry)}",
            ]

            if added_ids:
                lines.append(
                    "**+ Toegevoegd:** "
                    + ", ".join(
                        display_role(after_roles[role_id])
                        for role_id in added_ids
                    )
                )

            if removed_ids:
                lines.append(
                    "**− Verwijderd:** "
                    + ", ".join(
                        display_role(before_roles[role_id])
                        for role_id in removed_ids
                    )
                )

            await self.send_log(
                after.guild,
                "🎭 ROLLEN GEWIJZIGD",
                description="\n".join(lines),
                colour=discord.Color.orange(),
                thumbnail=after.display_avatar.url,
            )

        if before.timed_out_until != after.timed_out_until:
            entry = await self.find_audit_entry(
                after.guild,
                discord.AuditLogAction.member_update,
                target_id=after.id,
            )

            if after.timed_out_until is None:
                title = "✅ TIMEOUT VERWIJDERD"
                timeout_text = "Verwijderd"
                colour = discord.Color.green()
            else:
                title = "⏱️ TIMEOUT GEWIJZIGD"
                timeout_text = discord.utils.format_dt(
                    after.timed_out_until,
                    style="F",
                )
                colour = discord.Color.orange()

            await self.send_log(
                after.guild,
                title,
                description=(
                    f"{display_user(after)} • **{timeout_text}**\n"
                    f"**Door:** {self.executor_text(entry)}"
                ),
                colour=colour,
                thumbnail=after.display_avatar.url,
            )

    # =====================================================
    # CHANNELS / CATEGORIES
    # Positie/re-order wordt bewust NIET gelogd.
    # Server Stats categorie wordt volledig genegeerd.
    # =====================================================

    def ignore_channel_log(
        self,
        channel: discord.abc.GuildChannel,
    ) -> bool:
        if channel.id == ACTION_LOG_CHANNEL_ID:
            return True

        if channel.id in IGNORED_CHANNEL_CATEGORY_IDS:
            return True

        if channel.category_id in IGNORED_CHANNEL_CATEGORY_IDS:
            return True

        return False


    @commands.Cog.listener()
    async def on_guild_channel_create(
        self,
        channel: discord.abc.GuildChannel,
    ):
        if self.ignore_channel_log(channel):
            return

        entry = await self.find_audit_entry(
            channel.guild,
            discord.AuditLogAction.channel_create,
            target_id=channel.id,
        )

        extra = (
            f"\n**Categorie:** {display_channel(channel.category)}"
            if channel.category is not None
            else ""
        )

        await self.send_log(
            channel.guild,
            "➕ KANAAL AANGEMAAKT",
            description=(
                f"{display_channel(channel)} • `{channel.type}`\n"
                f"**Door:** {self.executor_text(entry)}"
                f"{extra}"
            ),
            colour=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(
        self,
        channel: discord.abc.GuildChannel,
    ):
        if self.ignore_channel_log(channel):
            return

        entry = await self.find_audit_entry(
            channel.guild,
            discord.AuditLogAction.channel_delete,
            target_id=channel.id,
        )

        extra = (
            f"\n**Categorie:** {display_channel(channel.category)}"
            if channel.category is not None
            else ""
        )

        await self.send_log(
            channel.guild,
            "➖ KANAAL VERWIJDERD",
            description=(
                f"**{channel.name}** • `{channel.type}`\n"
                f"**Door:** {self.executor_text(entry)}"
                f"{extra}"
            ),
            colour=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ):
        if (
            self.ignore_channel_log(before)
            or self.ignore_channel_log(after)
        ):
            return

        changes = []

        if before.name != after.name:
            changes.append(
                f"**Naam:** `{before.name}` → `{after.name}`"
            )

        if before.category_id != after.category_id:
            changes.append(
                f"**Categorie:** {display_channel(before.category)} → "
                f"{display_channel(after.category)}"
            )

        if isinstance(before, discord.TextChannel) and isinstance(after, discord.TextChannel):
            old_topic = (before.topic or "").strip()
            new_topic = (after.topic or "").strip()

            # Leeg -> leeg nooit loggen.
            if old_topic != new_topic and (old_topic or new_topic):
                changes.append(
                    f"**Topic:** `{shorten(old_topic, 220, 'Geen topic')}` → "
                    f"`{shorten(new_topic, 220, 'Geen topic')}`"
                )

            if before.slowmode_delay != after.slowmode_delay:
                changes.append(
                    f"**Slowmode:** `{before.slowmode_delay}s` → "
                    f"`{after.slowmode_delay}s`"
                )

            if before.nsfw != after.nsfw:
                changes.append(
                    f"**NSFW:** `{bool_text(before.nsfw)}` → "
                    f"`{bool_text(after.nsfw)}`"
                )

        if isinstance(before, discord.VoiceChannel) and isinstance(after, discord.VoiceChannel):
            if before.bitrate != after.bitrate:
                changes.append(
                    f"**Bitrate:** `{before.bitrate}` → `{after.bitrate}`"
                )

            if before.user_limit != after.user_limit:
                changes.append(
                    f"**User limit:** `{before.user_limit}` → `{after.user_limit}`"
                )

        before_overwrites = before.overwrites
        after_overwrites = after.overwrites

        overwrite_targets = set(before_overwrites) | set(after_overwrites)
        overwrite_changes = []

        for target in overwrite_targets:
            old = before_overwrites.get(
                target,
                discord.PermissionOverwrite(),
            )

            new = after_overwrites.get(
                target,
                discord.PermissionOverwrite(),
            )

            if old == new:
                continue

            target_name = (
                target.mention
                if hasattr(target, "mention")
                else str(target)
            )

            overwrite_changes.append(
                f"**Permissions {target_name}:** "
                f"{overwrite_summary(old)} → {overwrite_summary(new)}"
            )

        if overwrite_changes:
            changes.extend(overwrite_changes[:5])

        # Belangrijk: position wordt NIET bekeken.
        if not changes:
            return

        entry = await self.find_audit_entry(
            after.guild,
            discord.AuditLogAction.channel_update,
            target_id=after.id,
        )

        await self.send_log(
            after.guild,
            "✏️ KANAAL GEWIJZIGD",
            description=(
                f"{display_channel(after)}\n"
                + "\n".join(changes)
                + f"\n**Door:** {self.executor_text(entry)}"
            ),
            colour=discord.Color.orange(),
        )

    # =====================================================
    # ROLES
    # Positie/re-order wordt bewust NIET gelogd.
    # =====================================================

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        entry = await self.find_audit_entry(
            role.guild,
            discord.AuditLogAction.role_create,
            target_id=role.id,
        )

        await self.send_log(
            role.guild,
            "➕ ROL AANGEMAAKT",
            description=(
                f"{display_role(role)} • `{colour_text(role.colour)}`\n"
                f"**Door:** {self.executor_text(entry)}"
            ),
            colour=discord.Color.green(),
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        entry = await self.find_audit_entry(
            role.guild,
            discord.AuditLogAction.role_delete,
            target_id=role.id,
        )

        await self.send_log(
            role.guild,
            "➖ ROL VERWIJDERD",
            description=(
                f"**{role.name}**\n"
                f"**Door:** {self.executor_text(entry)}"
            ),
            colour=discord.Color.red(),
        )

    @commands.Cog.listener()
    async def on_guild_role_update(
        self,
        before: discord.Role,
        after: discord.Role,
    ):
        changes = []

        if before.name != after.name:
            changes.append(
                f"**Naam:** `{before.name}` → `{after.name}`"
            )

        if before.colour != after.colour:
            changes.append(
                f"**Kleur:** `{colour_text(before.colour)}` → "
                f"`{colour_text(after.colour)}`"
            )

        if before.hoist != after.hoist:
            changes.append(
                f"**Apart tonen:** `{bool_text(before.hoist)}` → "
                f"`{bool_text(after.hoist)}`"
            )

        if before.mentionable != after.mentionable:
            changes.append(
                f"**Mentionable:** `{bool_text(before.mentionable)}` → "
                f"`{bool_text(after.mentionable)}`"
            )

        if before.permissions != after.permissions:
            before_allowed = {
                name
                for name, value in before.permissions
                if value
            }

            after_allowed = {
                name
                for name, value in after.permissions
                if value
            }

            added = after_allowed - before_allowed
            removed = before_allowed - after_allowed

            if added:
                changes.append(
                    "**Permissions +:** "
                    + ", ".join(
                        sorted(
                            name.replace("_", " ").title()
                            for name in added
                        )[:15]
                    )
                )

            if removed:
                changes.append(
                    "**Permissions −:** "
                    + ", ".join(
                        sorted(
                            name.replace("_", " ").title()
                            for name in removed
                        )[:15]
                    )
                )

        # position wordt bewust genegeerd
        if not changes:
            return

        entry = await self.find_audit_entry(
            after.guild,
            discord.AuditLogAction.role_update,
            target_id=after.id,
        )

        await self.send_log(
            after.guild,
            "✏️ ROL GEWIJZIGD",
            description=(
                f"{display_role(after)}\n"
                + "\n".join(changes)
                + f"\n**Door:** {self.executor_text(entry)}"
            ),
            colour=discord.Color.orange(),
        )

    # =====================================================
    # SERVER SETTINGS
    # =====================================================

    @commands.Cog.listener()
    async def on_guild_update(
        self,
        before: discord.Guild,
        after: discord.Guild,
    ):
        changes = []

        if before.name != after.name:
            changes.append(
                f"**Naam:** `{before.name}` → `{after.name}`"
            )

        if before.verification_level != after.verification_level:
            changes.append(
                f"**Verificatie:** `{before.verification_level}` → "
                f"`{after.verification_level}`"
            )

        if before.default_notifications != after.default_notifications:
            changes.append(
                f"**Notificaties:** `{before.default_notifications}` → "
                f"`{after.default_notifications}`"
            )

        if before.explicit_content_filter != after.explicit_content_filter:
            changes.append(
                f"**Contentfilter:** `{before.explicit_content_filter}` → "
                f"`{after.explicit_content_filter}`"
            )

        if before.afk_channel != after.afk_channel:
            changes.append(
                f"**AFK-kanaal:** {display_channel(before.afk_channel)} → "
                f"{display_channel(after.afk_channel)}"
            )

        if before.afk_timeout != after.afk_timeout:
            changes.append(
                f"**AFK-timeout:** `{before.afk_timeout}s` → "
                f"`{after.afk_timeout}s`"
            )

        if not changes:
            return

        entry = await self.find_audit_entry(
            after,
            discord.AuditLogAction.guild_update,
            target_id=after.id,
        )

        await self.send_log(
            after,
            "⚙️ SERVERINSTELLINGEN GEWIJZIGD",
            description=(
                "\n".join(changes)
                + f"\n**Door:** {self.executor_text(entry)}"
            ),
            colour=discord.Color.orange(),
        )

    # =====================================================
    # TEST COMMAND
    # =====================================================

    @actionlog_group.command(
        name="test",
        description="Stuur een testbericht naar de Action Log.",
    )
    async def test_command(
        self,
        interaction: discord.Interaction,
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ Dit werkt alleen in een server.",
                ephemeral=True,
            )
            return

        member = interaction.user

        if not isinstance(member, discord.Member):
            return

        allowed = (
            member.id == interaction.guild.owner_id
            or member.guild_permissions.administrator
            or any(
                role.name.lower() in {"admin", "owner"}
                for role in member.roles
            )
        )

        if not allowed:
            await interaction.response.send_message(
                "❌ Je hebt geen toegang tot dit command.",
                ephemeral=True,
            )
            return

        channel = interaction.guild.get_channel(ACTION_LOG_CHANNEL_ID)

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Het Action Log-kanaal kon niet worden gevonden.",
                ephemeral=True,
            )
            return

        await self.send_log(
            interaction.guild,
            "🧪 ACTION LOG TEST",
            description=f"{display_user(member)} • Alles werkt.",
            colour=PURPLE,
            thumbnail=member.display_avatar.url,
        )

        await interaction.response.send_message(
            f"✅ Test verstuurd naar {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Actionlog(bot))
