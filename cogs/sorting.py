import asyncio

import discord
from discord import app_commands
from discord.ext import commands


# =========================================================
# CONFIG
# =========================================================

ACTIVE_GAME_CATEGORY_ID = 940987098427707412
GAME_PROPOSAL_CHANNEL_ID = 940336215331319858

ARCHIVE_CATEGORY_ID = 1545088593716973568
UNARCHIVE_CHANNEL_ID = 1545089501745913916


# =========================================================
# PERMISSIONS
# =========================================================

def management_access(member: discord.Member) -> bool:

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
        or "owner" in role_names
    )


# =========================================================
# SORT HELPERS
# =========================================================

def game_sort_key(
    channel: discord.TextChannel
) -> str:

    name = channel.name

    name = name.lstrip(
        "▏│┃|◆ "
    )

    return name.lower().strip()


async def repair_category_sort(
    category: discord.CategoryChannel,
    pinned_channel_id: int
) -> int:

    pinned = category.guild.get_channel(
        pinned_channel_id
    )

    if not isinstance(
        pinned,
        discord.TextChannel
    ):
        raise RuntimeError(
            f"Pinned kanaal {pinned_channel_id} niet gevonden."
        )

    if pinned.category_id != category.id:
        raise RuntimeError(
            f"{pinned.name} staat niet in {category.name}."
        )

    games = [
        channel
        for channel in category.text_channels
        if channel.id != pinned_channel_id
    ]

    games.sort(
        key=game_sort_key
    )

    # -----------------------------------------------------
    # PINNED CHANNEL BOVENAAN
    # -----------------------------------------------------

    await pinned.move(
        beginning=True,
        category=category,
        reason="Game categorie opnieuw sorteren"
    )

    await asyncio.sleep(1.0)

    # -----------------------------------------------------
    # ALLE GAMES A-Z DIRECT ACHTER ELKAAR
    # -----------------------------------------------------

    previous = pinned

    moved = 0

    for channel in games:

        # Cache-volgorde opnieuw controleren
        current_channels = category.text_channels

        try:
            previous_index = current_channels.index(
                previous
            )

            channel_index = current_channels.index(
                channel
            )

        except ValueError:
            continue

        # Staat hij al precies achter de vorige?
        if channel_index == previous_index + 1:
            previous = channel
            continue

        print(
            f"[SORTING] Verplaats {channel.name} "
            f"na {previous.name}"
        )

        await channel.move(
            after=previous,
            category=category,
            reason="Game categorie alfabetisch sorteren"
        )

        moved += 1

        # Niet Discord bombarderen
        await asyncio.sleep(1.25)

        previous = channel

    return moved


# =========================================================
# COG
# =========================================================

class Sorting(commands.Cog):

    def __init__(
        self,
        bot: commands.Bot
    ):

        self.bot = bot

        self.sort_lock = asyncio.Lock()

    sorting = app_commands.Group(
        name="sorting",
        description="Sorteer serverkanalen"
    )

    @sorting.command(
        name="games",
        description="Sorteer alle gamekanalen opnieuw"
    )
    async def sorting_games(
        self,
        interaction: discord.Interaction
    ):

        if interaction.guild is None:
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

        if self.sort_lock.locked():

            await interaction.response.send_message(
                "⏳ Er draait al een sorteeractie.",
                ephemeral=True
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        async with self.sort_lock:

            guild = interaction.guild

            active_category = guild.get_channel(
                ACTIVE_GAME_CATEGORY_ID
            )

            archive_category = guild.get_channel(
                ARCHIVE_CATEGORY_ID
            )

            if not isinstance(
                active_category,
                discord.CategoryChannel
            ):

                await interaction.followup.send(
                    "❌ Actieve gamecategorie niet gevonden.",
                    ephemeral=True
                )

                return

            if not isinstance(
                archive_category,
                discord.CategoryChannel
            ):

                await interaction.followup.send(
                    "❌ Archiefcategorie niet gevonden.",
                    ephemeral=True
                )

                return

            try:

                print(
                    "[SORTING] Actieve games sorteren..."
                )

                active_moved = await repair_category_sort(
                    active_category,
                    GAME_PROPOSAL_CHANNEL_ID
                )

                await asyncio.sleep(
                    2
                )

                print(
                    "[SORTING] Archief sorteren..."
                )

                archive_moved = await repair_category_sort(
                    archive_category,
                    UNARCHIVE_CHANNEL_ID
                )

                print(
                    "[SORTING] Klaar"
                )

            except discord.Forbidden:

                await interaction.followup.send(
                    "❌ Ik heb geen toestemming om "
                    "kanalen te verplaatsen.",
                    ephemeral=True
                )

                return

            except Exception as exc:

                print(
                    f"[SORTING ERROR] "
                    f"{type(exc).__name__}: {exc}"
                )

                await interaction.followup.send(
                    "❌ Sorteren ging fout:\n"
                    f"`{type(exc).__name__}: {exc}`",
                    ephemeral=True
                )

                return

        await interaction.followup.send(
            "✅ **Gamecategorieën gesorteerd.**\n\n"
            f"🎮 Actief verplaatst: **{active_moved}**\n"
            f"📦 Archief verplaatst: **{archive_moved}**",
            ephemeral=True
        )


async def setup(
    bot: commands.Bot
):

    await bot.add_cog(
        Sorting(bot)
    )