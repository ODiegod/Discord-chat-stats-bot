# Support Stats Bot

A Discord bot that counts how many **different people** team members helped (via replies or mentions), broken down by day and by channel. It reports on **one person or the whole team at once**, using a saved team list you edit from chat, and it can **post a weekly report automatically**.

## Commands

| Command | What it does |
|---|---|
| `/support-stats` | Opens a form and returns the stats on demand (only visible to you) |
| `/team add` / `remove` / `list` / `clear` | Manage the saved team list |
| `/report channels add` / `remove` / `list` / `clear` | Manage the channels counted by the weekly report |
| `/report destination` | Set the channel where the weekly report is posted |
| `/report schedule` | Change the day and time of the automatic report |
| `/report window` | Set how many hours a chat can continue before it counts as a new conversation |
| `/report status` | Show the weekly report settings and the next run |
| `/report run-now` | Post the weekly report right now (good for testing) |
| `/report on` / `off` | Enable or disable the automatic weekly report |
| `/access add` / `remove` / `list` / `clear` | Choose which roles can use the bot (administrators only) |

**Only server administrators and the roles you allow can use these commands.** Everyone else is refused. See *Who can use the bot* below.

## Automatic weekly report

By default, every **Thursday at 18:00 US Eastern time (`America/New_York`)** the bot posts, for **everyone on the saved team list**, the stats from the **previous Friday through that Thursday** (7 days). It is posted publicly in the destination channel. The day and time are not fixed: change them any time with `/report schedule`.

Set it up once:

1. `/team add` the people to include.
2. `/report channels add` the channels to count.
3. `/report destination` the channel where the report will be posted.
4. `/report status` to confirm everything and see the next run time.
5. Optional: `/report run-now` to see how the report looks before Thursday.

Details:

- **Period:** the 7 days ending on the report day. On a Thursday that is Friday through Thursday. If you move the report to another day, the period moves with it (for example, Monday covers the previous Tuesday through Monday). `/report run-now` uses the 7 days ending today.
- **Missed runs:** the bot checks every minute. If it was offline at the scheduled time but comes back **the same day**, it posts the report right away. Each server gets at most one automatic report per day.
- **Errors:** if something is wrong (empty team, no channels, deleted channel, missing permissions), the bot posts a short warning **once** in the destination channel instead of failing silently. After fixing the problem, use `/report run-now` (the automatic report doesn't retry on the same day).
- The bot has to be **running** at that time. See *Keeping it online* below.

## What the report looks like

With several users you get a **team summary**, then one **daily table per person**:

```
User             support-en   support-pt  Total
-----------------------------------------------
Alice                     4            1      5
Bob                       2            0      2
Carlos                    0            0      0
-----------------------------------------------
Team total                6            1      7
```

The team summary uses each person's **Total**. Each person's own table shows the days, then two summary rows:

```
Day              support-en   support-pt  Total
-----------------------------------------------
05/10 Mon                 2            0      2
06/10 Tue                 1            1      2
07/10 Wed                 1            0      1
...
-----------------------------------------------
Total                     4            1      5
Unique people             3            1      4
```

- **Total** is the sum of the daily counts.
- **Unique people** counts each person once in the whole period.

In this example, Alice helped 5 people over the days, but one of them was helped on two different days, so there are 4 different people.

If a table has too many channels and days to fit in a Discord message, the bot says so. Use a shorter day range or fewer channels.

## How counting works

- A person counts **once per day per channel** for each team member, no matter how many times they were answered.
- A chat that **continues across midnight** doesn't count twice: if the same team member answers the same person in the same channel within the *conversation window* (default 3 hours) of their previous message, it's the same conversation. See *Conversations that cross midnight* below.
- A message counts as "helping" someone if the author **replied to** that person or **@mentioned** them.
- Bots and the author themselves are excluded.
- **Total** is the **sum of the daily counts**. If a person is helped on two different days, they count on both days and twice in the Total (unless it's the same conversation, see the conversation window above).
- **Unique people** counts each person **once** in the whole period, no matter how many days they were helped.
- Within a single day, the Total column doesn't repeat someone who appears in several channels that day.
- **Team total** is the plain sum of each person's Total. If the same person is helped by two team members, they count for both, and twice in the team total.
- Example: a team member helps person X on Tuesday, Y on Wednesday and X again on Thursday. The days show 1, 1 and 1, the **Total is 3** and **Unique people is 2**.
- Days follow the configured timezone (default `America/New_York`, i.e. US Eastern time), not UTC. A day runs from 00:00 to 23:59 in that timezone, so messages near midnight can land on a different day than in other timezones.
- Limitation: if someone is helped by writing their name with no reply or mention, it can't be detected.

## Requirements

- Python 3.10 or newer
- A Discord server where you (or an admin) can add a bot

## Setup

You only do this once. After that, starting the bot is a single command.

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

This installs `discord.py` and `tzdata` (needed on Windows for timezone support).

### 2. Create the bot

1. Go to <https://discord.com/developers/applications> and click **New Application**.
2. Open the **Bot** tab and click **Reset Token**. Copy the token. It is only shown once, so you'll paste it into a file in step 4.
3. In the same tab, under **Privileged Gateway Intents**, enable:
   - **Message Content Intent**
   - **Server Members Intent**
4. Save the changes.

### 3. Invite the bot to your server

1. Go to **OAuth2 > URL Generator**.
2. Under **Scopes**, select `bot` and `applications.commands`.
3. Under **Bot Permissions**, select **View Channels**, **Read Message History**, **Send Messages** and **Embed Links**.
4. Open the generated URL and pick your server. You need the *Manage Server* permission, or ask an admin to do it.

The bot needs **Send Messages** and **Embed Links** in the destination channel, and **View Channel** plus **Read Message History** in every counted channel. Check channel-level permission overrides too, not just the server-wide ones.

### 4. Save your token once (`.env` file)

1. In the same folder as `support_stats_bot.py`, copy `.env.example` to a new file named `.env`:

   ```bash
   cp .env.example .env          # macOS / Linux
   copy .env.example .env        # Windows (Command Prompt)
   ```

2. Open `.env` and replace `paste_your_bot_token_here` with your bot token.

That's all. You don't need to type the token or the server ID again, and there's nothing to set in the terminal each time. The bot reads `.env` on every start, no matter which folder you launch it from.

> The file must be named exactly `.env` (it starts with a dot). On Windows, make sure Notepad didn't save it as `.env.txt`.

### 5. Run

```bash
python support_stats_bot.py
```

You should see:

```
Bot online as YourBotName
Commands registered in 1 server(s).
```

The bot registers its slash commands in **every server it is in**, and again automatically whenever it is added to a new one, so you don't need a server ID. Type `/` in your server to check that the commands showed up.

### 6. Allow your team

Right after the first start, **only server administrators can use the bot**. An administrator should allow the role your team uses, for example:

```
/access add role:@Moderators
```

From then on, members with that role can use every command. You can do this at any time and as often as you like. See *Who can use the bot* for details.

## Settings

Everything below goes in `.env` and is **optional**, except `DISCORD_TOKEN`. `.env.example` lists all of them, commented out. Real environment variables, if you set any (for example in a hosting dashboard), take priority over `.env`.

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | The bot token from step 2 |
| `GUILD_ID` | No | Register the commands only in this server. By default the bot registers them in every server it is in, so you normally leave this out |
| `TIMEZONE` | No | Any [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones). Default: `America/New_York` (US Eastern: EST in winter, EDT in summer). For a fixed UTC-5 all year with no daylight saving, use `EST`. This setting applies to the whole bot (reports, day boundaries and the schedule) |
| `REPORT_WEEKDAY` | No | **Default** day of the automatic report for servers that haven't set one. `0` = Monday ... `6` = Sunday. Default: `3` (Thursday). Change it per server with `/report schedule` |
| `REPORT_HOUR` | No | **Default** hour of the automatic report (0-23, in `TIMEZONE`). Default: `18`. Change it per server with `/report schedule` |
| `CONVERSATION_WINDOW_HOURS` | No | **Default** conversation window in hours for servers that haven't set one. Default: `3`. Change it per server with `/report window` |
| `ALLOWED_ROLE_IDS` | No | **Default** roles allowed to use the bot, besides administrators, as comma-separated role IDs. Servers that haven't used `/access` follow this list. Without it, only administrators can use the bot. The old name `TEAM_EDITOR_ROLE_ID` still works |
| `TEAM_FILE` | No | Where the saved team list is stored. Default: `team.json` in the same folder as the script |
| `CONFIG_FILE` | No | Where the weekly report settings are stored. Default: `config.json` in the same folder as the script |

## Files

| File | What it is |
|---|---|
| `support_stats_bot.py` | The bot |
| `requirements.txt` | Dependencies (`pip install -r requirements.txt`) |
| `.env.example` | Template for your settings. Copy it to `.env` |
| `.env` | **Your** settings and bot token. You create it, and you never share it |
| `.gitignore` | Keeps `.env` and your saved data out of git |
| `team.json`, `config.json` | Created automatically the first time you use `/team` or `/report`. They hold the team list and report settings |

## Who can use the bot

The bot is **closed by default**. Every command (`/support-stats`, `/team`, `/report` and `/access` itself) works only for:

- **Server administrators**: members with the *Administrator* permission, which includes the server owner. They always have access.
- **Members with an allowed role**: the roles you add with `/access add`.

Everyone else gets a private message saying they don't have access. This includes the read-only commands, like `/team list` and `/report status`.

| Command | Who can run it |
|---|---|
| `/access add` / `remove` / `clear` | Administrators only |
| `/access list` | Administrators and allowed roles |
| `/support-stats`, `/team ...`, `/report ...` | Administrators and allowed roles |

```
/access add role:@Moderators role2:@Support       (up to 5 roles at once)
/access list
/access remove role:@Support
/access remove role_id:123456789012345678         (for a role that was deleted)
/access clear                                      (only administrators keep access)
```

Notes:

- **Allowed roles can't change who has access.** Only administrators can use `/access add`, `remove` and `clear`, so a moderator can't grant access to anyone else.
- **Per server:** each server keeps its own list, saved in `config.json`. Servers that haven't used `/access` follow the default from `ALLOWED_ROLE_IDS` in `.env`, if you set it. After the first `/access add` or `/access remove`, the server's own list takes over. `/access clear` leaves the list empty (administrators only) and ignores the `.env` default.
- **Opening it to everyone:** adding the `@everyone` role allows all members. The bot warns you when you do this. Remove it with `/access remove role:@everyone`.
- **Visibility in Discord:** `/access` is hidden from non-administrators by default. The other commands are visible to everyone in the `/` menu, but refuse members without access. To also hide them, use *Server Settings > Integrations* in Discord, but keep in mind that this changes who sees them, not who can run them: the bot checks access on every use.
- **Automatic report:** the weekly report is posted by the bot itself, so it doesn't depend on who has access. People who can read the destination channel can see the report there.
- **Upgrading from an older version:** until now every member could use the commands (unless `TEAM_EDITOR_ROLE_ID` was set). After updating, members need an allowed role. Run `/access add` once, or set `ALLOWED_ROLE_IDS` in `.env`.

## Using `/support-stats` (on demand)

Type `/support-stats` and fill in the form:

| Field | What to enter |
|---|---|
| **Users** | Leave **blank** to use the saved team list. Or type a comma-separated list of names, @mentions or IDs to override it just for this report |
| **Day range** | `5-8`, a single day like `12`, or `29-3` to cross a month boundary |
| **Month of the first day** (optional) | `9`, `September` or `9/2026`. Blank = current month |
| **Channel IDs** | One or more channel IDs separated by commas (up to 10) |

### Date examples (assuming today is Oct 8, 2026)

| Days | Month | Period counted |
|---|---|---|
| `5-8` | blank | Oct 5 to Oct 8 |
| `29-3` | blank | Sep 29 to Oct 3 |
| `29-3` | `September` | Sep 29 to Oct 3 |
| `30-2` | `12/2025` | Dec 30, 2025 to Jan 2, 2026 |
| `5-8` | `Sep` | Sep 5 to Sep 8 |

The maximum range is 31 days.

## Conversations that cross midnight

By default, a person helped on two different days counts once on each day. For a team member who works across midnight, that would count one long chat twice. To avoid that, the bot uses a **conversation window** (default **3 hours**):

- If the same team member replies to or mentions the same person **in the same channel** within the window of their previous message to that person, it's the **same conversation** and isn't counted again.
- Every new reply refreshes the window, so a long back-and-forth stays one conversation, even if it runs past midnight for hours.
- If the person comes back **later than the window** (for example, the next morning), that's a new conversation and counts normally on that day.
- It is checked per team member: if two different team members answer the same person, each one counts it for themselves.
- It also works at the edges of a report. If a chat starts just before the first day of the period, the bot looks back by the window length so it isn't counted again on day one.

Example with a 3-hour window: Alice answers a customer at 23:50 on Monday and again at 00:10 on Tuesday. The customer counts for Alice on **Monday only**. If Alice answers the same customer again at 09:00 on Tuesday, that's a new conversation and counts on Tuesday too.

```
/report window                 (shows the current value)
/report window hours:4
/report window hours:0         (turns it off: each day counts on its own)
```

The value goes from 0 to 24 hours (decimals like `1.5` are fine) and applies to both `/support-stats` and the weekly report. The reports mention it in the footer, and `/report status` shows it. A message with no reply or mention isn't an interaction, so it doesn't extend the window.

## Managing the report channels

```
/report channels add channel1:#support-en channel2:#support-pt
/report channels list
/report channels remove channel1:#support-pt
/report channels clear
```

Works like the team list: `add` and `remove` accept up to 5 and 3 channels from the picker, plus an option for typing IDs (useful for a channel that was deleted and can no longer be picked). The list is saved and the limit is 10 channels. People can only add channels they can access themselves.

## Changing the schedule

```
/report schedule day:Monday time:9:30
/report schedule time:8pm
/report schedule
```

Set the day, the time, or both (the one you leave out stays as it was). Time accepts 24h (`20:00`, `20h30`) or am/pm (`8pm`, `8:30 pm`) and always uses the bot's timezone. Run `/report schedule` with no options to see the current setting, or `/report status` to also see the next run. If you set a time that already passed today, the report won't fire immediately; use `/report run-now` if you want it right away.

**São Paulo equivalent:** the default 18:00 US Eastern is 19:00 in São Paulo while the US is on daylight saving time (EDT, roughly March to November) and 20:00 in São Paulo the rest of the year (EST). To keep 19:00 São Paulo during EST, use `/report schedule time:5pm`.

## Managing the team list

```
/team add user:@Alice user2:@Bob user3:@Carlos user4:@Dana
```

Use `/team list` to check it, `/team remove` to take someone out, and `/team clear` to start over. The list is saved to disk, so it survives restarts. The limit is 10 people. To remove someone who already left the server, use the `user_id` option of `/team remove`.

## Security notes

- Only server administrators and allowed roles can use the bot at all (see *Who can use the bot*). Allowed roles can't change who has access.
- The person running `/support-stats` can only query channels **they** have access to.
- The weekly report is posted publicly in the destination channel, so pick a channel whose audience should see these numbers.
- Never share or commit your bot token. It lives in `.env`, which the included `.gitignore` already excludes. If the token leaks, click **Reset Token** in the Developer Portal and paste the new one into `.env`.
- On-demand reports are ephemeral (only visible to whoever ran the command).
- Keep `team.json` and `config.json` out of public repositories, since they contain user and channel IDs.

## Troubleshooting

| Problem | Fix |
|---|---|
| `DISCORD_TOKEN is not set` | Create the `.env` file next to `support_stats_bot.py` with your token (step 4). Check it isn't named `.env.txt` |
| `LoginFailure` / improper token | Reset the token in the Developer Portal and paste the new one into `.env`, with no spaces |
| `PrivilegedIntentsRequired` error | Enable and save both intents from step 2 |
| Commands don't appear | Look at the console. It should say `Commands registered in N server(s)`. If it says it could not register them, re-invite the bot with the `applications.commands` scope (step 3). If `N` is 0, the bot isn't in any server yet. If you set `GUILD_ID` in `.env`, check that it is the right server |
| "The bot doesn't have access to #channel" | Give the bot **View Channel** and **Read Message History** on that channel |
| "The bot is missing these permissions in #channel" | Give the bot the listed permissions in the destination channel |
| The weekly report didn't post | Run `/report status` (is it **on**, with channels and destination set, and is the schedule what you expect?). Make sure the bot was running at the report time. Use `/report run-now` to see the exact error |
| "I couldn't find any of those users" | Try user IDs or @mentions instead of names |
| "You don't have access to this bot" | Your role isn't allowed yet. Ask a server administrator to run `/access add` for it |
| "Only server administrators can change who has access" | `/access add`, `remove` and `clear` are for administrators only |
| Members who used the bot before can't use it anymore | The bot is closed by default now. An administrator runs `/access add role:@YourRole` once |
| Timezone error on Windows | Run `pip install tzdata` |

## Keeping it online

The bot only responds, and only sends the weekly report, **while the script is running**. To keep it available 24/7, host it on a small VPS or a service like Railway or Fly.io. Most hosts let you set the variables from the settings table in a dashboard instead of using a `.env` file (they take priority over it). If the host has a temporary filesystem, set `TEAM_FILE` and `CONFIG_FILE` to a persistent volume path, otherwise the team list and report settings can be lost on redeploy.
