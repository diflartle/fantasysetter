# fantasysetter

A lightweight daily Fantasy Hockey lineup advisor and reminder tool. Due to Yahoo sunsetting direct public write access to their Fantasy API, this tool calculates your daily optimal lineup using official NHL game schedules and your custom player rankings, then sends a rich recommendation digest to Discord and email with a direct link to set your lines on Yahoo.

## Overview

1. **`lineup_reminder.py`** - Production script that checks today's NHL schedule, evaluates your team, calculates optimal starters/bench/off-day splits, and sends notifications.
2. **`rankings_editor.html`** - Standalone dark-mode web tool with drag-and-drop, one-click `▲`/`▼` adjustments, and direct file-saving to reorder your player priorities.
3. **`roster.json`** - Local representation of your fantasy team, player positions, and priority ranks.

## Features

- **Optimal Lineup Solver**: Uses lookahead slot reservation logic to resolve multi-position eligibility bottlenecks (`C/LW`, `RW/LW`, etc.).
- **Official NHL Schedule Integration**: Automatically queries `api-web.nhle.com` every morning for live matchups, opponent designations (`vs` / `@`), and local puck-drop start times in Eastern Time.
- **Logjam Warnings**: Explicitly breaks out `Benched (Has Game)` to warn you when active players must sit due to full position slots.
- **Rich Notifications**:
  - **Discord**: Formatted color embed listing active starters by slot, benched players with games, and off-day players, with a direct link to your Yahoo team.
  - **Email**: Plaintext daily morning digest sent via SMTP.
- **Zero Yahoo Auth Overhead**: No broken refresh tokens, expired consumer keys, or OAuth redirects.

## Installation

1. Clone the repository:

```bash
git clone [https://github.com/yourusername/fantasysetter.git](https://github.com/yourusername/fantasysetter.git)
cd fantasysetter
```

2. Install dependencies:

```bash
pip install requests python-dotenv
```

3. Set up your environment file:

```Bash
cp .env.example .env
```

## Configuration

### Environment Variables

```(.env)Ini, TOML
# Link to your Yahoo Fantasy league or team page
YAHOO_LEAGUE_URL=[https://hockey.fantasysports.yahoo.com/hockey/YOUR_LEAGUE_ID/YOUR_TEAM_ID](https://hockey.fantasysports.yahoo.com/hockey/YOUR_LEAGUE_ID/YOUR_TEAM_ID)

# Discord Webhook Notification
DISCORD_WEBHOOK_URL=[https://discord.com/api/webhooks/](https://discord.com/api/webhooks/)...

# Optional Email Notifications (SMTP)
EMAIL_FROM=youraddress@gmail.com
EMAIL_TO=youraddress@gmail.com
EMAIL_PASS=your_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
```

### Team Roster (roster.json)

Define your team in roster.json. Rank determines starting priority (lower number = starts first):

```JSON
[
  {"name": "Connor McDavid", "team": "EDM", "eligible": ["C"], "rank": 1},
  {"name": "Nathan MacKinnon", "team": "COL", "eligible": ["C"], "rank": 2},
  {"name": "Artemi Panarin", "team": "NYR", "eligible": ["LW"], "rank": 3},
  {"name": "Mikko Rantanen", "team": "COL", "eligible": ["RW"], "rank": 4},
  {"name": "Cale Makar", "team": "COL", "eligible": ["D"], "rank": 5},
  {"name": "Igor Shesterkin", "team": "NYR", "eligible": ["G"], "rank": 6}
]
```

### Re-ranking Players (rankings_editor.html)

Double-click rankings_editor.html in any web browser to open the visual manager:
Load: Click Open File and select your roster.json.
Reorder: Drag-and-drop rows or use the ▲ and ▼ buttons to adjust starter preference.
Save: Click Save File (supported browsers will overwrite roster.json directly using the File System API; others will download an updated copy).

## Usage

Run Manually

```bash
python lineup_reminder.py
```

## Automated Daily Scheduling

Run every morning before the day's first puck drop (e.g., 9:00 AM Eastern):

Linux / macOS (Cron):

```bash
crontab -e
```

Add:

```bash
0 9 \* \* \* cd /path/to/fantasysetter && /usr/bin/python3 lineup_reminder.py >> logs/fantasy.log 2>&1
```

Windows (Task Scheduler):

Create a Basic Task triggering daily at 9:00 AM running python.exe with the argument lineup_reminder.py and Start in set to your project root.

Files
|File|Purpose|
| ---- | ----|
|lineup_reminder.py|Main script: pulls NHL schedule, solves lines, dispatches alerts|
|rankings_editor.html|Browser GUI for editing rank priority and roster order|
|roster.json|Local player database with position eligibility and rankings|
|.env|Environment configuration (Discord webhook, SMTP, league URL)|
|logs/fantasy.log|Rotating execution log|

Customizing Roster Slots:

Modify the SLOTS dictionary near the top of lineup_reminder.py to match your league settings:

```python
SLOTS = {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2}
```

License: GPLv3
