import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# CONFIG
# =========================================================

PROTECTED_MODULES = {
    "modules",
}

EXPECTED_MODULES = [
    "modules",
    "games",
    "sorting",
    "stats",
    "welcome",
    "react",
    "levels",
    "voice",
    "events",
    "suggestions",
    "changelog",
    "health",
    "botinfo",
    "actionlog",
    "birthdays",
    "reminders",
]

MODULE_LABELS = {
    "modules": "Modules",
    "games": "Games",
    "sorting": "Sorting",
    "stats": "Stats",
    "welcome": "Welcome",
    "react": "React Roles",
    "levels": "Levels",
    "voice": "Voice",
    "events": "Events",
    "suggestions": "Suggesties",
    "changelog": "Changelog",
    "health": "Health",
    "botinfo": "Bot Info",
    "actionlog": "Action Log",
    "birthdays": "Verjaardagen",
    "reminders": "Reminders",
}

MODULE_ALIASES = {
    "game": "games",
    "games": "games",
    "sorting": "sorting",
    "sort": "sorting",
    "stats": "stats",
    "stat": "stats",
    "welcome": "welcome",
    "welkom": "welcome",
    "react": "react",
    "reactroles": "react",
    "roles": "react",
    "modules": "modules",
    "module": "modules",
    "levels": "levels",
    "level": "levels",
    "xp": "levels",
    "voice": "voice",
    "voices": "voice",
    "events": "events",
    "event": "events",
    "suggestions": "suggestions",
    "suggestie": "suggestions",
    "suggesties": "suggestions",
    "changelog": "changelog",
    "health": "health",
    "botinfo": "botinfo",
    "actionlog": "actionlog",
    "log": "actionlog",
    "logs": "actionlog",
    "birthdays": "birthdays",
    "birthday": "birthdays",
    "verjaardagen": "birthdays",
    "reminders": "reminders",
    "reminder": "reminders",
}


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
# HELPERS
# =========================================================

def normalise_module_name(
    name: str
) -> str:

    cleaned = (
        name
        .strip()
        .lower()
        .replace("cogs.", "")
        .replace(".py", "")
    )

    return MODULE_ALIASES.get(
        cleaned,
        cleaned
    )


def extension_name(
    module_name: str
) -> str:

    return f"cogs.{module_name}"


def module_label(
    module_name: str
) -> str:

    return MODULE_LABELS.get(
        module_name,
        module_name.title()
    )


# =========================================================
# UI
# =========================================================

class ModuleButton(
    discord.ui.Button
):

    def __init__(
        self,
        cog: "Modules",
        module_name: str,
        row: int
    ):

        self.cog = cog
        self.module_name = module_name

        loaded = (
            extension_name(module_name)
            in
            cog.bot.extensions
        )

        if module_name in PROTECTED_MODULES:
            style = discord.ButtonStyle.secondary
            emoji = "🔒"
        elif loaded:
            style = discord.ButtonStyle.success
            emoji = "🟢"
        else:
            style = discord.ButtonStyle.secondary
            emoji = "⚫"

        super().__init__(
            label=module_label(module_name),
            style=style,
            emoji=emoji,
            row=row,
            custom_id=f"module:select:{module_name}"
        )

    async def callback(
        self,
        interaction: discord.Interaction
    ):

        view = self.view

        if not isinstance(
            view,
            ModulePanelView
        ):
            return

        if interaction.user.id != view.author_id:

            await interaction.response.send_message(
                "❌ Dit modulepaneel is niet van jou.",
                ephemeral=True
            )

            return

        loaded = (
            extension_name(self.module_name)
            in
            self.cog.bot.extensions
        )

        if self.module_name in PROTECTED_MODULES:

            text = (
                f"### 🔒 {module_label(self.module_name)}\n"
                "Deze module is beschermd en kan niet vanuit "
                "Discord worden uitgeschakeld of herladen."
            )

        elif loaded:

            text = (
                f"### 🟢 {module_label(self.module_name)}\n"
                "Deze module is momenteel **geladen**.\n\n"
                "Kies wat je ermee wilt doen."
            )

        else:

            text = (
                f"### ⚫ {module_label(self.module_name)}\n"
                "Deze module is momenteel **uitgeschakeld**.\n\n"
                "Je kunt hem hieronder weer laden."
            )

        await interaction.response.edit_message(
            content=text,
            view=ModuleActionView(
                self.cog,
                view.author_id,
                self.module_name
            )
        )


class ModulePanelView(
    discord.ui.View
):

    def __init__(
        self,
        cog: "Modules",
        author_id: int
    ):

        super().__init__(
            timeout=300
        )

        self.cog = cog
        self.author_id = author_id

        for index, module_name in enumerate(
            EXPECTED_MODULES
        ):

            self.add_item(
                ModuleButton(
                    cog,
                    module_name,
                    row=index // 5
                )
            )


class ModuleActionView(
    discord.ui.View
):

    def __init__(
        self,
        cog: "Modules",
        author_id: int,
        module_name: str
    ):

        super().__init__(
            timeout=300
        )

        self.cog = cog
        self.author_id = author_id
        self.module_name = module_name

        loaded = (
            extension_name(module_name)
            in
            cog.bot.extensions
        )

        if (
            module_name not in PROTECTED_MODULES
            and loaded
        ):

            reload_button = discord.ui.Button(
                label="Reload",
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                row=0
            )

            unload_button = discord.ui.Button(
                label="Uitschakelen",
                emoji="⏹️",
                style=discord.ButtonStyle.danger,
                row=0
            )

            reload_button.callback = self.reload_callback
            unload_button.callback = self.unload_callback

            self.add_item(
                reload_button
            )

            self.add_item(
                unload_button
            )

        elif (
            module_name not in PROTECTED_MODULES
            and not loaded
        ):

            load_button = discord.ui.Button(
                label="Laden",
                emoji="▶️",
                style=discord.ButtonStyle.success,
                row=0
            )

            load_button.callback = self.load_callback

            self.add_item(
                load_button
            )

        back_button = discord.ui.Button(
            label="Terug",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            row=1
        )

        back_button.callback = self.back_callback

        self.add_item(
            back_button
        )

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "❌ Dit modulepaneel is niet van jou.",
                ephemeral=True
            )

            return False

        return True

    async def show_panel(
        self,
        interaction: discord.Interaction,
        result: str | None = None
    ):

        content = self.cog.panel_content()

        if result:
            content = (
                f"{result}\n\n"
                f"{content}"
            )

        await interaction.edit_original_response(
            content=content,
            view=ModulePanelView(
                self.cog,
                self.author_id
            )
        )

    async def back_callback(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.edit_message(
            content=self.cog.panel_content(),
            view=ModulePanelView(
                self.cog,
                self.author_id
            )
        )

    async def load_callback(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.defer()

        success, message = await self.cog.load_module(
            self.module_name
        )

        await self.show_panel(
            interaction,
            message
        )

    async def unload_callback(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.defer()

        success, message = await self.cog.unload_module(
            self.module_name
        )

        await self.show_panel(
            interaction,
            message
        )

    async def reload_callback(
        self,
        interaction: discord.Interaction
    ):

        await interaction.response.defer()

        success, message = await self.cog.reload_module(
            self.module_name
        )

        await self.show_panel(
            interaction,
            message
        )


# =========================================================
# MODULE COG
# =========================================================

class Modules(
    commands.Cog
):

    def __init__(
        self,
        bot: commands.Bot
    ):

        self.bot = bot

    # =====================================================
    # GROUP
    # =====================================================

    module = app_commands.Group(
        name="module",
        description="Beheer de modules van LEVENLOOS."
    )

    # =====================================================
    # ACCESS
    # =====================================================

    async def check_access(
        self,
        interaction: discord.Interaction
    ) -> bool:

        if interaction.guild is None:

            await interaction.response.send_message(
                "❌ Dit command werkt alleen in een server.",
                ephemeral=True
            )

            return False

        member = interaction.user

        if not isinstance(
            member,
            discord.Member
        ):

            return False

        if not management_access(
            member
        ):

            await interaction.response.send_message(
                "❌ Je hebt geen toegang tot dit command.",
                ephemeral=True
            )

            return False

        return True

    # =====================================================
    # PANEL CONTENT
    # =====================================================

    def panel_content(
        self
    ) -> str:

        loaded_count = sum(
            1
            for name in EXPECTED_MODULES
            if extension_name(name) in self.bot.extensions
        )

        return (
            "## 🧩 LEVENLOOS MODULES\n"
            f"> **{loaded_count}/{len(EXPECTED_MODULES)}** modules geladen.\n"
            "> 🟢 = geladen • ⚫ = uitgeschakeld • 🔒 = beschermd\n\n"
            "Klik op een module om hem te beheren."
        )

    # =====================================================
    # INTERNAL ACTIONS
    # =====================================================

    async def load_module(
        self,
        module_name: str
    ) -> tuple[bool, str]:

        extension = extension_name(
            module_name
        )

        if extension in self.bot.extensions:

            return (
                False,
                f"ℹ️ `{module_name}` is al geladen."
            )

        try:

            await self.bot.load_extension(
                extension
            )

            await self.bot.tree.sync()

        except commands.ExtensionNotFound:

            return (
                False,
                f"❌ Module `{module_name}` bestaat niet."
            )

        except commands.ExtensionFailed as exc:

            return (
                False,
                f"❌ `{module_name}` kon niet worden geladen.\n"
                f"`{type(exc.original).__name__}: {exc.original}`"
            )

        except commands.ExtensionError as exc:

            return (
                False,
                f"❌ `{module_name}` kon niet worden geladen.\n"
                f"`{type(exc).__name__}: {exc}`"
            )

        return (
            True,
            f"✅ Module `{module_name}` geladen."
        )

    async def unload_module(
        self,
        module_name: str
    ) -> tuple[bool, str]:

        if module_name in PROTECTED_MODULES:

            return (
                False,
                "❌ De modulemanager kan zichzelf niet uitschakelen."
            )

        extension = extension_name(
            module_name
        )

        if extension not in self.bot.extensions:

            return (
                False,
                f"ℹ️ `{module_name}` is al uitgeschakeld."
            )

        try:

            await self.bot.unload_extension(
                extension
            )

            await self.bot.tree.sync()

        except commands.ExtensionError as exc:

            return (
                False,
                f"❌ `{module_name}` kon niet worden uitgeschakeld.\n"
                f"`{type(exc).__name__}: {exc}`"
            )

        return (
            True,
            f"✅ Module `{module_name}` uitgeschakeld."
        )

    async def reload_module(
        self,
        module_name: str
    ) -> tuple[bool, str]:

        if module_name in PROTECTED_MODULES:

            return (
                False,
                "❌ Gebruik voor `modules` zelf een normale botrestart."
            )

        extension = extension_name(
            module_name
        )

        if extension not in self.bot.extensions:

            return (
                False,
                f"❌ `{module_name}` is niet geladen."
            )

        try:

            await self.bot.reload_extension(
                extension
            )

            await self.bot.tree.sync()

        except commands.ExtensionFailed as exc:

            return (
                False,
                f"❌ `{module_name}` kon niet worden herladen.\n"
                f"`{type(exc.original).__name__}: {exc.original}`"
            )

        except commands.ExtensionError as exc:

            return (
                False,
                f"❌ `{module_name}` kon niet worden herladen.\n"
                f"`{type(exc).__name__}: {exc}`"
            )

        return (
            True,
            f"✅ Module `{module_name}` herladen."
        )

    # =====================================================
    # PANEL
    # =====================================================

    @module.command(
        name="panel",
        description="Open het interactieve modulepaneel."
    )
    async def module_panel(
        self,
        interaction: discord.Interaction
    ):

        if not await self.check_access(
            interaction
        ):
            return

        await interaction.response.send_message(
            self.panel_content(),
            view=ModulePanelView(
                self,
                interaction.user.id
            ),
            ephemeral=True
        )

    # =====================================================
    # LIST
    # =====================================================

    @module.command(
        name="list",
        description="Bekijk welke botmodules geladen zijn."
    )
    async def module_list(
        self,
        interaction: discord.Interaction
    ):

        if not await self.check_access(
            interaction
        ):
            return

        lines = []

        for name in EXPECTED_MODULES:

            loaded = (
                extension_name(name)
                in
                self.bot.extensions
            )

            icon = "🟢" if loaded else "🔴"

            protection = (
                " 🔒"
                if name in PROTECTED_MODULES
                else ""
            )

            lines.append(
                f"{icon} `{name}`{protection}"
            )

        await interaction.response.send_message(
            "**🧩 LEVENLOOS MODULES**\n\n"
            + "\n".join(lines)
            + "\n\n🔒 = kan niet worden uitgeschakeld.",
            ephemeral=True
        )

    # =====================================================
    # LOAD
    # =====================================================

    @module.command(
        name="load",
        description="Laad een uitgeschakelde botmodule."
    )
    @app_commands.describe(
        naam="Bijvoorbeeld: stats, welcome of react"
    )
    async def module_load(
        self,
        interaction: discord.Interaction,
        naam: str
    ):

        if not await self.check_access(
            interaction
        ):
            return

        module_name = normalise_module_name(
            naam
        )

        await interaction.response.defer(
            ephemeral=True
        )

        success, message = await self.load_module(
            module_name
        )

        await interaction.followup.send(
            message,
            ephemeral=True
        )

    # =====================================================
    # UNLOAD
    # =====================================================

    @module.command(
        name="unload",
        description="Schakel een geladen botmodule uit."
    )
    @app_commands.describe(
        naam="Bijvoorbeeld: stats, welcome of react"
    )
    async def module_unload(
        self,
        interaction: discord.Interaction,
        naam: str
    ):

        if not await self.check_access(
            interaction
        ):
            return

        module_name = normalise_module_name(
            naam
        )

        await interaction.response.defer(
            ephemeral=True
        )

        success, message = await self.unload_module(
            module_name
        )

        await interaction.followup.send(
            message,
            ephemeral=True
        )

    # =====================================================
    # RELOAD
    # =====================================================

    @module.command(
        name="reload",
        description="Herlaad een botmodule zonder botrestart."
    )
    @app_commands.describe(
        naam="Bijvoorbeeld: stats, welcome of react"
    )
    async def module_reload(
        self,
        interaction: discord.Interaction,
        naam: str
    ):

        if not await self.check_access(
            interaction
        ):
            return

        module_name = normalise_module_name(
            naam
        )

        await interaction.response.defer(
            ephemeral=True
        )

        success, message = await self.reload_module(
            module_name
        )

        await interaction.followup.send(
            message,
            ephemeral=True
        )


# =========================================================
# EXTENSION SETUP
# =========================================================

async def setup(
    bot: commands.Bot
):

    await bot.add_cog(
        Modules(bot)
    )
