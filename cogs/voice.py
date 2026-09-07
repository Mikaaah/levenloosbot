from discord.ext import commands


class Voice(
    commands.Cog
):
    """
    Placeholder voor: Tijdelijke voice channels

    Deze module is bewust nog leeg.
    Functionaliteit kan later worden toegevoegd zonder de
    hoofdstructuur van de bot opnieuw aan te passen.
    """

    def __init__(
        self,
        bot: commands.Bot
    ):
        self.bot = bot


async def setup(
    bot: commands.Bot
):
    await bot.add_cog(
        Voice(bot)
    )
