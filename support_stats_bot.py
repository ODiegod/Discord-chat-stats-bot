"""
Discord bot that counts how many DIFFERENT PEOPLE team members helped
(via reply or mention), per day and per channel, for one or several people at once.
It can also post a weekly report automatically.

Commands:
  /support-stats          Opens a form (users, day range, optional month, channel IDs).
                          Leave "users" blank to use the saved team list.
  /team add|remove|list|clear      Manage the saved team list
  /report channels add|remove|list|clear   Manage the channels counted by the weekly report
  /report destination     Set the channel where the weekly report is posted
  /report schedule        Change the day and time of the automatic report
  /report window          Set how many hours a chat can continue before it counts as a new one
  /report status          Show the weekly report settings and the next run
  /report run-now         Post the weekly report right now (for testing)
  /report on | off        Enable / disable the automatic weekly report
  /access add|remove|list|clear   Choose which roles can use the bot (administrators only)

Access: only server administrators and the roles allowed with /access can use ANY command of
this bot. With no roles set, only administrators can use it.

Automatic report: by default every Thursday at 18:00 (US Eastern time) the bot posts, for
everyone on the saved team list, the stats of the 7 days ending that day (Friday through
Thursday). The day and time can be changed with /report schedule.

Usage:
    1. pip install -r requirements.txt        (or: pip install -U discord.py tzdata)
    2. Copy .env.example to .env (same folder as this script) and put your bot token in it:
           DISCORD_TOKEN=your_bot_token
    3. python support_stats_bot.py

Settings are read from the .env file (real environment variables win over it).
You only fill it in once. Everything except DISCORD_TOKEN is optional:
    GUILD_ID=123...                    only register the commands in this one server
                                       (default: every server the bot is in)
    TIMEZONE=America/New_York          timezone for days and the schedule (US Eastern: EST/EDT)
    TEAM_FILE / CONFIG_FILE            where data is saved (default: team.json / config.json
                                       in the same folder as this script)
    REPORT_WEEKDAY=3  REPORT_HOUR=18   DEFAULT schedule, 0=Mon ... 6=Sun (Thursday 18:00);
                                       change it per server with /report schedule
    CONVERSATION_WINDOW_HOURS=3        DEFAULT same-conversation window (change with /report window)
    ALLOWED_ROLE_IDS=123...,456...     DEFAULT roles allowed to use the bot, besides administrators
                                       (change per server with /access; the old name
                                       TEAM_EDITOR_ROLE_ID still works)
"""

import json
import os
import re
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

# ----------------------- .env FILE -----------------------
BASE_DIR = Path(__file__).resolve().parent  # data files and .env live next to this script


def load_env_file(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE per line, # comments, optional quotes and `export`.
    Variables already set in the real environment win. Empty values are ignored."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:  # inline comment on an unquoted value
            value = value.split(" #", 1)[0].strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


load_env_file(BASE_DIR / ".env")

# ----------------------- CONFIGURATION -----------------------
GUILD_ID = os.environ.get("GUILD_ID")  # optional
TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))  # US Eastern (EST in winter, EDT in summer)
TEAM_FILE = os.environ.get("TEAM_FILE", str(BASE_DIR / "team.json"))
CONFIG_FILE = os.environ.get("CONFIG_FILE", str(BASE_DIR / "config.json"))
# Roles allowed to use the bot (besides administrators). This is only the default for servers
# that haven't used /access. TEAM_EDITOR_ROLE_ID is the old name and still works.
ENV_ALLOWED_ROLE_IDS = list(dict.fromkeys(
    int(i) for i in re.findall(
        r"\d{15,20}",
        os.environ.get("ALLOWED_ROLE_IDS", "") + " " + os.environ.get("TEAM_EDITOR_ROLE_ID", ""),
    )
))
REPORT_WEEKDAY = int(os.environ.get("REPORT_WEEKDAY", "3"))  # 0=Mon ... 3=Thu
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "18"))
MAX_CHANNELS = 10
MAX_USERS = 10
MAX_RANGE_DAYS = 31
DEFAULT_WINDOW_HOURS = float(os.environ.get("CONVERSATION_WINDOW_HOURS", "3"))
# -------------------------------------------------------------

MONTHS = ["", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]
MONTH_ABBR = {name[:3].lower(): i for i, name in enumerate(MONTHS) if i}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NO_MENTIONS = discord.AllowedMentions.none()

intents = discord.Intents.default()
intents.message_content = True  # also enable in the Developer Portal (Bot > Privileged Intents)
intents.members = True          # same; needed to look users up by name


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        weekly_report_loop.start()

    async def sync_guild(self, guild: discord.abc.Snowflake) -> None:
        """Registers the slash commands in one server (shows up right away)."""
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)


client = Bot()


# ----------------------- JSON STORAGE -----------------------
def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)  # atomic write, avoids a corrupted file


# --- saved team list ---
def load_team(guild_id: int) -> list[int]:
    return [int(i) for i in _read_json(TEAM_FILE).get(str(guild_id), [])]


def save_team(guild_id: int, ids: list[int]) -> None:
    data = _read_json(TEAM_FILE)
    data[str(guild_id)] = ids
    _write_json(TEAM_FILE, data)


# --- weekly report settings ---
DEFAULT_CONFIG = {
    "channels": [],
    "destination": None,
    "enabled": True,
    "last_run": None,
    "weekday": REPORT_WEEKDAY,  # 0=Mon ... 6=Sun
    "hour": REPORT_HOUR,
    "minute": 0,
    "window_hours": DEFAULT_WINDOW_HOURS,  # same-conversation window; 0 = off
    "allowed_roles": None,                 # None = use ALLOWED_ROLE_IDS from .env; [] = admins only
}


def load_config(guild_id: int) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read_json(CONFIG_FILE).get(str(guild_id), {}))
    return cfg


def update_config(guild_id: int, **changes) -> dict:
    data = _read_json(CONFIG_FILE)
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(data.get(str(guild_id), {}))
    cfg.update(changes)
    data[str(guild_id)] = cfg
    _write_json(CONFIG_FILE, data)
    return cfg


# ----------------------- ACCESS CONTROL -----------------------
ACCESS_DENIED = ("⛔ You don't have access to this bot. Ask a server administrator to allow your role "
                 "with `/access add`.")
ADMIN_ONLY = "⛔ Only server administrators can change who has access to the bot."


def allowed_role_ids(guild_id: int) -> list[int]:
    """Roles allowed in this server: the ones set with /access, else the default from .env."""
    saved = load_config(guild_id)["allowed_roles"]
    return list(ENV_ALLOWED_ROLE_IDS) if saved is None else [int(r) for r in saved]


def is_admin(member) -> bool:
    """True for members with the Administrator permission (the server owner always has it)."""
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and perms.administrator)


def has_access(interaction: discord.Interaction) -> bool:
    """Administrators, or members with one of the allowed roles. Everyone else is blocked.
    (Adding the @everyone role to the allowed roles opens the bot to all members.)"""
    member = interaction.user
    if is_admin(member):
        return True
    allowed = set(allowed_role_ids(interaction.guild_id))
    return any(getattr(role, "id", None) in allowed for role in getattr(member, "roles", []))


async def access_check(interaction: discord.Interaction) -> bool:
    return has_access(interaction)


class GatedGroup(app_commands.Group):
    """A command group whose commands only run for administrators and allowed roles."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return has_access(interaction)


# ----------------------- USER RESOLUTION -----------------------
async def resolve_user(guild: discord.Guild, text: str) -> discord.Member | None:
    """Accepts an ID, an @mention or a username / display name."""
    found = re.search(r"\d{15,20}", text)
    if found:
        user_id = int(found.group())
        member = guild.get_member(user_id)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    name = text.strip().lstrip("@")
    member = guild.get_member_named(name)
    if member:
        return member
    matches = await guild.query_members(query=name, limit=1)
    return matches[0] if matches else None


async def resolve_targets(guild: discord.Guild, text: str) -> tuple[dict[int, str], list[str]]:
    """
    Parses a comma-separated list of names, @mentions or IDs.
    Returns ({user_id: display_name}, [tokens that could not be found]).
    """
    targets: dict[int, str] = {}
    missing: list[str] = []
    tokens = [t.strip() for t in re.split(r"[,;\n]+", text) if t.strip()]

    for token in tokens:
        ids = re.findall(r"\d{15,20}", token)
        queries = ids if ids else [token]  # several mentions can share one token
        for q in queries:
            member = await resolve_user(guild, q)
            if member:
                targets[member.id] = member.display_name
            else:
                missing.append(q)
    return targets, missing


async def saved_team_targets(guild: discord.Guild) -> dict[int, str]:
    """Loads the saved team list. Keeps people who left the server (by ID)."""
    targets: dict[int, str] = {}
    for uid in load_team(guild.id):
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except discord.HTTPException:
                member = None
        if member:
            targets[uid] = member.display_name
            continue
        try:
            targets[uid] = (await client.fetch_user(uid)).display_name
        except discord.HTTPException:
            targets[uid] = str(uid)
    return targets


# ----------------------- CHANNEL ACCESS -----------------------
def read_access_error(channel, user: discord.abc.User | None, bot_member: discord.Member) -> str | None:
    """Returns an error message if the channel can't be read, else None."""
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return f"`{getattr(channel, 'id', channel)}` is not a text channel."
    if user is not None:
        up = channel.permissions_for(user)
        if not (up.view_channel and up.read_message_history):
            return f"You don't have access to #{channel.name}."
    bp = channel.permissions_for(bot_member)
    if not (bp.view_channel and bp.read_message_history):
        return f"The bot doesn't have access to #{channel.name}."
    return None


def post_access_error(channel, bot_member: discord.Member) -> str | None:
    """Returns an error message if the bot can't post reports in this channel."""
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return "The destination must be a text channel."
    bp = channel.permissions_for(bot_member)
    missing = [name for name, ok in [
        ("View Channel", bp.view_channel), ("Send Messages", bp.send_messages),
        ("Embed Links", bp.embed_links)] if not ok]
    if missing:
        return f"The bot is missing these permissions in #{channel.name}: {', '.join(missing)}."
    return None


# ----------------------- DATE PARSING -----------------------
def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    """Adds/subtracts months. Returns (year, month)."""
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def parse_month(text: str, today: datetime) -> tuple[int, int] | None:
    """'9', 'september', 'sep', '9/2026', 'september 2026' -> (month, year). Blank -> None."""
    t = text.strip().lower()
    if not t:
        return None

    nums = [int(n) for n in re.findall(r"\d+", t)]
    letters = re.sub(r"[^a-z]", "", t)[:3]

    if letters:  # month name (optionally followed by the year)
        month = MONTH_ABBR.get(letters)
        year = nums[0] if nums else None
    else:        # month number (optionally followed by the year)
        month = nums[0] if nums else None
        year = nums[1] if len(nums) > 1 else None

    if not month or not 1 <= month <= 12:
        raise ValueError("Invalid month. Use a number (9), a name (September) or month/year (9/2026).")

    if year is None:
        # a month that hasn't happened yet this year, with no year given => last year
        year = today.year - 1 if month > today.month else today.year
    elif year < 100:
        year += 2000
    return month, year


def parse_period(days_text: str, month_text: str, today: datetime) -> tuple[datetime, datetime]:
    """
    Turns the form fields into (start, exclusive end).

    - days: '5-8', '5 to 8' or '12'
    - month (optional): the month of the FIRST day. Blank = current month.
    - If the first day is greater than the last (e.g. '29-3'), the range crosses a
      month boundary: it starts in the given month and ends in the following one.
      With no month given, it assumes the range ends in the current month and
      started in the previous one.
    """
    numbers = [int(n) for n in re.findall(r"\d+", days_text)]
    if len(numbers) == 1:
        numbers = numbers * 2
    if len(numbers) != 2:
        raise ValueError("Enter a single day (e.g. 12) or a range (e.g. 5-8 or 29-3).")
    d1, d2 = numbers
    crosses_month = d1 > d2

    month_year = parse_month(month_text, today)
    if month_year:
        start_month, start_year = month_year
    elif crosses_month:
        start_year, start_month = shift_month(today.year, today.month, -1)
    else:
        start_month, start_year = today.month, today.year

    if crosses_month:
        end_year, end_month = shift_month(start_year, start_month, +1)
    else:
        end_month, end_year = start_month, start_year

    try:
        start = datetime(start_year, start_month, d1, tzinfo=TZ)
    except ValueError:
        raise ValueError(f"Day {d1} does not exist in {MONTHS[start_month]} {start_year}.")
    try:
        last_day = datetime(end_year, end_month, d2, tzinfo=TZ)
    except ValueError:
        raise ValueError(f"Day {d2} does not exist in {MONTHS[end_month]} {end_year}.")

    end = last_day + timedelta(days=1)
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValueError(f"The maximum range is {MAX_RANGE_DAYS} days.")
    return start, end


def weekly_period(today: date) -> tuple[datetime, datetime]:
    """The 7 days ending today (Friday through Thursday when the report runs on Thursday)."""
    start = datetime.combine(today - timedelta(days=6), dt_time.min, tzinfo=TZ)
    end = datetime.combine(today + timedelta(days=1), dt_time.min, tzinfo=TZ)
    return start, end


def next_run_time(now: datetime, weekday: int, hour: int, minute: int) -> datetime:
    """Next scheduled report time (timezone-aware) for the given weekday/hour/minute."""
    candidate = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=TZ)
    candidate += timedelta(days=(weekday - now.weekday()) % 7)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def parse_time_of_day(text: str) -> tuple[int, int]:
    """'20:00', '20', '8pm', '8:30 pm' -> (hour, minute)."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm)?\s*", text.lower())
    if not m:
        raise ValueError("Invalid time. Use 24h like `20:00`, or `8pm`.")
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            raise ValueError("With am/pm, the hour must be between 1 and 12.")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Invalid time. Use 24h like `20:00`, or `8pm`.")
    return hour, minute


def schedule_text(cfg: dict) -> str:
    return f"{WEEKDAY_NAMES[cfg['weekday']]} at {cfg['hour']:02d}:{cfg['minute']:02d} ({TZ.key})"


# ----------------------- COUNTING -----------------------
async def people_helped(msg: discord.Message, author_id: int) -> set[int]:
    """IDs of the people helped in this message (mentions + reply)."""
    ids = set()

    for u in msg.mentions:
        if not u.bot and u.id != author_id:
            ids.add(u.id)

    if msg.reference and msg.reference.message_id:
        ref = msg.reference.resolved
        if not isinstance(ref, discord.Message):
            try:
                ref = await msg.channel.fetch_message(msg.reference.message_id)
            except discord.HTTPException:
                ref = None
        if isinstance(ref, discord.Message):
            if not ref.author.bot and ref.author.id != author_id:
                ids.add(ref.author.id)

    return ids


@dataclass
class UserStats:
    by_day_channel: dict = field(default_factory=lambda: defaultdict(set))  # (date, channel_id) -> {ids}
    by_day: dict = field(default_factory=lambda: defaultdict(set))          # date -> {ids}
    by_channel: dict = field(default_factory=lambda: defaultdict(set))      # channel_id -> {ids}
    total: set = field(default_factory=set)                                 # every person once, whole period

    def channel_total(self, channel_id: int) -> int:
        """Sum of the daily counts for one channel (a person on 2 days counts 2)."""
        return sum(len(v) for (_, cid), v in self.by_day_channel.items() if cid == channel_id)

    def days_total(self) -> int:
        """Sum of the daily totals (a person on 2 days counts 2)."""
        return sum(len(v) for v in self.by_day.values())


async def collect_stats(channels, targets: dict[int, str], start: datetime, end: datetime,
                        window_hours: float = 0) -> dict[int, UserStats]:
    """
    One pass over each channel, counting for all users at once.

    window_hours: if the same team member already replied to / mentioned the same person in
    the same channel within this many hours before, it is the SAME conversation and does not
    count as a new one. This stops a chat that crosses midnight from counting on both days.
    Each new reply in a chat refreshes the window, so a long back-and-forth stays one
    conversation. 0 turns it off (each day then counts on its own).
    """
    window = timedelta(hours=window_hours)
    # Messages from before `start` are read only to know whether a chat was already
    # going on (e.g. it began right before the first day of the period). They are not counted.
    fetch_from = start - window if window_hours > 0 else start

    stats = {uid: UserStats() for uid in targets}
    last_seen: dict[tuple[int, int, int], datetime] = {}  # (member, person, channel) -> last interaction

    for channel in channels:
        async for msg in channel.history(limit=None, after=fetch_from, before=end, oldest_first=True):
            st = stats.get(msg.author.id)
            if st is None:
                continue
            ids = await people_helped(msg, msg.author.id)
            if not ids:
                continue

            new_ids = set()
            for person_id in ids:
                key = (msg.author.id, person_id, channel.id)
                previous = last_seen.get(key)
                is_new = window_hours <= 0 or previous is None or (msg.created_at - previous) > window
                last_seen[key] = msg.created_at
                if is_new:
                    new_ids.add(person_id)

            if msg.created_at < start or not new_ids:
                continue
            day = msg.created_at.astimezone(TZ).date()
            st.by_day_channel[(day, channel.id)] |= new_ids
            st.by_day[day] |= new_ids
            st.by_channel[channel.id] |= new_ids
            st.total |= new_ids
    return stats


# ----------------------- REPORT BUILDERS -----------------------
def _header(first_col: str, width: int, channels) -> str:
    names = "".join(f"{c.name[:12]:>13}" for c in channels)
    return f"{first_col:<{width}}{names}{'Total':>7}"


def summary_table(targets: dict[int, str], stats: dict[int, UserStats], channels) -> str:
    """One row per user, using each person's TOTAL (sum of the daily counts). The last row is
    the plain sum of the rows above, so a person helped by two team members counts for both."""
    header = _header("User", 14, channels)
    sep = "-" * len(header)
    lines = [header, sep]
    for uid, name in targets.items():
        st = stats[uid]
        cols = "".join(f"{st.channel_total(c.id):>13}" for c in channels)
        lines.append(f"{name[:13]:<14}{cols}{st.days_total():>7}")
    lines.append(sep)
    cols = "".join(
        f"{sum(stats[uid].channel_total(c.id) for uid in targets):>13}" for c in channels
    )
    grand_total = sum(stats[uid].days_total() for uid in targets)
    lines.append(f"{'Team total':<14}{cols}{grand_total:>7}")
    return "\n".join(lines)


def daily_table(st: UserStats, channels, first_day: date, last_day: date) -> str:
    """Daily rows, then two summary rows:
    Total          = sum of the daily counts (someone helped on 2 days counts 2)
    Unique people  = each person counted once in the whole period"""
    width = 14
    header = _header("Day", width, channels)
    sep = "-" * len(header)
    lines = [header, sep]

    day = first_day
    while day <= last_day:
        label = f"{day:%d/%m} {WEEKDAYS[day.weekday()]}"
        cols = "".join(f"{len(st.by_day_channel[(day, c.id)]):>13}" for c in channels)
        lines.append(f"{label:<{width}}{cols}{len(st.by_day[day]):>7}")
        day += timedelta(days=1)

    lines.append(sep)
    cols = "".join(f"{st.channel_total(c.id):>13}" for c in channels)
    lines.append(f"{'Total':<{width}}{cols}{st.days_total():>7}")
    cols = "".join(f"{len(st.by_channel[c.id]):>13}" for c in channels)
    lines.append(f"{'Unique people':<{width}}{cols}{len(st.total):>7}")
    return "\n".join(lines)


def code_block(text: str) -> str:
    block = f"```\n{text}\n```"
    if len(block) > 4000:  # Discord embed description limit is 4096
        return "Table too long to display here. Try a shorter day range or fewer channels."
    return block


def build_report(targets: dict[int, str], stats: dict[int, UserStats], channels,
                 first_day: date, last_day: date, window_hours: float = 0) -> list[discord.Embed]:
    """Returns the embeds to send (always at least one)."""
    footer = (f"{first_day:%d/%m} to {last_day:%d/%m/%Y} • people helped via reply or mention "
              "• Total = sum of the days • Unique people = each person once in the period")
    if window_hours > 0:
        footer += f" • chats continuing within {window_hours:g}h count once"
    embeds: list[discord.Embed] = []

    if len(targets) > 1:
        embed = discord.Embed(
            title="Team summary",
            description=code_block(summary_table(targets, stats, channels)),
            color=discord.Color.green(),
        )
        embed.set_footer(text=footer)
        embeds.append(embed)

    for uid, name in targets.items():
        embed = discord.Embed(
            title=f"Support stats for {name}",
            description=code_block(daily_table(stats[uid], channels, first_day, last_day)),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=footer)
        embeds.append(embed)

    return embeds


# ----------------------- FORM (/support-stats) -----------------------
class SupportStatsForm(discord.ui.Modal, title="Support stats"):
    users = discord.ui.TextInput(
        label="Users (blank = saved team list)",
        style=discord.TextStyle.paragraph,
        placeholder="Comma-separated names, @mentions or IDs. E.g. john, @mary, 1234...",
        required=False,
        max_length=300,
    )
    days = discord.ui.TextInput(
        label="Day range",
        placeholder="E.g. 5-8, 12 (single day) or 29-3 (crosses months)",
        max_length=20,
    )
    month = discord.ui.TextInput(
        label="Month of the first day (optional)",
        placeholder="E.g. 9, September or 9/2026. Blank = current month",
        required=False,
        max_length=20,
    )
    channels = discord.ui.TextInput(
        label="Channel IDs (comma-separated)",
        style=discord.TextStyle.paragraph,
        placeholder="E.g. 111111111111111111, 222222222222222222",
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not has_access(interaction):
            return await interaction.response.send_message(ACCESS_DENIED, ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        today = datetime.now(TZ)

        # --- period ---
        try:
            start, end = parse_period(self.days.value, self.month.value, today)
        except ValueError as e:
            return await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        first_day, last_day = start.date(), (end - timedelta(days=1)).date()

        # --- users: typed list, or the saved team ---
        missing: list[str] = []
        if self.users.value.strip():
            targets, missing = await resolve_targets(guild, self.users.value)
        else:
            targets = await saved_team_targets(guild)
            if not targets:
                return await interaction.followup.send(
                    "⚠️ No users given and the saved team list is empty. "
                    "Type some users in the form, or add people with `/team add`.", ephemeral=True)
        if not targets:
            return await interaction.followup.send(
                "⚠️ I couldn't find any of those users. Try IDs or @mentions.", ephemeral=True)
        if len(targets) > MAX_USERS:
            return await interaction.followup.send(f"⚠️ Maximum of {MAX_USERS} users at once.", ephemeral=True)

        # --- channels ---
        channel_ids = list(dict.fromkeys(int(i) for i in re.findall(r"\d{15,20}", self.channels.value)))
        if not channel_ids:
            return await interaction.followup.send("⚠️ No valid channel ID found.", ephemeral=True)
        if len(channel_ids) > MAX_CHANNELS:
            return await interaction.followup.send(f"⚠️ Maximum of {MAX_CHANNELS} channels.", ephemeral=True)

        channels = []
        for cid in channel_ids:
            channel = guild.get_channel_or_thread(cid)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(cid)
                except discord.HTTPException:
                    return await interaction.followup.send(f"⚠️ Channel `{cid}` not found.", ephemeral=True)
            error = read_access_error(channel, interaction.user, guild.me)
            if error:
                return await interaction.followup.send(f"⚠️ {error}", ephemeral=True)
            channels.append(channel)

        # --- count and report ---
        window_hours = load_config(guild.id)["window_hours"]
        stats = await collect_stats(channels, targets, start, end, window_hours)
        embeds = build_report(targets, stats, channels, first_day, last_day, window_hours)
        note = f"⚠️ Not found, skipped: {', '.join(missing)}" if missing else None

        for i, embed in enumerate(embeds):
            kwargs = {"embed": embed, "ephemeral": True, "allowed_mentions": NO_MENTIONS}
            if i == 0 and note:
                kwargs["content"] = note
            await interaction.followup.send(**kwargs)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"Form error: {error!r}")
        msg = "⚠️ Something went wrong while generating the report."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


# ----------------------- WEEKLY REPORT -----------------------
async def run_weekly_report(guild: discord.Guild) -> str | None:
    """
    Builds and posts the weekly report for one server (the 7 days ending today).
    Returns an error message (str) if it could not be posted, else None.
    """
    cfg = load_config(guild.id)

    dest_id = cfg.get("destination")
    dest = guild.get_channel_or_thread(dest_id) if dest_id else None
    if dest is None:
        return "No destination channel set. Use `/report destination`."
    error = post_access_error(dest, guild.me)
    if error:
        return error

    if not cfg.get("channels"):
        return "No channels to count. Use `/report channels add`."
    channels = []
    for cid in cfg["channels"]:
        channel = guild.get_channel_or_thread(cid)
        if channel is None:
            return f"Channel `{cid}` was not found (deleted?). Update it with `/report channels add` and `/report channels remove`."
        error = read_access_error(channel, None, guild.me)
        if error:
            return error
        channels.append(channel)

    targets = await saved_team_targets(guild)
    if not targets:
        return "The team list is empty. Add people with `/team add`."

    today = datetime.now(TZ).date()
    start, end = weekly_period(today)
    first_day, last_day = start.date(), today

    window_hours = cfg["window_hours"]
    stats = await collect_stats(channels, targets, start, end, window_hours)
    embeds = build_report(targets, stats, channels, first_day, last_day, window_hours)

    header = f"📊 **Weekly support report**: {first_day:%a %d/%m} to {last_day:%a %d/%m/%Y}"
    for i, embed in enumerate(embeds):
        kwargs = {"embed": embed, "allowed_mentions": NO_MENTIONS}
        if i == 0:
            kwargs["content"] = header
        await dest.send(**kwargs)
    return None


def is_due(cfg: dict, now: datetime) -> bool:
    """True if this server's report should run now (at most once per day)."""
    return bool(
        cfg["enabled"]
        and cfg["destination"]
        and cfg["last_run"] != now.date().isoformat()
        and now.weekday() == cfg["weekday"]
        and (now.hour, now.minute) >= (cfg["hour"], cfg["minute"])
    )


async def run_due_reports() -> None:
    """Runs the report for every server whose scheduled time has arrived."""
    now = datetime.now(TZ)
    for guild in client.guilds:
        cfg = load_config(guild.id)
        if not is_due(cfg, now):
            continue
        # Mark as run BEFORE posting: guarantees at most one attempt per day, so a
        # failing setup warns once instead of every minute. Use /report run-now to retry.
        update_config(guild.id, last_run=now.date().isoformat())
        try:
            error = await run_weekly_report(guild)
            if error:
                print(f"[weekly report] {guild.name}: {error}")
                dest = guild.get_channel_or_thread(cfg["destination"])
                if dest is not None and post_access_error(dest, guild.me) is None:
                    await dest.send(f"⚠️ Weekly report failed: {error} "
                                    "After fixing it, use `/report run-now`.")
        except Exception as e:  # never let one server stop the loop
            print(f"[weekly report] {guild.name}: unexpected error {e!r}")


@tasks.loop(minutes=1)
async def weekly_report_loop():
    """Checks every minute whether any server's report is due (also catches up after downtime)."""
    await run_due_reports()


@weekly_report_loop.before_loop
async def before_weekly_report_loop():
    await client.wait_until_ready()


# ----------------------- COMMANDS -----------------------
@client.tree.command(name="support-stats", description="Count different people helped, per day and channel")
@app_commands.guild_only()
@app_commands.check(access_check)
async def support_stats(interaction: discord.Interaction):
    await interaction.response.send_modal(SupportStatsForm())


@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Tells the user why a command was refused (instead of Discord's generic failure message)."""
    if isinstance(error, app_commands.CheckFailure):
        text = ACCESS_DENIED
    else:
        traceback.print_exception(type(error), error, error.__traceback__)
        text = "⚠️ Something went wrong while running that command."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


# --- /team ---
team_group = GatedGroup(
    name="team",
    description="Manage the saved list of people used by /support-stats and the weekly report",
    guild_only=True,
)


@team_group.command(name="add", description="Add people to the saved team list")
@app_commands.describe(user="Person to add", user2="Another person (optional)", user3="Another person (optional)",
                       user4="Another person (optional)", user5="Another person (optional)")
async def team_add(interaction: discord.Interaction, user: discord.Member,
                   user2: Optional[discord.Member] = None, user3: Optional[discord.Member] = None,
                   user4: Optional[discord.Member] = None, user5: Optional[discord.Member] = None):
    current = load_team(interaction.guild_id)
    added = []
    for m in (user, user2, user3, user4, user5):
        if m is not None and not m.bot and m.id not in current and m.id not in [a.id for a in added]:
            added.append(m)

    if len(current) + len(added) > MAX_USERS:
        return await interaction.response.send_message(
            f"⚠️ The team list can have at most {MAX_USERS} people. Nothing was changed.", ephemeral=True)
    if not added:
        return await interaction.response.send_message(
            "Nothing to add: those people are already on the list (or are bots).", ephemeral=True)

    save_team(interaction.guild_id, current + [m.id for m in added])
    await interaction.response.send_message(
        "✅ Added: " + ", ".join(m.mention for m in added) + f"\nTeam size: {len(current) + len(added)}",
        ephemeral=True, allowed_mentions=NO_MENTIONS)


@team_group.command(name="remove", description="Remove people from the saved team list")
@app_commands.describe(user="Person to remove", user2="Another person (optional)", user3="Another person (optional)",
                       user_id="ID of someone who left the server (optional)")
async def team_remove(interaction: discord.Interaction, user: Optional[discord.Member] = None,
                      user2: Optional[discord.Member] = None, user3: Optional[discord.Member] = None,
                      user_id: Optional[str] = None):
    to_remove = {m.id for m in (user, user2, user3) if m is not None}
    if user_id:
        to_remove |= {int(i) for i in re.findall(r"\d{15,20}", user_id)}
    if not to_remove:
        return await interaction.response.send_message("⚠️ Pick at least one person to remove.", ephemeral=True)

    current = load_team(interaction.guild_id)
    removed = [uid for uid in current if uid in to_remove]
    if not removed:
        return await interaction.response.send_message("Nobody from that selection was on the list.", ephemeral=True)

    save_team(interaction.guild_id, [uid for uid in current if uid not in to_remove])
    await interaction.response.send_message(
        "🗑️ Removed: " + ", ".join(f"<@{uid}>" for uid in removed) + f"\nTeam size: {len(current) - len(removed)}",
        ephemeral=True, allowed_mentions=NO_MENTIONS)


@team_group.command(name="list", description="Show the saved team list")
async def team_list(interaction: discord.Interaction):
    ids = load_team(interaction.guild_id)
    if not ids:
        return await interaction.response.send_message(
            "The team list is empty. Add people with `/team add`.", ephemeral=True)
    lines = "\n".join(f"{i}. <@{uid}>" for i, uid in enumerate(ids, 1))
    embed = discord.Embed(title=f"Team list ({len(ids)})", description=lines, color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)


@team_group.command(name="clear", description="Empty the saved team list")
async def team_clear(interaction: discord.Interaction):
    save_team(interaction.guild_id, [])
    await interaction.response.send_message("🗑️ The team list is now empty.", ephemeral=True)


# --- /report ---
report_group = GatedGroup(
    name="report",
    description="Settings for the automatic weekly report",
    guild_only=True,
)


# --- /report channels (add / remove / list / clear) ---
channels_group = GatedGroup(
    name="channels",
    description="Manage the channels counted by the weekly report",
    parent=report_group,
)


def _channel_ids(pickers, extra_text: str | None) -> list[int]:
    """Collects IDs from channel pickers plus any IDs/#mentions typed as text."""
    ids = [c.id for c in pickers if c is not None]
    if extra_text:
        ids += [int(i) for i in re.findall(r"\d{15,20}", extra_text)]
    return list(dict.fromkeys(ids))


@channels_group.command(name="add", description="Add channels to the weekly report")
@app_commands.describe(channel1="Channel to add", channel2="Another channel (optional)",
                       channel3="Another channel (optional)", channel4="Another channel (optional)",
                       channel5="Another channel (optional)",
                       extra_ids="More channel IDs, comma-separated (optional)")
async def report_channels_add(interaction: discord.Interaction, channel1: discord.TextChannel,
                              channel2: Optional[discord.TextChannel] = None,
                              channel3: Optional[discord.TextChannel] = None,
                              channel4: Optional[discord.TextChannel] = None,
                              channel5: Optional[discord.TextChannel] = None,
                              extra_ids: Optional[str] = None):
    guild = interaction.guild
    wanted = _channel_ids((channel1, channel2, channel3, channel4, channel5), extra_ids)
    current = load_config(guild.id)["channels"]
    new = [cid for cid in wanted if cid not in current]
    if not new:
        return await interaction.response.send_message(
            "Nothing to add: those channels are already on the list.", ephemeral=True)
    if len(current) + len(new) > MAX_CHANNELS:
        return await interaction.response.send_message(
            f"⚠️ The channel list can have at most {MAX_CHANNELS} channels. Nothing was changed.", ephemeral=True)

    for cid in new:
        channel = guild.get_channel_or_thread(cid)
        if channel is None:
            return await interaction.response.send_message(f"⚠️ Channel `{cid}` not found.", ephemeral=True)
        error = read_access_error(channel, interaction.user, guild.me)
        if error:
            return await interaction.response.send_message(f"⚠️ {error}", ephemeral=True)

    update_config(guild.id, channels=current + new)
    await interaction.response.send_message(
        "✅ Added: " + ", ".join(f"<#{cid}>" for cid in new) + f"\nChannels on the list: {len(current) + len(new)}",
        ephemeral=True, allowed_mentions=NO_MENTIONS)


@channels_group.command(name="remove", description="Remove channels from the weekly report")
@app_commands.describe(channel1="Channel to remove", channel2="Another channel (optional)",
                       channel3="Another channel (optional)",
                       channel_id="ID of a deleted channel, or several separated by commas (optional)")
async def report_channels_remove(interaction: discord.Interaction,
                                 channel1: Optional[discord.TextChannel] = None,
                                 channel2: Optional[discord.TextChannel] = None,
                                 channel3: Optional[discord.TextChannel] = None,
                                 channel_id: Optional[str] = None):
    to_remove = set(_channel_ids((channel1, channel2, channel3), channel_id))
    if not to_remove:
        return await interaction.response.send_message("⚠️ Pick at least one channel to remove.", ephemeral=True)

    current = load_config(interaction.guild_id)["channels"]
    removed = [cid for cid in current if cid in to_remove]
    if not removed:
        return await interaction.response.send_message("None of those channels were on the list.", ephemeral=True)

    remaining = [cid for cid in current if cid not in to_remove]
    update_config(interaction.guild_id, channels=remaining)
    await interaction.response.send_message(
        "🗑️ Removed: " + ", ".join(f"<#{cid}>" for cid in removed) + f"\nChannels on the list: {len(remaining)}",
        ephemeral=True, allowed_mentions=NO_MENTIONS)


@channels_group.command(name="list", description="Show the channels counted by the weekly report")
async def report_channels_list(interaction: discord.Interaction):
    ids = load_config(interaction.guild_id)["channels"]
    if not ids:
        return await interaction.response.send_message(
            "No channels set. Add some with `/report channels add`.", ephemeral=True)
    lines = []
    for i, cid in enumerate(ids, 1):
        missing = interaction.guild.get_channel_or_thread(cid) is None
        lines.append(f"{i}. <#{cid}>" + (f" ⚠️ not found (`{cid}`), remove it with `/report channels remove`" if missing else ""))
    embed = discord.Embed(title=f"Counted channels ({len(ids)})", description="\n".join(lines),
                          color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)


@channels_group.command(name="clear", description="Remove all channels from the weekly report")
async def report_channels_clear(interaction: discord.Interaction):
    update_config(interaction.guild_id, channels=[])
    await interaction.response.send_message("🗑️ The channel list is now empty.", ephemeral=True)


# --- /report schedule ---
@report_group.command(name="schedule", description="Change the day and time of the automatic weekly report")
@app_commands.describe(day="Day of the week", time="24h like 20:00, or 8pm (in the bot's timezone)")
@app_commands.choices(day=[app_commands.Choice(name=n, value=i) for i, n in enumerate(WEEKDAY_NAMES)])
async def report_schedule(interaction: discord.Interaction, day: Optional[app_commands.Choice[int]] = None,
                          time: Optional[str] = None):
    gid = interaction.guild_id
    if day is None and time is None:
        return await interaction.response.send_message(
            f"The weekly report runs on **{schedule_text(load_config(gid))}**.\n"
            "Change it with `/report schedule day:<day> time:<time>`. You can set just one of them.",
            ephemeral=True)

    changes = {}
    if day is not None:
        changes["weekday"] = day.value
    if time is not None:
        try:
            changes["hour"], changes["minute"] = parse_time_of_day(time)
        except ValueError as e:
            return await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)

    cfg = update_config(gid, **changes)
    now = datetime.now(TZ)

    # If the new time already passed today, don't fire immediately on the next tick.
    skipped_today = now.weekday() == cfg["weekday"] and (now.hour, now.minute) >= (cfg["hour"], cfg["minute"])
    if skipped_today:
        cfg = update_config(gid, last_run=now.date().isoformat())

    when = next_run_time(now, cfg["weekday"], cfg["hour"], cfg["minute"])
    msg = f"✅ Weekly report scheduled for **{schedule_text(cfg)}**.\nNext run: <t:{int(when.timestamp())}:F>"
    if skipped_today:
        msg += "\nThat time has already passed today, so it won't run again today. Use `/report run-now` if you want it now."
    if not cfg["enabled"]:
        msg += "\n⏸️ The automatic report is currently off. Turn it on with `/report on`."
    await interaction.response.send_message(msg, ephemeral=True)


@report_group.command(name="window", description="Set how many hours a chat can continue before it counts as a new one")
@app_commands.describe(hours="0 to 24 (0 = count each day separately). Leave empty to see the current value")
async def report_window(interaction: discord.Interaction, hours: Optional[app_commands.Range[float, 0, 24]] = None):
    gid = interaction.guild_id
    if hours is None:
        current = load_config(gid)["window_hours"]
        state = f"**{current:g} hours**" if current > 0 else "**off** (each day counts on its own)"
        return await interaction.response.send_message(
            f"Conversation window: {state}.\n"
            "If the same team member replies to / mentions the same person in the same channel within this "
            "time of their previous message, it is the same conversation and doesn't count again "
            "(for example, a chat that crosses midnight). It applies to `/support-stats` and the weekly report.\n"
            "Change it with `/report window hours:<number>`.", ephemeral=True)

    update_config(gid, window_hours=float(hours))
    if hours > 0:
        msg = (f"✅ Conversation window set to **{hours:g} hours**. A person answered again by the same team "
               f"member in the same channel within {hours:g}h of their last message counts as the same conversation.")
    else:
        msg = "✅ Conversation window turned **off**. Each day now counts on its own."
    await interaction.response.send_message(msg, ephemeral=True)


@report_group.command(name="destination", description="Set the channel where the weekly report is posted")
@app_commands.describe(channel="Channel to post the report in")
async def report_destination(interaction: discord.Interaction, channel: discord.TextChannel):
    error = post_access_error(channel, interaction.guild.me)
    if error:
        return await interaction.response.send_message(f"⚠️ {error}", ephemeral=True)

    update_config(interaction.guild_id, destination=channel.id, enabled=True)
    await interaction.response.send_message(
        f"✅ Weekly report will be posted in {channel.mention}.", ephemeral=True, allowed_mentions=NO_MENTIONS)


@report_group.command(name="status", description="Show the weekly report settings and the next run")
async def report_status(interaction: discord.Interaction):
    cfg = load_config(interaction.guild_id)
    team = load_team(interaction.guild_id)
    when = next_run_time(datetime.now(TZ), cfg["weekday"], cfg["hour"], cfg["minute"])

    channels = ", ".join(f"<#{c}>" for c in cfg["channels"]) or "not set (`/report channels add`)"
    dest = f"<#{cfg['destination']}>" if cfg["destination"] else "not set (`/report destination`)"
    team_txt = f"{len(team)} people" if team else "empty (`/team add`)"
    state = "✅ on" if cfg["enabled"] else "⏸️ off"

    embed = discord.Embed(title="Weekly report", color=discord.Color.blurple())
    embed.add_field(name="Status", value=state, inline=True)
    embed.add_field(name="Schedule", value=schedule_text(cfg), inline=False)
    embed.add_field(name="Next run",
                    value=f"<t:{int(when.timestamp())}:F>" if cfg["enabled"] else "— (report is off)", inline=False)
    embed.add_field(name="Team", value=team_txt, inline=False)
    embed.add_field(name="Counted channels", value=channels, inline=False)
    embed.add_field(name="Posted in", value=dest, inline=False)
    window = cfg["window_hours"]
    embed.add_field(name="Conversation window", value=f"{window:g} hours" if window > 0 else "off", inline=False)
    embed.set_footer(text="Period: the 7 days ending on the report day. Change the schedule with /report schedule.")
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)


@report_group.command(name="run-now", description="Post the weekly report right now (7 days ending today)")
async def report_run_now(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    error = await run_weekly_report(interaction.guild)
    if error:
        return await interaction.followup.send(f"⚠️ {error}", ephemeral=True)
    cfg = load_config(interaction.guild_id)
    await interaction.followup.send(f"✅ Report posted in <#{cfg['destination']}>.", ephemeral=True)


@report_group.command(name="on", description="Enable the automatic weekly report")
async def report_on(interaction: discord.Interaction):
    update_config(interaction.guild_id, enabled=True)
    await interaction.response.send_message("✅ Automatic weekly report is on.", ephemeral=True)


@report_group.command(name="off", description="Disable the automatic weekly report")
async def report_off(interaction: discord.Interaction):
    update_config(interaction.guild_id, enabled=False)
    await interaction.response.send_message("⏸️ Automatic weekly report is off.", ephemeral=True)


# --- /access (administrators only) ---
access_group = app_commands.Group(
    name="access",
    description="Choose which roles can use this bot (administrators only)",
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),  # hidden from non-admins by default
)


def role_label(role_id: int, guild_id: int) -> str:
    return "@everyone" if role_id == guild_id else f"<@&{role_id}>"


def _role_ids(pickers, extra_text: str | None) -> list[int]:
    ids = [r.id for r in pickers if r is not None]
    if extra_text:
        ids += [int(i) for i in re.findall(r"\d{15,20}", extra_text)]
    return list(dict.fromkeys(ids))


@access_group.command(name="add", description="Allow roles to use the bot")
@app_commands.describe(role="Role to allow", role2="Another role (optional)", role3="Another role (optional)",
                       role4="Another role (optional)", role5="Another role (optional)")
async def access_add(interaction: discord.Interaction, role: discord.Role,
                     role2: Optional[discord.Role] = None, role3: Optional[discord.Role] = None,
                     role4: Optional[discord.Role] = None, role5: Optional[discord.Role] = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message(ADMIN_ONLY, ephemeral=True)

    gid = interaction.guild_id
    current = allowed_role_ids(gid)
    new = [r for r in _role_ids((role, role2, role3, role4, role5), None) if r not in current]
    if not new:
        return await interaction.response.send_message(
            "Nothing to add: those roles already have access.", ephemeral=True)

    update_config(gid, allowed_roles=current + new)
    msg = "✅ These roles can now use the bot: " + ", ".join(role_label(r, gid) for r in new)
    if gid in new:
        msg += "\n⚠️ **@everyone** means every member of the server can use the bot."
    await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=NO_MENTIONS)


@access_group.command(name="remove", description="Stop allowing roles to use the bot")
@app_commands.describe(role="Role to remove", role2="Another role (optional)", role3="Another role (optional)",
                       role_id="ID of a deleted role, or several separated by commas (optional)")
async def access_remove(interaction: discord.Interaction, role: Optional[discord.Role] = None,
                        role2: Optional[discord.Role] = None, role3: Optional[discord.Role] = None,
                        role_id: Optional[str] = None):
    if not is_admin(interaction.user):
        return await interaction.response.send_message(ADMIN_ONLY, ephemeral=True)

    to_remove = set(_role_ids((role, role2, role3), role_id))
    if not to_remove:
        return await interaction.response.send_message("⚠️ Pick at least one role to remove.", ephemeral=True)

    gid = interaction.guild_id
    current = allowed_role_ids(gid)
    removed = [r for r in current if r in to_remove]
    if not removed:
        return await interaction.response.send_message("None of those roles were allowed.", ephemeral=True)

    remaining = [r for r in current if r not in to_remove]
    update_config(gid, allowed_roles=remaining)
    msg = "🗑️ Removed: " + ", ".join(role_label(r, gid) for r in removed)
    if not remaining:
        msg += "\nNow only administrators can use the bot."
    await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=NO_MENTIONS)


@access_group.command(name="list", description="Show who can use the bot")
async def access_list(interaction: discord.Interaction):
    if not has_access(interaction):
        return await interaction.response.send_message(ACCESS_DENIED, ephemeral=True)

    gid = interaction.guild_id
    ids = allowed_role_ids(gid)
    lines = ["👑 **Administrators** (including the server owner), always"]
    for r in ids:
        missing = r != gid and interaction.guild.get_role(r) is None
        lines.append(f"• {role_label(r, gid)}" + (f" ⚠️ role not found (`{r}`), remove it with `/access remove`" if missing else ""))
    if not ids:
        lines.append("No roles allowed yet. Add one with `/access add`.")
    elif load_config(gid)["allowed_roles"] is None:
        lines.append("\n_These roles come from the default in `.env`. They become editable here once you use `/access add` or `/access remove`._")
    embed = discord.Embed(title="Who can use the bot", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=NO_MENTIONS)


@access_group.command(name="clear", description="Remove all allowed roles (only administrators keep access)")
async def access_clear(interaction: discord.Interaction):
    if not is_admin(interaction.user):
        return await interaction.response.send_message(ADMIN_ONLY, ephemeral=True)
    update_config(interaction.guild_id, allowed_roles=[])
    await interaction.response.send_message("🗑️ Now only administrators can use the bot.", ephemeral=True)


client.tree.add_command(team_group)
client.tree.add_command(report_group)
client.tree.add_command(access_group)


_commands_synced = False


@client.event
async def on_ready():
    global _commands_synced
    print(f"Bot online as {client.user}")
    if _commands_synced:  # on_ready can fire again after a reconnect
        return
    _commands_synced = True

    servers = [g for g in client.guilds if not GUILD_ID or g.id == int(GUILD_ID)]
    for guild in servers:
        try:
            await client.sync_guild(guild)
        except discord.HTTPException as e:
            print(f"Could not register commands in '{guild.name}': {e}. "
                  "Make sure the invite included the applications.commands scope.")
    print(f"Commands registered in {len(servers)} server(s).")
    if not servers:
        print("The bot isn't in any server yet. Invite it with the link from the README, then restart.")


@client.event
async def on_guild_join(guild: discord.Guild):
    """Registers the commands as soon as the bot is added to a new server."""
    if GUILD_ID and guild.id != int(GUILD_ID):
        return
    try:
        await client.sync_guild(guild)
    except discord.HTTPException as e:
        print(f"Could not register commands in '{guild.name}': {e}")


if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_TOKEN is not set.\n"
            "Create a file named .env next to this script (copy .env.example) with this line:\n"
            "    DISCORD_TOKEN=your_bot_token"
        )
    client.run(token)
