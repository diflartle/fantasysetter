# fantasysetter

Automated Yahoo Fantasy Hockey lineup optimizer that sets your daily roster based on custom player rankings and NHL game schedules.

## Overview

This project provides two complementary tools for managing your Yahoo Fantasy Hockey team:

1. **`auto_lineup.py`** - Automated command-line script for daily lineup optimization (production use)
2. **`app.py`** - Interactive Flask web UI for manual lineup management and initial OAuth setup

## Features

- **Smart Lineup Optimization**: Uses a greedy algorithm to assign your best-ranked players to roster positions, considering positional eligibility and constraints
- **Schedule-Aware**: Automatically benches players whose teams aren't playing today (pulled from NHL API)
- **Custom Rankings**: Define your own player rankings in `rankings.json` to override default rankings
- **Multi-Position Support**: Intelligently handles players eligible for multiple positions (C/LW, RW/LW, etc.)
- **Discord Notifications**: Sends formatted embed messages when lineups are updated
- **Email Alerts**: Notifies you of errors via email
- **Change Detection**: Only submits lineup changes when necessary, avoiding unnecessary API calls

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/fantasysetter.git
cd fantasysetter
```

2. Install dependencies:
```bash
pip install flask requests python-dotenv
```

3. Copy `.env.example` to `.env` and configure your settings:
```bash
cp .env.example .env
```

## Configuration

### Yahoo API Setup

1. Create a Yahoo app at https://developer.yahoo.com/apps/
   - Set redirect URI to `https://localhost:5000/callback` (or your production URL)
   - Request read/write permissions for Fantasy Sports

2. Add credentials to `.env`:
```
YAHOO_CLIENT_ID=your_client_id
YAHOO_CLIENT_SECRET=your_client_secret
YAHOO_TEAM_KEY=nhl.l.LEAGUE_ID.t.TEAM_ID
```

### Optional: Discord Webhook

Add to `.env` for lineup update notifications:
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

### Optional: Email Notifications

Configure SMTP settings in `.env` for error notifications:
```
EMAIL_FROM=youraddress@gmail.com
EMAIL_TO=youraddress@gmail.com
EMAIL_PASS=your_app_password
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
```

### Custom Player Rankings

Create `rankings.json` with your player rankings (lower = better):
```json
{
  "Connor McDavid": 1,
  "Auston Matthews": 2,
  "Nathan MacKinnon": 3
}
```

Players not in `rankings.json` will be ranked 9999 (benched by default).

## Usage

### Initial OAuth Setup (First Time Only)

Use the Flask web app to authorize with Yahoo and obtain tokens:

```bash
python app.py
```

1. Visit `https://localhost:5000`
2. Click "Authorize with Yahoo"
3. Grant permissions
4. Tokens will be saved to `yahoo_tokens.json`

### Automated Daily Lineup (Recommended)

Run the command-line script:

```bash
python auto_lineup.py
```

This will:
1. Refresh OAuth tokens
2. Fetch your current roster
3. Check NHL schedule for today's games
4. Compute optimal lineup (benching players without games)
5. Submit changes to Yahoo (if lineup changed)
6. Send Discord notification (if configured)

### Schedule with Cron/Task Scheduler

Run daily at 10 AM (before games start):

**Linux/Mac (crontab):**
```
0 10 * * * cd /path/to/fantasysetter && python auto_lineup.py >> logs/lineup.log 2>&1
```

**Windows (Task Scheduler):**
Create a scheduled task to run `auto_lineup.py` daily at 10:00 AM.

## Files

| File | Purpose |
|------|---------|
| `auto_lineup.py` | Main automated script for daily lineup management |
| `app.py` | Flask web UI to deal with authorizing your Yahoo account permissions. |
| `rankings.json` | Your custom player rankings (not tracked in git) |
| `yahoo_tokens.json` | OAuth tokens (auto-refreshed, not tracked in git) |
| `.env.example` | Template for environment variables |

## Roster Slots

Default configuration (edit `SLOTS` in `auto_lineup.py` to match your league):
```python
SLOTS = {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2}
```

## How It Works

### Lineup Algorithm

1. **Separate by player type**: Goalies vs. skaters
2. **Schedule filtering**: Inflate rank (+9999) for players whose teams aren't playing today
3. **Multi-pass assignment**:
   - Pass 1: Assign single-position-eligible players first
   - Pass 2: Fill scarcest positions with best remaining players
4. **Goalie assignment**: Simple rank-based selection for goalie slots
5. **Benchmarking**: Remaining players assigned to bench

### Position Eligibility Mapping

The script handles Yahoo's position eligibility system:
- Players can be eligible for multiple positions (e.g., `["C", "LW"]`)
- Algorithm prioritizes filling hard-to-fill positions first
- Prevents wasting multi-eligible players on easily-filled slots

## Security Notes

- Never commit `yahoo_tokens.json`, `rankings.json`, or `.env` to version control
- Use app-specific passwords for email (not your main account password)

## Troubleshooting

**"No valid tokens" error:**
- Run `app.py` first to complete OAuth flow and generate `yahoo_tokens.json`

**Lineup not changing:**
- Check `rankings.json` exists and contains your players
- Verify `YAHOO_TEAM_KEY` in `.env` matches your actual team
- Players not in `rankings.json` will be benched (rank 9999)

**Schedule API errors:**
- NHL API occasionally has downtime - script will continue with rank-only optimization

**Discord webhook not working:**
- Verify `DISCORD_WEBHOOK_URL` is set correctly in `.env`
- Check webhook permissions in Discord server settings

## License

GPLv3

## Contributing

Pull requests welcome! Please ensure changes work with both `app.py` and `auto_lineup.py`.
