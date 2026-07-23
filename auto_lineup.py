import os, requests, base64, json, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo # Standard in Python 3.9+
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from dotenv import load_dotenv
import logging
import sys

# Ensure logs directory exists before configuring logging
os.makedirs("logs", exist_ok=True)

load_dotenv()

# --- CONFIGURATION ---
CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
TOKEN_FILE = "yahoo_tokens.json"
REDIRECT_URI = "http://localhost:5000/callback"
FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
SLOTS = {"C": 2, "LW": 2, "RW": 2, "D": 4, "G": 2}
NHL_TIMEZONE = ZoneInfo("America/New_York") # Force NHL Time
TEAM_ABBREV_MAP = {
    "LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB", "MTL": "MON"
}

# --- LOGGING SETUP ---
# Log to File AND Console (so systemd catches it too)
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
    sender = os.environ.get("EMAIL_FROM")
    recipient = os.environ.get("EMAIL_TO")
    password = os.environ.get("EMAIL_PASS")
    server = os.environ.get("SMTP_SERVER")
    port_str = os.environ.get("SMTP_PORT", "587")

    if not all([sender, recipient, password, server]):
        logging.warning("⚠️ Email configuration missing. Skipping email.")
        return

    msg = MIMEMultipart()
    msg["From"] = formataddr(("Fantasy Setter", sender))
    msg["To"] = formataddr(("No Postseason Losses", recipient))
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

def send_discord_message(subject, body):
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logging.error("⚠️ No DISCORD_WEBHOOK_URL found in .env")
        return

    content = f"**{subject}**\n```{body}```"
    try:
        r = requests.post(webhook_url, json={"content": content})
        if r.status_code == 204:
            logging.info("📨 Discord message sent.")
        else:
            logging.error(f"⚠️ Discord webhook failed ({r.status_code}): {r.text}")
    except Exception as e:
        logging.error(f"⚠️ Discord webhook error: {e}")

def load_tokens():
    with open(TOKEN_FILE) as f: return json.load(f)

def save_tokens(t):
    # Atomic write to prevent corruption during crash
    temp_file = f"{TOKEN_FILE}.tmp"
    with open(temp_file, "w") as f:
        json.dump(t, f)
    os.replace(temp_file, TOKEN_FILE)

def basic_auth():
    return base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

def refresh():
    tokens = load_tokens()
    headers = {
        "Authorization": f"Basic {basic_auth()}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "redirect_uri": REDIRECT_URI
    }
    r = requests.post("https://api.login.yahoo.com/oauth2/get_token", headers=headers, data=data)
    r.raise_for_status()
    new = r.json()
    if "refresh_token" not in new:
        new["refresh_token"] = tokens["refresh_token"]
    save_tokens(new)
    return new["access_token"]

def api_get(path, token):
    r = requests.get(f"{FANTASY_BASE}{path}", headers={"Authorization":f"Bearer {token}"})
    r.raise_for_status()
    return r.text

def find_text(e, path, namespace=None):
    n = e.find(path, namespace) if namespace else e.find(path)
    return n.text if n is not None else None

def parse_roster(xml):
    root = ET.fromstring(xml)
    players = []
    namespace = {'ns': 'http://fantasysports.yahooapis.com/fantasy/v2/base.rng'}

    # Load custom rankings
    rankings = {}
    if os.path.exists("rankings.json"):
        try:
            with open("rankings.json") as f:
                rankings = json.load(f)
        except json.JSONDecodeError:
            logging.error("⚠️ rankings.json is invalid JSON. Ignoring.")

    for p in root.findall(".//ns:player", namespace):
        pk = find_text(p, "ns:player_key", namespace)
        name = find_text(p, "ns:name/ns:full", namespace) or find_text(p, "ns:name", namespace)
        elig = [x.text for x in p.findall(".//ns:eligible_positions/ns:position", namespace)]
        sel = find_text(p, "ns:selected_position/ns:position", namespace)
        team_abbr = find_text(p, "ns:editorial_team_abbr", namespace)
        status = find_text(p, "ns:status", namespace)

        # Check Goalie Starting Status (1 = Starting, None/0 = Not confirmed)
        is_starting = find_text(p, "ns:starting_status/ns:is_starting", namespace)

        # Use custom ranking if available, otherwise use default
        rank = rankings.get(name, 9999)

        players.append({
            "player_key": pk,
            "name": name,
            "eligible": elig,
            "sel": sel,
            "team_abbr": team_abbr,
            "rank": rank,
            "status": status,
            "is_starting": is_starting == "1"
        })
    return players

def get_active_teams(date_str):
    """Fetches the abbreviations of NHL teams playing today."""
    api_url = f"https://api-web.nhle.com/v1/schedule/{date_str}"
    try:
        games_data = requests.get(api_url, timeout=10).json()
        if not games_data.get("gameWeek") or not games_data["gameWeek"][0].get("games"):
            logging.info(f"⚠️ No games found for {date_str}")
            return []
        teams = [
            team_abbr for game in games_data["gameWeek"][0]["games"]
            for team_abbr in (game["awayTeam"]["abbrev"], game["homeTeam"]["abbrev"])
        ]
        return teams
    except Exception as e:
        logging.error(f"⚠️ Error fetching NHL schedule: {e}")
        return []

def adjust_rankings_with_schedule(players):
    today_str = datetime.now(NHL_TIMEZONE).strftime("%Y-%m-%d")

    def normalize_team_abbrev(abbrev):
        return TEAM_ABBREV_MAP.get(abbrev, abbrev)

    active_teams = [normalize_team_abbrev(t) for t in get_active_teams(today_str)]
    logging.info(f"Active Teams for {today_str}: {', '.join(active_teams)}")

    for p in players:
        # 1. CHECK INJURY STATUS
        if p.get("status") in ["O", "IR", "IR+"]:
            p["rank"] += 20000
            continue

        # 2. CHECK SCHEDULE
        team = p.get("team_abbr")
        if normalize_team_abbrev(team) not in active_teams:
            p["rank"] += 10000

def choose_lineup(players, slots):
    logging.info("Calculating optimal lineup...")

    goalies = [p for p in players if p["eligible"] == ["G"]]
    skaters = [p for p in players if "G" not in p["eligible"]]

    assigned = defaultdict(list)
    used = set()

    skater_positions = ["C", "LW", "RW", "D"]

    def get_eligible_positions(player):
        return [pos for pos in player["eligible"] if pos in skater_positions]

    def get_available_positions(player):
        eligible = get_eligible_positions(player)
        return [pos for pos in eligible if len(assigned[pos]) < slots[pos]]

    # Sort skaters by rank (Playing > No Game > Injured)
    skaters.sort(key=lambda x: x["rank"])

    for player in skaters:
        if player["player_key"] in used: continue

        available = get_available_positions(player)

        if not available:
            logging.info(f"  BENCHED: {player['name']} (Rank {player['rank']})")
            continue

        if len(available) == 1:
            pos = available[0]
            assigned[pos].append(player)
            used.add(player["player_key"])
            logging.info(f"  ASSIGNED: {player['name']} -> {pos} (Forced)")
        else:
            # SMART LOOKAHEAD LOGIC
            best_alternative_ranks = {}
            for pos in available:
                best_rank_for_pos = 99999
                for teammate in skaters:
                    if teammate["player_key"] == player["player_key"]: continue
                    if teammate["player_key"] in used: continue
                    if pos in get_eligible_positions(teammate):
                        best_rank_for_pos = teammate["rank"]
                        break
                best_alternative_ranks[pos] = best_rank_for_pos

            chosen_pos = max(best_alternative_ranks, key=best_alternative_ranks.get)
            assigned[chosen_pos].append(player)
            used.add(player["player_key"])
            logging.info(f"  DECISION: {player['name']} -> {chosen_pos} (Saved alt for rank {best_alternative_ranks})")

    # --- UPDATED GOALIE LOGIC ---
    # Prioritize: 1. Confirmed Starters, 2. Rank, 3. Non-starters
    # We give a massive bonus to confirmed starters so they jump to the top
    for g in goalies:
        if g["is_starting"]:
            g["sort_score"] = g["rank"] - 5000 # Bonus
        else:
            g["sort_score"] = g["rank"]

    goalies.sort(key=lambda x: x["sort_score"])

    for g in goalies[:slots["G"]]:
        assigned["G"].append(g)
        used.add(g["player_key"])
        start_tag = " (CONFIRMED)" if g["is_starting"] else ""
        logging.info(f"  GOALIE: {g['name']}{start_tag}")

    bench = [p for p in players if p["player_key"] not in used]
    bench.sort(key=lambda x: x["rank"])

    return assigned, bench

def build_payload(assigned, bench=None, ir_players=None, date_str=None):
    if date_str is None:
        date_str = datetime.now(NHL_TIMEZONE).strftime("%Y-%m-%d")

    parts = [
        '<?xml version="1.0"?><fantasy_content><roster>',
        '<coverage_type>date</coverage_type>',
        f'<date>{date_str}</date>',
        '<players>'
    ]

    # Helper to add player
    def add_p(plist, pos_override=None):
        if not plist: return
        for p in plist:
            pos = pos_override if pos_override else p['sel'] # Default to current if unknown, but usually passed
            # Actually, the dict key is the position for assigned
            parts.append(f"<player><player_key>{p['player_key']}</player_key><position>{pos}</position></player>")

    for pos, plist in assigned.items():
        for p in plist:
            parts.append(f"<player><player_key>{p['player_key']}</player_key><position>{pos}</position></player>")

    if bench:
        for p in bench:
             parts.append(f"<player><player_key>{p['player_key']}</player_key><position>BN</position></player>")

    if ir_players:
        for p in ir_players:
            parts.append(f"<player><player_key>{p['player_key']}</player_key><position>{p['sel']}</position></player>")

    parts.append('</players></roster></fantasy_content>')
    return "".join(parts)

def is_season_active(team_key, token):
    # Derive league key: "418.l.12345.t.6" -> "418.l.12345"
    parts = team_key.split(".t.")
    if len(parts) != 2:
        logging.warning("Could not parse league key from YAHOO_TEAM_KEY; assuming season is active.")
        return True
    league_key = parts[0]
    
    try:
        xml = api_get(f"/league/{league_key}", token)
        root = ET.fromstring(xml)
        namespace = {'ns': 'http://fantasysports.yahooapis.com/fantasy/v2/base.rng'}
        is_finished = find_text(root, ".//ns:is_finished", namespace)
        current_week = find_text(root, ".//ns:current_week", namespace)
        if is_finished == "1" or current_week is None:
            return False
        return True
        
    except requests.exceptions.HTTPError as e:
        # Catch 400, 403, and 404 errors which indicate the season/league is no longer accessible
        if e.response.status_code in [400, 403, 404]:
            logging.info(f"API returned {e.response.status_code} for league check. Assuming offseason is active.")
            return False
            
        # For other HTTP errors (like 500 server errors), fall back to True
        logging.warning(f"HTTP Error checking season status: {e}. Assuming active.")
        return True
        
    except Exception as e:
        # Catch non-HTTP errors (like XML parsing issues)
        logging.warning(f"Could not determine season status: {e}. Assuming active.")
        return True

def apply_lineup(team_key, payload, token):
    r = requests.put(f"{FANTASY_BASE}/team/{team_key}/roster", headers={
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/xml"
    }, data=payload)
    return r.status_code, r.text

def has_lineup_changed(players, assigned, bench, ir_players=None):
    current = {p['player_key']: p['sel'] for p in players}
    proposed = {}
    for pos, plist in assigned.items():
        for p in plist: proposed[p['player_key']] = pos
    for p in bench: proposed[p['player_key']] = 'BN'
    if ir_players:
        for p in ir_players: proposed[p['player_key']] = p['sel']

    for player_key in current:
        if current.get(player_key) != proposed.get(player_key):
            return True
    return False

def check_roster_sanity(players):
    roster_size = len(players)
    ir_eligible = [p for p in players if any(tag in p["eligible"] for tag in ("IR", "IR+", "NA"))]
    ir_count = len(ir_eligible)

    if roster_size > 18:
        overage = roster_size - 18
        if ir_count < overage:
            msg = f"⚠️ **Roster Illegal!** {roster_size} players (Limit 18). Only {ir_count} IR-eligible."
            send_discord_message("Roster Sanity Check", msg)
            return

    if roster_size == 18 and ir_count > 0:
        msg = f"ℹ️ **Optimization Alert:** You have 18 players and {ir_count} are IR eligible. You could IR them and add a Free Agent."
        send_discord_message("Free Roster Spot Available", msg)

def send_discord_embed(title, assigned, bench):
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url: return

    fields = []
    for pos, plist in assigned.items():
        names = ", ".join(p["name"] for p in plist)
        fields.append({"name": pos, "value": names or "—", "inline": True})

    bench_names_list = []
    for p in bench:
        icon = "🏒" if p["rank"] < 9999 else "⏸️"
        if p.get("status"): icon = "🏥"
        bench_names_list.append(f"{icon} {p['name']}")
    bench_names = ", ".join(bench_names_list) or "—"

    embed = {
        "title": title,
        "color": 0x2ECC71,
        "description": f"✅ Lineup Set — {datetime.now(NHL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} ET",
        "fields": fields + [{"name": "Bench", "value": bench_names, "inline": False}],
        "footer": {"text": "Fantasy Setter"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    try:
        requests.post(webhook_url, json={"username": "Fantasy Setter", "embeds": [embed]})
    except Exception:
        pass

if __name__ == "__main__":
    logging.info("=== Starting Fantasy Lineup Setter ===")
    try:
        token = refresh()
        logging.info("Token refreshed successfully.")

        TEAM_KEY = os.getenv("YAHOO_TEAM_KEY")
        if not TEAM_KEY:
            raise SystemExit("Missing YAHOO_TEAM_KEY in .env")

        if not is_season_active(TEAM_KEY, token):
            logging.info("Season is not active (off-season or finished). Nothing to do.")
            raise SystemExit(0)

        roster_xml = api_get(f"/team/{TEAM_KEY}/roster", token)
        players = parse_roster(roster_xml)
        check_roster_sanity(players)

        ir_players = [p for p in players if p.get('sel') in ('IR', 'IR+', 'NA')]
        active_players = [p for p in players if p.get('sel') not in ('IR', 'IR+', 'NA')]

        if ir_players:
            logging.info(f"ℹ️ Preserving IR positions for: {', '.join(p['name'] for p in ir_players)}")

        adjust_rankings_with_schedule(active_players)
        assigned, bench = choose_lineup(active_players, SLOTS)

        if not has_lineup_changed(players, assigned, bench, ir_players):
            logging.info("Lineup is already optimal. No changes made.")
        else:
            logging.info("Submitting new lineup...")
            payload = build_payload(assigned, bench, ir_players)
            code, text = apply_lineup(TEAM_KEY, payload, token)

            if code == 200:
                logging.info("SUCCESS: Lineup successfully applied.")
                send_discord_embed("Fantasy Lineup Updated", assigned, bench)
            else:
                logging.error(f"FAILURE: Yahoo API returned {code}: {text}")
                send_email("Fantasy Lineup Error", f"Error code {code}:\n\n{text}")
    except Exception as e:
        logging.exception("CRITICAL ERROR: Script crashed.")
        send_email("Fantasy Setter Error", "Script crashed in main.")