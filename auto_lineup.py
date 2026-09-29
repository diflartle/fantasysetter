import os
import sys
import json
import logging
import smtplib
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv

os.makedirs("logs", exist_ok=True)
load_dotenv()

# --- CONFIGURATION ---
SLOTS = {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2}
NHL_TIMEZONE = ZoneInfo("America/New_York")
ROSTER_FILE = "roster.json"
YAHOO_LEAGUE_URL = os.getenv("YAHOO_LEAGUE_URL", "https://hockey.fantasysports.yahoo.com/")

TEAM_ABBREV_MAP = {
    "LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB", "MTL": "MON"
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("logs/fantasy.log"),
        logging.StreamHandler(sys.stdout)
    ]
)

def send_email(subject, body):
    sender = os.getenv("EMAIL_FROM")
    recipient = os.getenv("EMAIL_TO")
    password = os.getenv("EMAIL_PASS")
    server = os.getenv("SMTP_SERVER")
    port_str = os.getenv("SMTP_PORT", "587")

    if not all([sender, recipient, password, server]):
        logging.warning("⚠️ Email configuration missing. Skipping email.")
        return

    msg = MIMEMultipart()
    msg["From"] = formataddr(("Fantasy Reminder", sender))
    msg["To"] = formataddr(("Manager", recipient))
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(server, int(port_str)) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
        logging.info("📧 Email sent successfully.")
    except Exception as e:
        logging.error(f"⚠️ Failed to send email: {e}")

def get_nhl_schedule(date_str):
    """Fetches today's matchups and start times from NHL Web API."""
    api_url = f"https://api-web.nhle.com/v1/schedule/{date_str}"
    try:
        res = requests.get(api_url, timeout=10).json()
        games = res.get("gameWeek", [{}])[0].get("games", [])
        
        team_schedule = {}
        for game in games:
            home = TEAM_ABBREV_MAP.get(game["homeTeam"]["abbrev"], game["homeTeam"]["abbrev"])
            away = TEAM_ABBREV_MAP.get(game["awayTeam"]["abbrev"], game["awayTeam"]["abbrev"])
            start_utc = game.get("startTimeUTC")
            
            # Convert UTC start time to ET
            time_et = ""
            if start_utc:
                dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(NHL_TIMEZONE)
                time_et = dt.strftime("%I:%M %p ET")

            team_schedule[home] = {"opponent": f"vs {away}", "time": time_et}
            team_schedule[away] = {"opponent": f"@ {home}", "time": time_et}

        return team_schedule
    except Exception as e:
        logging.error(f"⚠️ Error fetching NHL schedule: {e}")
        return {}

def choose_lineup(players, schedule, slots):
    # Separate goalies and skaters
    goalies = [p for p in players if p.get("eligible") == ["G"]]
    skaters = [p for p in players if "G" not in p.get("eligible", [])]

    # Adjust player rank by game presence
    for p in skaters + goalies:
        team = TEAM_ABBREV_MAP.get(p.get("team"), p.get("team"))
        if team in schedule:
            p["game_info"] = f"({schedule[team]['opponent']} - {schedule[team]['time']})"
            p["effective_rank"] = p.get("rank", 999)
        else:
            p["game_info"] = "(No Game)"
            p["effective_rank"] = p.get("rank", 999) + 10000

    assigned = defaultdict(list)
    used = set()
    skater_positions = ["C", "LW", "RW", "D"]

    def get_positions(player):
        return [pos for pos in player.get("eligible", []) if pos in skater_positions]

    def get_available_positions(player):
        return [pos for pos in get_positions(player) if len(assigned[pos]) < slots[pos]]

    skaters.sort(key=lambda x: x["effective_rank"])

    for player in skaters:
        pid = player["name"]
        available = get_available_positions(player)
        if not available:
            continue

        if len(available) == 1:
            chosen = available[0]
        else:
            # Lookahead slot reservation
            slot_scores = {}
            for pos in available:
                best_teammate = 99999
                for tm in skaters:
                    if tm["name"] == pid or tm["name"] in used:
                        continue
                    if pos in get_positions(tm):
                        best_teammate = tm["effective_rank"]
                        break
                slot_scores[pos] = best_teammate
            chosen = max(slot_scores, key=slot_scores.get)

        assigned[chosen].append(player)
        used.add(pid)

    # Goalies
    goalies.sort(key=lambda x: x["effective_rank"])
    for g in goalies[:slots["G"]]:
        assigned["G"].append(g)
        used.add(g["name"])

    # Bench and Off-day sorting
    bench = [p for p in players if p["name"] not in used]
    bench_playing = [p for p in bench if p["effective_rank"] < 10000]
    bench_idle = [p for p in bench if p["effective_rank"] >= 10000]

    return assigned, bench_playing, bench_idle

def send_discord_reminder(assigned, bench_playing, bench_idle, today_str):
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return

    fields = []
    for pos, plist in assigned.items():
        val = "\n".join(f"• **{p['name']}** {p['game_info']}" for p in plist) if plist else "—"
        fields.append({"name": f"Starting {pos}", "value": val, "inline": False})

    if bench_playing:
        b_val = "\n".join(f"• ⚠️ **{p['name']}** {p['game_info']}" for p in bench_playing)
        fields.append({"name": "Benched (Has Game)", "value": b_val, "inline": False})

    if bench_idle:
        off_val = ", ".join(p['name'] for p in bench_idle)
        fields.append({"name": "Off Today", "value": off_val, "inline": False})

    embed = {
        "title": f"🏒 Daily Lineup Recommendation — {today_str}",
        "url": YAHOO_LEAGUE_URL,
        "color": 0x3498DB,
        "description": f"Log into Yahoo to lock in your lines.\n👉 [Open Yahoo Fantasy]({YAHOO_LEAGUE_URL})",
        "fields": fields,
        "footer": {"text": "Daily NHL Lineup Advisor"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    try:
        requests.post(webhook_url, json={"embeds": [embed]}, timeout=10)
        logging.info("📨 Discord reminder sent.")
    except Exception as e:
        logging.error(f"⚠️ Discord webhook failed: {e}")

if __name__ == "__main__":
    today_str = datetime.now(NHL_TIMEZONE).strftime("%Y-%m-%d")
    logging.info(f"=== Generating Daily Lineup Reminder for {today_str} ===")

    if not os.path.exists(ROSTER_FILE):
        logging.error(f"Missing {ROSTER_FILE}. Please initialize your team file.")
        sys.exit(1)

    with open(ROSTER_FILE, "r") as f:
        roster = json.load(f)

    schedule = get_nhl_schedule(today_str)
    assigned, bench_playing, bench_idle = choose_lineup(roster, schedule, SLOTS)

    # Dispatch alerts
    send_discord_reminder(assigned, bench_playing, bench_idle, today_str)

    # Optional Email dispatch
    email_body_lines = [f"Recommended Lineup for {today_str}:\n"]
    for pos, plist in assigned.items():
        email_body_lines.append(f"{pos}:")
        for p in plist:
            email_body_lines.append(f"  - {p['name']} {p['game_info']}")
    if bench_playing:
        email_body_lines.append("\nBenched Players with Games (Must Sit):")
        for p in bench_playing:
            email_body_lines.append(f"  - {p['name']} {p['game_info']}")
    email_body_lines.append(f"\nSet your lineup here: {YAHOO_LEAGUE_URL}")

    send_email(f"Fantasy Lineup Alert - {today_str}", "\n".join(email_body_lines))