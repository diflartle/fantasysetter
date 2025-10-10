import os, requests, base64, json, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, date
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from dotenv import load_dotenv
load_dotenv()

CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
TOKEN_FILE = "yahoo_tokens.json"
REDIRECT_URI = "http://localhost:5000/callback"
FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
SLOTS = {"C":2,"LW":2,"RW":2,"D":4,"G":2}
TEAM_ABBREV_MAP = {
    "LAK": "LA",
    "NJD": "NJ",
    "SJS": "SJ",
    "TBL": "TB",
}

def send_email(subject, body):
    sender = os.getenv("EMAIL_FROM")
    recipient = os.getenv("EMAIL_TO")
    password = os.getenv("EMAIL_PASS")
    server = os.getenv("SMTP_SERVER")
    port = int(os.getenv("SMTP_PORT"))

    msg = MIMEMultipart()
    msg["From"] = formataddr(("Fantasy Setter", sender))
    msg["To"] = formataddr(("No Postseason Losses", recipient))
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(server, port) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
        print("📧 Email sent successfully.")
    except Exception as e:
        print("⚠️ Failed to send email:", e)

def send_discord_message(subject, body):
    import requests, json, os
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️ No DISCORD_WEBHOOK_URL found in .env")
        return

    content = f"**{subject}**\n```{body}```"
    try:
        r = requests.post(webhook_url, json={"content": content})
        if r.status_code == 204:
            print("📨 Discord message sent.")
        else:
            print(f"⚠️ Discord webhook failed ({r.status_code}): {r.text}")
    except Exception as e:
        print("⚠️ Discord webhook error:", e)

def load_tokens():
    with open(TOKEN_FILE) as f: return json.load(f)

def save_tokens(t):
    with open(TOKEN_FILE,"w") as f: json.dump(t,f)

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
        with open("rankings.json") as f:
            rankings = json.load(f)
    for p in root.findall(".//ns:player", namespace):
        pk = find_text(p, "ns:player_key", namespace)
        name = find_text(p, "ns:name/ns:full", namespace) or find_text(p, "ns:name", namespace)
        elig = [x.text for x in p.findall(".//ns:eligible_positions/ns:position", namespace)]
        sel = find_text(p, "ns:selected_position/ns:position", namespace)
        team_abbr = find_text(p, "ns:editorial_team_abbr", namespace)
        # Use custom ranking if available, otherwise use default
        rank = rankings.get(name, 9999)
        players.append({"player_key":pk,"name":name,"eligible":elig,"sel":sel,"team_abbr":team_abbr,"rank":rank})
    return players

def get_active_teams(date):
    """Fetches the abbreviations of NHL teams playing today."""
    today_str = date
    api_url = f"https://api-web.nhle.com/v1/schedule/{today_str}"
    try:
        games_data = requests.get(api_url, timeout=10).json()
        if not games_data.get("gameWeek") or not games_data["gameWeek"][0].get("games"):
            print(f"⚠️ No games found for {today_str}")
            return []
        teams = [
            team_abbr for game in games_data["gameWeek"][0]["games"]
            for team_abbr in (game["awayTeam"]["abbrev"], game["homeTeam"]["abbrev"])
        ]
        return teams
    except Exception as e:
        print(f"⚠️ Error fetching NHL schedule: {e}")
        return []

def adjust_rankings_with_schedule(players):
    """Inflate rank for players with no game today."""
    today = datetime.now().strftime("%Y-%m-%d")
    def normalize_team_abbrev(abbrev):
        return TEAM_ABBREV_MAP.get(abbrev, abbrev)
    active_teams = [normalize_team_abbrev(t) for t in get_active_teams(today)]  # From NHL API
    for p in players:
        team = p.get("team_abbr")
        if team not in active_teams:
            p["rank"] += 9999

def choose_lineup(players, slots):
    """
    Optimally assign players to roster positions.
    
    Args:
        players: List of player dicts with 'eligible', 'rank', 'player_key'
        slots: Dict of position -> count (e.g., {'C': 2, 'LW': 2, 'RW': 2, 'D': 4, 'G': 2})
    
    Returns:
        assigned: Dict of position -> list of players
        bench: List of unassigned players
    """
    # Separate goalies from skaters
    goalies = [p for p in players if p["eligible"] == ["G"]]
    skaters = [p for p in players if "G" not in p["eligible"]]
    
    assigned = defaultdict(list)
    used = set()
    
    # Process skaters
    skater_positions = ["C", "LW", "RW", "D"]
    
    def get_eligible_positions(player):
        """Get list of skater positions player is eligible for"""
        return [pos for pos in player["eligible"] if pos in skater_positions]
    
    def get_available_positions(player):
        """Get positions player is eligible for that still have slots"""
        eligible = get_eligible_positions(player)
        return [pos for pos in eligible if len(assigned[pos]) < slots[pos]]
    
    # Sort skaters by rank (best first)
    skaters.sort(key=lambda x: x["rank"])
    
    # Multi-pass assignment strategy
    # Pass 1: Assign players who can only fill one position type
    for player in skaters:
        if player["player_key"] in used:
            continue
        
        available = get_available_positions(player)
        if len(available) == 1 and player['rank'] < 9999:
            assigned[available[0]].append(player)
            used.add(player["player_key"])
    
    # Pass 2: Assign remaining players, prioritizing filling positions that are hardest to fill
    # Calculate scarcity: how many remaining eligible players per remaining slot
    while len(used) < sum(slots[pos] for pos in skater_positions):
        # Get unfilled positions
        unfilled = {pos: slots[pos] - len(assigned[pos]) 
                   for pos in skater_positions 
                   if len(assigned[pos]) < slots[pos]}
        
        if not unfilled:
            break
        
        # Find the scarcest position (fewest eligible players per remaining slot)
        scarcity = {}
        for pos, remaining_slots in unfilled.items():
            eligible_count = sum(1 for p in skaters 
                               if p["player_key"] not in used 
                               and pos in get_eligible_positions(p))
            scarcity[pos] = eligible_count / remaining_slots if remaining_slots > 0 else float('inf')
        
        # Fill the scarcest position with best available player
        scarcest_pos = min(scarcity, key=scarcity.get)
        
        # Find best ranked player eligible for this position
        for player in skaters:
            if player["player_key"] in used:
                continue
            if scarcest_pos in get_eligible_positions(player):
                assigned[scarcest_pos].append(player)
                used.add(player["player_key"])
                break
    
    # Fill goalie positions (straightforward - just by rank)
    goalies.sort(key=lambda x: x["rank"])
    for g in goalies[:slots["G"]]:
        assigned["G"].append(g)
        used.add(g["player_key"])
    
    # Bench is everyone not used
    bench = [p for p in players if p["player_key"] not in used]
    bench.sort(key=lambda x: x["rank"])
    
    return assigned, bench

def build_payload(assigned, bench=None, date=None):
    """
    Build XML payload for roster assignment.
    
    Args:
        assigned: Dict of position -> list of players
        bench: Optional list of bench players (will use 'BN' as position)
        date: Optional date string (YYYY-MM-DD). Defaults to today.
    
    Returns:
        XML string for roster submission
    """
    # Default to today if no date provided
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    
    parts = [
        '<?xml version="1.0"?><fantasy_content><roster>',
        '<coverage_type>date</coverage_type>',
        f'<date>{date}</date>',
        '<players>'
    ]
    
    # Add assigned players with their positions
    for pos, plist in assigned.items():
        for p in plist:
            parts.append(
                f"<player>"
                f"<player_key>{p['player_key']}</player_key>"
                f"<position>{pos}</position>"
                f"</player>"
            )
    
    # Optionally add bench players
    if bench:
        for p in bench:
            parts.append(
                f"<player>"
                f"<player_key>{p['player_key']}</player_key>"
                f"<position>BN</position>"
                f"</player>"
            )
    
    parts.append('</players></roster></fantasy_content>')
    
    return "".join(parts)

def apply_lineup(team_key, payload, token):
    r = requests.put(f"{FANTASY_BASE}/team/{team_key}/roster", headers={
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/xml"
    }, data=payload)
    return r.status_code, r.text

def has_lineup_changed(players, assigned, bench):
    """
    Check if the proposed lineup differs from current lineup.
    
    Args:
        players: List of all players with their current 'sel' positions
        assigned: Dict of proposed position assignments
        bench: List of proposed bench players
    
    Returns:
        bool: True if lineup has changed, False otherwise
    """
    # Build a map of current assignments: player_key -> position
    current = {p['player_key']: p['sel'] for p in players}
    
    # Build a map of proposed assignments: player_key -> position
    proposed = {}
    for pos, plist in assigned.items():
        for p in plist:
            proposed[p['player_key']] = pos
    
    for p in bench:
        proposed[p['player_key']] = 'BN'
    
    # Compare: if any player's position changed, return True
    for player_key in current:
        if current.get(player_key) != proposed.get(player_key):
            return True
    
    return False

def send_discord_embed(title, assigned, bench):
    import requests, os
    from datetime import datetime

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("⚠️ No DISCORD_WEBHOOK_URL found in .env")
        return

    # Build the formatted lineup
    fields = []
    for pos, plist in assigned.items():
        names = ", ".join(p["name"] for p in plist)
        fields.append({
            "name": pos,
            "value": names or "—",
            "inline": True
        })

    bench_names = ", ".join(p["name"] for p in bench) or "—"

    embed = {
        "title": title,
        "color": 0x2ECC71,  # Discord green
        "description": f"✅ Lineup successfully applied — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "fields": fields + [{
            "name": "Bench",
            "value": bench_names,
            "inline": False
        }],
        "footer": {
            "text": "Fantasy Setter"
        },
        "timestamp": datetime.utcnow().isoformat()
    }

    data = {
        "username": "Fantasy Setter",
        "embeds": [embed]
    }

    try:
        r = requests.post(webhook_url, json=data)
        if r.status_code in (200, 204):
            print("📨 Discord embed sent.")
        else:
            print(f"⚠️ Discord webhook failed ({r.status_code}): {r.text}")
    except Exception as e:
        print("⚠️ Discord webhook error:", e)

if __name__ == "__main__":
    token = refresh()
    print(f"[{datetime.now()}] refreshed token")

    TEAM_KEY = os.getenv("YAHOO_TEAM_KEY")
    if not TEAM_KEY:
        raise SystemExit("Missing YAHOO_TEAM_KEY in .env")

    roster_xml = api_get(f"/team/{TEAM_KEY}/roster", token)
    players = parse_roster(roster_xml)

    # Optionally adjust ranks for schedule
    adjust_rankings_with_schedule(players)

    assigned, bench = choose_lineup(players, SLOTS)
    
    # Check if lineup actually changed
    if not has_lineup_changed(players, assigned, bench):
        print("✅ Lineup is already optimal - no changes needed.")
    else:
        payload = build_payload(assigned, bench)
        
        print("🟢 Submitting lineup changes...")
        code, text = apply_lineup(TEAM_KEY, payload, token)

        if code == 200:
            print("✅ Lineup successfully applied.")
            send_discord_embed("Fantasy Lineup Updated", assigned, bench)
        else:
            print(f"⚠️ Error setting lineup ({code}): {text}")
            send_email("Fantasy Lineup Error", f"Error code {code}:\n\n{text}")