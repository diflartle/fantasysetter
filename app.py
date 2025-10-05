# app.py
import json
import os
import base64
from flask import Flask, request, redirect, render_template_string
import requests
from dotenv import load_dotenv
load_dotenv()

# ---------- CONFIG ----------
CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")
REDIRECT_URI = os.getenv("YAHOO_REDIRECT_URI", "https://localhost:5000/callback")
TOKEN_FILE = "yahoo_tokens.json"

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev-secret")

# ---------- Token storage ----------
def save_tokens(data):
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Tokens saved to {TOKEN_FILE}")

def load_tokens():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)

def basic_auth_header():
    auth = f"{CLIENT_ID}:{CLIENT_SECRET}"
    return base64.b64encode(auth.encode()).decode()

# ---------- Flask routes ----------
INDEX_HTML = """
<!DOCTYPE html>
<html>
<head><title>Yahoo OAuth</title></head>
<body>
<h2>Yahoo Fantasy OAuth Setup</h2>
{% if tokens %}
<p>✅ Tokens already saved to <code>yahoo_tokens.json</code></p>
<pre>{{ tokens | tojson(indent=2) }}</pre>
<p><a href="{{ url_for('authorize') }}">Re-authorize</a></p>
{% else %}
<p>Click below to authorize with Yahoo and save tokens:</p>
<p><a href="{{ url_for('authorize') }}">Authorize with Yahoo</a></p>
{% endif %}
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head><title>Success</title></head>
<body>
<h2>✅ Authorization Successful</h2>
<p>Access and refresh tokens have been saved to <code>yahoo_tokens.json</code></p>
<pre>{{ tokens | tojson(indent=2) }}</pre>
<p><a href="{{ url_for('index') }}">Back to home</a></p>
</body>
</html>
"""

@app.route("/")
def index():
    tokens = load_tokens()
    return render_template_string(INDEX_HTML, tokens=tokens)

@app.route("/authorize")
def authorize():
    auth_url = f"{AUTH_URL}?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=fspt-w"
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Authorization failed or denied", 400

    headers = {
        "Authorization": f"Basic {basic_auth_header()}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    r = requests.post(TOKEN_URL, headers=headers, data=data)
    r.raise_for_status()
    tokens = r.json()
    save_tokens(tokens)

    return render_template_string(SUCCESS_HTML, tokens=tokens)

if __name__ == "__main__":
    if not CLIENT_ID or not CLIENT_SECRET:
        print("⚠️  ERROR: YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET must be set in .env")
        exit(1)

    print("=" * 60)
    print("Yahoo Fantasy OAuth Setup")
    print("=" * 60)
    print(f"Redirect URI: {REDIRECT_URI}")
    print("Make sure this matches your Yahoo app configuration!")
    print("=" * 60)
    print("\nStarting server at https://localhost:5000")
    print("Visit https://localhost:5000 in your browser\n")

    # Try to load certs, fall back to generating if not found
    cert_path = "certs/cert.pem"
    key_path = "certs/key.pem"

    if not os.path.exists(cert_path) or not os.path.exists(key_path):
        print("⚠️  SSL certificates not found in certs/")
        print("Generate them with:")
        print("  mkdir certs")
        print("  openssl req -x509 -newkey rsa:4096 -nodes -out certs/cert.pem -keyout certs/key.pem -days 365")
        exit(1)

    app.run(host="0.0.0.0", port=5000, ssl_context=(cert_path, key_path))
