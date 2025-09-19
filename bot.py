from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


@dataclass
class BotConfig:
    token: str
    staff_role_id: int
    ticket_category_name: str
    transcript_channel_id: Optional[int]
    locale: str = "lt"

    @classmethod
    def load(cls, path: Path) -> "BotConfig":
        with path.open(encoding="utf-8") as fp:
            data: dict[str, Any] = json.load(fp)
        return cls(
            token=data["token"],
            staff_role_id=int(data.get("staff_role_id", 0)),
            ticket_category_name=data.get("ticket_category_name", "Bilietai"),
            transcript_channel_id=(
                int(data["transcript_channel_id"])
                if data.get("transcript_channel_id")
                else None
            ),
            locale=data.get("locale", "lt"),
        )


@dataclass(frozen=True)
class TicketCategory:
    identifier: str
    label: str
    description: str


TICKET_CATEGORIES: tuple[TicketCategory, ...] = (
    TicketCategory(
        identifier="pagalba",
        label="Pagalba",
        description="Bendros pagalbos prašymai dėl žaidimo mechanikų ar serverio sistemų naudojimo.",
    ),
    TicketCategory(
        identifier="klausimas",
        label="Klausimas",
        description="Klausimai, susiję su žaidimu, serverio taisyklėmis ar galimybėmis.",
    ),
    TicketCategory(
        identifier="klaida",
        label="Klaida",
        description="Pranešimai apie technines klaidas, neveikiančias komandas ar kitus sutrikimus.",
    ),
    TicketCategory(
        identifier="parama",
        label="Parama",
        description="Užklausos dėl mokėjimų, prenumeratų ar kitų finansinių klausimų.",
    ),
    TicketCategory(
        identifier="report",
        label="Report situacija",
        description="Pranešimai apie žaidėjų pažeidimus, netinkamą elgesį ar taisyklių laužymą.",
    ),
)


class TicketManager:
    """Atskiras valdiklis, atsakingas už bilietų kūrimą ir uždarymą."""

    STATE_FILE = Path("tickets_state.json")

    def __init__(self, bot: commands.Bot, config: BotConfig) -> None:
        self.bot = bot
        self.config = config
        self._state = {"next_id": 1}
        self._state_lock = asyncio.Lock()
        self._load_state()

    # region internal pagalbinės funkcijos
    def _load_state(self) -> None:
        if self.STATE_FILE.exists():
            try:
                with self.STATE_FILE.open("r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    if isinstance(data, dict) and "next_id" in data:
                        self._state.update(data)
            except Exception as exc:  # pragma: no cover - būtina apsauga nuo klaidos
                LOGGER.warning("Nepavyko nuskaityti tickets_state.json: %s", exc)

    def _save_state_unlocked(self) -> None:
        with self.STATE_FILE.open("w", encoding="utf-8") as fp:
            json.dump(self._state, fp, ensure_ascii=False, indent=2)

    async def _get_next_ticket_id(self) -> int:
        async with self._state_lock:
            ticket_id = int(self._state.get("next_id", 1))
            self._state["next_id"] = ticket_id + 1
            self._save_state_unlocked()
        return ticket_id

    async def _get_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        category = discord.utils.get(guild.categories, name=self.config.ticket_category_name)
        if category is None:
            LOGGER.info(
                "Kuriama kategorija '%s' gilde %s", self.config.ticket_category_name, guild.id
            )
            category = await guild.create_category(self.config.ticket_category_name, reason="Ticket sistemos kategorija")
        return category

    def _ticket_channel_name(self, user: discord.abc.User, ticket_id: int) -> str:
        username = discord.utils.remove_markdown(user.name)
        username = "".join(ch for ch in username.lower() if ch.isalnum() or ch in ("-", "_"))
        username = username.strip("-") or "naudotojas"
        return f"bilietas-{ticket_id}-{username}"[:95]

    def _channel_owner_id(self, channel: discord.TextChannel) -> Optional[int]:
        if channel.topic and "Owner:" in channel.topic:
            try:
                owner_str = channel.topic.split("Owner:")[-1].strip()
                return int(owner_str)
            except ValueError:
                return None
        return None

    async def _build_transcript(self, channel: discord.TextChannel) -> discord.File:
        buffer = StringIO()
        buffer.write(f"Transkriptas kanalui #{channel.name}\n")
        buffer.write(f"ID: {channel.id}\n")
        buffer.write("-" * 60 + "\n\n")
        async for message in channel.history(limit=None, oldest_first=True):
            created = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = f"{message.author} (ID: {message.author.id})"
            buffer.write(f"[{created}] {author}: {message.clean_content}\n")
            if message.attachments:
                for attachment in message.attachments:
                    buffer.write(f"    [Priedas] {attachment.url}\n")
        buffer.seek(0)
        filename = f"transkriptas_{channel.id}.txt"
        file_buffer = BytesIO(buffer.getvalue().encode("utf-8"))
        return discord.File(fp=file_buffer, filename=filename)

    # endregion

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        subject: str,
        description: str,
        ticket_category: TicketCategory,
    ) -> None:
        assert interaction.guild is not None, "Ši komanda gali būti naudojama tik serveryje"
        guild = interaction.guild
        user = interaction.user

        category = await self._get_category(guild)
        ticket_id = await self._get_next_ticket_id()
        channel_name = self._ticket_channel_name(user, ticket_id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        }

        staff_role = guild.get_role(self.config.staff_role_id)
        if staff_role is not None:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )

        topic = f"Ticket #{ticket_id} | Owner:{user.id}"
        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=topic,
            reason=f"Sukurtas naujas bilietas nuo {user}"
        )

        embed = discord.Embed(
            title=f"🎫 Bilietas #{ticket_id}",
            description="Naujas bilietas sukurtas! Žemiau pateikta informacija.",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Kategorija", value=ticket_category.label, inline=False)
        embed.add_field(name="Tema", value=subject[:1024] or "(nenurodyta)", inline=False)
        embed.add_field(name="Aprašymas", value=description[:1024] or "(nenurodytas)", inline=False)
        embed.set_footer(text=f"Bilietą sukūrė {user.display_name}")

        close_view = CloseTicketView(self)
        await channel.send(content=user.mention, embed=embed, view=close_view)

        await interaction.response.send_message(
            f"✅ Bilietas sukurtas: {channel.mention}",
            ephemeral=True,
        )

        transcript_channel = self._get_transcript_channel()
        if transcript_channel is not None:
            log_embed = discord.Embed(
                title="📥 Naujas bilietas",
                colour=discord.Colour.green(),
                description=f"Sukūrė {user.mention}\nKanalas: {channel.mention}",
            )
            log_embed.add_field(name="Kategorija", value=ticket_category.label, inline=False)
            log_embed.add_field(name="Tema", value=subject[:1024] or "(nenurodyta)", inline=False)
            await transcript_channel.send(embed=log_embed)

    async def close_ticket(self, interaction: discord.Interaction, reason: str | None) -> None:
        channel = interaction.channel
        assert isinstance(channel, discord.TextChannel)

        owner_id = self._channel_owner_id(channel)
        if owner_id is None:
            await interaction.response.send_message(
                "❌ Nepavyko nustatyti, kas sukūrė šį bilietą.", ephemeral=True
            )
            return

        user = interaction.user
        staff_role = channel.guild.get_role(self.config.staff_role_id)
        has_staff_role = staff_role in getattr(user, "roles", []) if staff_role else False
        if user.id != owner_id and not has_staff_role:
            await interaction.response.send_message(
                "❌ Tik bilieto savininkas arba komandos narys gali uždaryti bilietą.",
                ephemeral=True,
            )
            return

        transcript_channel = self._get_transcript_channel()
        transcript_file: Optional[discord.File] = None
        if transcript_channel is not None:
            transcript_file = await self._build_transcript(channel)

        new_name = f"uzdaryta-{channel.name}"[:95]
        overwrites = channel.overwrites
        owner = channel.guild.get_member(owner_id)
        if owner is not None:
            overwrites[owner] = discord.PermissionOverwrite(view_channel=False)

        await channel.edit(name=new_name, overwrites=overwrites, reason="Bilietas uždarytas")

        embed = discord.Embed(
            title="✅ Bilietas uždarytas",
            colour=discord.Colour.orange(),
            description=f"Bilietą uždarė {user.mention}.",
        )
        if reason:
            embed.add_field(name="Priežastis", value=reason[:1024], inline=False)

        await interaction.response.send_message(embed=embed)

        if transcript_channel is not None:
            log_embed = discord.Embed(
                title="📤 Bilietas uždarytas",
                colour=discord.Colour.orange(),
                description=f"Uždarytas kanalas {channel.mention}",
            )
            if owner is not None:
                log_embed.add_field(name="Savininkas", value=owner.mention, inline=False)
            if reason:
                log_embed.add_field(name="Priežastis", value=reason[:1024], inline=False)
            files = [transcript_file] if transcript_file else None
            await transcript_channel.send(embed=log_embed, files=files)

    def _get_transcript_channel(self) -> Optional[discord.TextChannel]:
        if self.config.transcript_channel_id is None:
            return None
        channel = self.bot.get_channel(self.config.transcript_channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
        return None


class TicketModal(discord.ui.Modal, title="Naujas bilietas"):
    def __init__(self, manager: TicketManager, category: TicketCategory) -> None:
        super().__init__()
        self.manager = manager
        self.category = category
        self.summary = discord.ui.TextInput(
            label="Trumpai aprašykite situaciją",
            placeholder="Įveskite pagrindinę informaciją",
            max_length=100,
        )
        self.details = discord.ui.TextInput(
            label="Išsamus aprašymas",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=800,
        )
        self.add_item(self.summary)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.manager.create_ticket(
            interaction,
            subject=self.summary.value or self.category.label,
            description=self.details.value or "",
            ticket_category=self.category,
        )


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, manager: TicketManager) -> None:
        options = [
            discord.SelectOption(
                label=category.label,
                description=category.description[:100],
                value=category.identifier,
            )
            for category in TICKET_CATEGORIES
        ]
        super().__init__(
            placeholder="Pasirinkite bilieto kategoriją",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket:select_category",
        )
        self.manager = manager

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        selected_id = self.values[0]
        category = next((c for c in TICKET_CATEGORIES if c.identifier == selected_id), None)
        if category is None:
            await interaction.response.send_message(
                "❌ Pasirinkta kategorija nebegalioja. Bandykite dar kartą.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TicketModal(self.manager, category))


class CloseTicketButton(discord.ui.Button):
    def __init__(self, manager: TicketManager) -> None:
        super().__init__(
            label="Uždaryti bilietą",
            style=discord.ButtonStyle.danger,
            custom_id="ticket:close",
        )
        self.manager = manager

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        owner_id = self.manager._channel_owner_id(interaction.channel) if isinstance(interaction.channel, discord.TextChannel) else None
        if owner_id is None:
            await interaction.response.send_message(
                "Šį mygtuką galima naudoti tik bilietų kanaluose.", ephemeral=True
            )
            return
        await interaction.response.send_modal(CloseTicketModal(self.manager))


class CloseTicketModal(discord.ui.Modal, title="Uždaryti bilietą"):
    def __init__(self, manager: TicketManager) -> None:
        super().__init__()
        self.manager = manager
        self.reason = discord.ui.TextInput(
            label="Priežastis",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=400,
            placeholder="Trumpai paaiškinkite, kodėl uždarote bilietą",
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.manager.close_ticket(interaction, reason=self.reason.value)


class TicketPanelView(discord.ui.View):
    def __init__(self, manager: TicketManager) -> None:
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect(manager))


class CloseTicketView(discord.ui.View):
    def __init__(self, manager: TicketManager) -> None:
        super().__init__(timeout=None)
        self.add_item(CloseTicketButton(manager))


class TicketBot(commands.Bot):
    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents)
        self.config = config
        self.manager = TicketManager(self, config)

    async def setup_hook(self) -> None:
        view = TicketPanelView(self.manager)
        self.add_view(view)
        close_view = CloseTicketView(self.manager)
        self.add_view(close_view)

        @self.tree.command(name="ticketpanel", description="Sukurti bilietų panelę šiame kanale")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def ticket_panel(interaction: discord.Interaction) -> None:
            embed = discord.Embed(
                title="🎫 Pagalbos bilietai",
                description=(
                    "Norėdami gauti pagalbą arba pranešti apie problemą, pasirinkite tinkamą kategoriją iš sąrašo apačioje. "
                    "Pateikę bilietą, detaliai aprašykite problemą, kad mūsų pagalbos operatoriai galėtų tiksliai suprasti situaciją "
                    "ir suteikti jums tinkamą informaciją ar pagalbą."
                ),
                colour=discord.Colour.blurple(),
            )
            await interaction.response.send_message(embed=embed, view=TicketPanelView(self.manager))

        @ticket_panel.error
        async def ticket_panel_error(
            interaction: discord.Interaction, error: app_commands.AppCommandError
        ) -> None:
            if isinstance(error, app_commands.errors.MissingPermissions):
                await interaction.response.send_message(
                    "❌ Šiai komandai reikia `Manage Server` leidimo.", ephemeral=True
                )
            else:
                LOGGER.error("Klaida vykdant /ticketpanel: %%s", error, exc_info=error)
                await interaction.response.send_message(
                    "Įvyko nenumatyta klaida. Bandykite dar kartą vėliau.", ephemeral=True
                )

    async def on_ready(self) -> None:
        LOGGER.info("Prisijungta kaip %s (ID: %s)", self.user, self.user.id if self.user else "n/a")


def main() -> None:
    config_path = Path("config.json")
    if not config_path.exists():
        raise SystemExit(
            "config.json nerastas. Nukopijuokite config.example.json ir užpildykite savo duomenimis."
        )
    config = BotConfig.load(config_path)

    bot = TicketBot(config)
    bot.run(config.token)


if __name__ == "__main__":
    main()
