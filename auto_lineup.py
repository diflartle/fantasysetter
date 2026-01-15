import os, requests, base64, json, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone, date
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
    sender = os.environ["EMAIL_FROM"]
    recipient = os.environ["EMAIL_TO"]
    password = os.environ["EMAIL_PASS"]
    server = os.environ["SMTP_SERVER"]
    port = int(os.environ["SMTP_PORT"])

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
        
        status = find_text(p, "ns:status", namespace)
        
        # Use custom ranking if available, otherwise use default
        rank = rankings.get(name, 9999)
        
        players.append({
            "player_key": pk, 
            "name": name, 
            "eligible": elig, 
            "sel": sel, 
            "team_abbr": team_abbr, 
            "rank": rank,
            "status": status  # Store the status
        })
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
    """
    Adjust rank based on Schedule AND Injury status.
    
    Priority Levels:
    1. Healthy + Game Today (Base Rank)
    2. Healthy + No Game    (Base Rank + 10,000)
    3. Injured (O/IR)       (Base Rank + 20,000) -> Always benched if possible
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    def normalize_team_abbrev(abbrev):
        return TEAM_ABBREV_MAP.get(abbrev, abbrev)
        
    active_teams = [normalize_team_abbrev(t) for t in get_active_teams(today)]
    
    for p in players:
        # 1. CHECK INJURY STATUS
        # 'O' (Out) and 'IR' definitely won't play. 
        # We treat 'DTD' (Day-to-Day) as active because they often play.
        if p.get("status") in ["O", "IR", "IR+"]:
            p["rank"] += 20000  # Massive penalty puts them at bottom of list
            continue  # Skip schedule check; injury overrides schedule

        # 2. CHECK SCHEDULE
        team = p.get("team_abbr")
        if team not in active_teams:
            p["rank"] += 10000  # Penalty puts them on bench, but above injured players

def choose_lineup(players, slots):
    """
    Assigns players to positions based on Rank, using a 'Lookahead' strategy
    to resolve dual-eligibility conflicts optimally.
    """
    # Separate goalies from skaters
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
    
    # Sort skaters by rank (Best players first)
    # Because of adjust_rankings, this order is: Playing > No Game > Injured
    skaters.sort(key=lambda x: x["rank"])
    
    for player in skaters:
        if player["player_key"] in used:
            continue
            
        available = get_available_positions(player)
        
        if not available:
            continue # Benched
            
        if len(available) == 1:
            # No choice: Take the only open slot
            pos = available[0]
            assigned[pos].append(player)
            used.add(player["player_key"])
        else:
            # SMART LOOKAHEAD:
            # If player fits multiple slots (e.g. LW/RW), take the one that 
            # saves the "scarcer" slot for a teammate.
            
            best_alternative_ranks = {}
            
            for pos in available:
                # Find the rank of the next best AVAILABLE teammate for this position
                best_rank_for_pos = 99999 
                
                for teammate in skaters:
                    if teammate["player_key"] == player["player_key"]: continue
                    if teammate["player_key"] in used: continue
                    
                    if pos in get_eligible_positions(teammate):
                        best_rank_for_pos = teammate["rank"]
                        break # Found best one (list is sorted)
                
                best_alternative_ranks[pos] = best_rank_for_pos
            
            # We choose the position where the ALTERNATIVE option is WORST.
            # This saves the "good alternative" slot for the other player.
            chosen_pos = max(best_alternative_ranks, key=best_alternative_ranks.get)
            
            assigned[chosen_pos].append(player)
            used.add(player["player_key"])
            
    # Fill goalie positions (Rank-based)
    goalies.sort(key=lambda x: x["rank"])
    for g in goalies[:slots["G"]]:
        assigned["G"].append(g)
        used.add(g["player_key"])
    
    # Bench is everyone not used
    bench = [p for p in players if p["player_key"] not in used]
    bench.sort(key=lambda x: x["rank"])
    
    return assigned, bench


def build_payload(assigned, bench=None, ir_players=None, date=None):
    """
    Build XML payload for roster assignment.

    Args:
        assigned: Dict of position -> list of players
        bench: Optional list of bench players (will use 'BN' as position)
        ir_players: Optional list of IR players (will preserve their IR/IR+ position)
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

    # Optionally add IR players (preserve their current IR position)
    if ir_players:
        for p in ir_players:
            parts.append(
                f"<player>"
                f"<player_key>{p['player_key']}</player_key>"
                f"<position>{p['sel']}</position>"
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

def has_lineup_changed(players, assigned, bench, ir_players=None):
    """
    Check if the proposed lineup differs from current lineup.

    Args:
        players: List of all players with their current 'sel' positions
        assigned: Dict of proposed position assignments
        bench: List of proposed bench players
        ir_players: Optional list of IR players (their positions remain unchanged)

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

    # IR players keep their current position
    if ir_players:
        for p in ir_players:
            proposed[p['player_key']] = p['sel']

    # Compare: if any player's position changed, return True
    for player_key in current:
        if current.get(player_key) != proposed.get(player_key):
            return True

    return False

def check_roster_sanity(players):
    """
    Performs roster sanity checks:
      1. If roster > 18 players, ensure extras are IR-eligible.
      2. If roster == 18 but at least one player is IR-eligible, warn that an IR slot is unused.
    """
    roster_size = len(players)

    # Identify IR-eligible players (IR, IR+, NA)
    ir_eligible = [
        p for p in players
        if any(tag in p["eligible"] for tag in ("IR", "IR+", "NA"))
    ]
    ir_count = len(ir_eligible)

    # --- Case 1: Too many players but not enough IR eligibility ---
    if roster_size > 18:
        overage = roster_size - 18

        if ir_count < overage:
            msg_lines = [
                f"⚠️ Roster sanity issue detected!",
                f"You have **{roster_size} total players**, which is **{overage} over** the normal 18 slots.",
                "",
                f"However, only **{ir_count} players** are IR/IR+/NA eligible.",
                f"You should have **at least {overage} IR-eligible players**.",
                "",
                "IR-eligible players:",
            ]

            for p in ir_eligible:
                tags = "/".join(p["eligible"])
                msg_lines.append(f"- {p['name']} [{tags}]")

            msg_lines.append("Drop someone or fix roster positions.")

            send_discord_message("Roster eligibility mismatch", "\n".join(msg_lines))
            # Nothing else to check in this case
            return

    # --- Case 2: Exactly 18 players but IR slot is wasted ---
    if roster_size == 18 and ir_count > 0:
        msg_lines = [
            f"⚠️ IR slot unused!",
            f"You have **exactly 18 players**, meaning your active roster is full.",
            "",
            f"However, **{ir_count} players** are IR/IR+/NA eligible.",
            "That means you can:",
            "  1. Move an IR-eligible player to IR, and",
            "  2. Add a free agent.",
            "",
            "IR-eligible players:",
        ]

        for p in ir_eligible:
            tags = "/".join(p["eligible"])
            msg_lines.append(f"- {p['name']} [{tags}]")

        send_discord_message("Unused IR Slot Available", "\n".join(msg_lines))

    # Otherwise: roster looks fine

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

    # Build bench names with game status icons
    # If rank < 9999, player has a game today (since adjust_rankings_with_schedule adds 9999 for no game)
    bench_names_list = []
    for p in bench:
        icon = "🏒" if p["rank"] < 9999 else "⏸️"
        bench_names_list.append(f"{icon} {p['name']}")
    bench_names = ", ".join(bench_names_list) or "—"

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
        "timestamp": datetime.now(timezone.utc).isoformat()
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


def print_lineup(assigned, bench):
    print(f"\n{'POS':<4} {'PLAYER':<25} {'RANK':<6} {'STATUS'}")
    print("-" * 45)
    
    # Define display order so it looks like a real roster
    display_order = ["C", "LW", "RW", "D", "G"]
    
    for pos in display_order:
        players = assigned.get(pos, [])
        for p in players:
            # Add a status flag if they are injured
            status = f"({p['status']})" if p.get('status') else ""
            print(f"{pos:<4} {p['name']:<25} {p['rank']:<6} {status}")
            
    print("-" * 45)
    print("BENCH:")
    for p in bench:
        status = f"({p['status']})" if p.get('status') else ""
        print(f"{'BN':<4} {p['name']:<25} {p['rank']:<6} {status}")
    print("\n")

if __name__ == "__main__":
    token = refresh()
    print(f"[{datetime.now()}] refreshed token")

    TEAM_KEY = os.getenv("YAHOO_TEAM_KEY")
    if not TEAM_KEY:
        raise SystemExit("Missing YAHOO_TEAM_KEY in .env")

    roster_xml = api_get(f"/team/{TEAM_KEY}/roster", token)
    players = parse_roster(roster_xml)
    check_roster_sanity(players)

    # Separate IR player from active roster (there can only be one IR slot)
    ir_players = [p for p in players if p.get('sel') in ('IR', 'IR+', 'NA')]
    active_players = [p for p in players if p.get('sel') not in ('IR', 'IR+', 'NA')]

    if len(ir_players) > 1:
        # This shouldn't happen, but handle it gracefully
        print(f"⚠️ Warning: Found {len(ir_players)} players in IR slots, but only 1 IR slot exists!")
        print(f"   This may indicate an issue with Yahoo's API response.")

    if ir_players:
        print(f"ℹ️ Found {len(ir_players)} player(s) in IR slot - preserving their position")
        for p in ir_players:
            print(f"   - {p['name']} ({p['sel']})")

    # Optionally adjust ranks for schedule (only for active players)
    adjust_rankings_with_schedule(active_players)

    assigned, bench = choose_lineup(active_players, SLOTS)

    # Check if lineup actually changed
    if not has_lineup_changed(players, assigned, bench, ir_players):
        print("✅ Lineup is already optimal - no changes needed.")
    else:
        payload = build_payload(assigned, bench, ir_players)

        print("🟢 Submitting lineup changes...")
        code, text = apply_lineup(TEAM_KEY, payload, token)

        if code == 200:
            print("✅ Lineup successfully applied.")
            send_discord_embed("Fantasy Lineup Updated", assigned, bench)
        else:
            print(f"⚠️ Error setting lineup ({code}): {text}")
            send_email("Fantasy Lineup Error", f"Error code {code}:\n\n{text}")