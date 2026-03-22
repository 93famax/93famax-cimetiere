"""
93Famax — Cimetière du Chat
Tourne sur Railway 24h/24.
Détecte les viewers qui disparaissent et sauvegarde leurs derniers mots.
Un viewer devient "fantôme" s'il n'a pas écrit depuis GHOST_DAYS jours
après avoir été présent sur au moins GHOST_MIN_STREAMS streams.
"""

import socket
import re
import json
import threading
import collections
import time
import os
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── CONFIG ───────────────────────────────────────────────────
OAUTH_TOKEN      = os.environ.get("TWITCH_TOKEN", "oauth:chjui1s9tbp9bclyle6j3bb8sryq21")
CHANNEL          = os.environ.get("TWITCH_CHANNEL", "93famax")
PORT             = int(os.environ.get("PORT", 8080))
SAVE_FILE        = Path("/tmp/cemetery.json")

GHOST_DAYS       = 90   # absent depuis N jours → fantôme
GHOST_MIN_STREAMS = 3   # doit avoir été là au moins N streams différents

# ─── DONNÉES ──────────────────────────────────────────────────
# viewer_data = { username_lower: {
#   username, first_seen, last_seen, last_message,
#   message_count, stream_sessions: set(), color
# }}
viewer_data = {}
lock = threading.Lock()

def save():
    try:
        with lock:
            # Convertir stream_sessions en list pour JSON
            data = {}
            for k, v in viewer_data.items():
                d = dict(v)
                d['stream_sessions'] = list(v.get('stream_sessions', set()))
                data[k] = d
            SAVE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"[Save] {e}")

def load():
    global viewer_data
    if SAVE_FILE.exists():
        try:
            raw = json.loads(SAVE_FILE.read_text())
            for k, v in raw.items():
                v['stream_sessions'] = set(v.get('stream_sessions', []))
                viewer_data[k] = v
            print(f"[Cemetery] {len(viewer_data)} viewers chargés")
        except Exception as e:
            print(f"[Cemetery] Erreur chargement : {e}")

def color_for_user(username):
    colors = ['#ff7f50','#9147ff','#60a5fa','#4ade80','#f472b6','#facc15',
              '#fb923c','#34d399','#c084fc','#a3e635','#ff3c00','#818cf8']
    return colors[sum(ord(c) for c in username) % len(colors)]

def get_ghosts():
    """Retourne les viewers considérés comme fantômes"""
    now = int(time.time() * 1000)
    ghosts = []
    with lock:
        for uname, v in viewer_data.items():
            days_absent = (now - v['last_seen']) / 86400000
            stream_count = len(v.get('stream_sessions', set()))
            if days_absent >= GHOST_DAYS and stream_count >= GHOST_MIN_STREAMS:
                ghosts.append({
                    'username':      v['username'],
                    'color':         v.get('color', '#c8cdd4'),
                    'first_seen':    v['first_seen'],
                    'last_seen':     v['last_seen'],
                    'last_message':  v['last_message'],
                    'message_count': v['message_count'],
                    'stream_count':  stream_count,
                })
    return ghosts

# ─── IRC ──────────────────────────────────────────────────────
current_session = str(int(time.time()))  # ID de session unique par lancement

def irc_reader():
    global current_session
    while True:
        try:
            print(f"[IRC] Connexion à #{CHANNEL}...")
            sock = socket.socket()
            sock.connect(("irc.chat.twitch.tv", 6667))
            sock.send(f"PASS {OAUTH_TOKEN}\r\n".encode())
            sock.send(f"NICK {CHANNEL}\r\n".encode())
            sock.send(f"CAP REQ :twitch.tv/tags\r\n".encode())
            sock.send(f"JOIN #{CHANNEL}\r\n".encode())
            sock.settimeout(300)
            print(f"[IRC] ✅ Connecté")
            current_session = str(int(time.time()))

            buf = ""
            while True:
                data = sock.recv(4096).decode("utf-8", errors="ignore")
                if not data: break
                buf += data
                lines = buf.split("\r\n")
                buf = lines[-1]

                for line in lines[:-1]:
                    if line.startswith("PING"):
                        sock.send("PONG :tmi.twitch.tv\r\n".encode())
                        continue
                    if "PRIVMSG" not in line:
                        continue

                    name_m  = re.search(r'display-name=([^;]+)', line)
                    color_m = re.search(r'color=(#[0-9A-Fa-f]{6})', line)
                    msg_m   = re.search(r'PRIVMSG #\w+ :(.+)', line)
                    if not name_m or not msg_m:
                        continue

                    username = name_m.group(1).strip()
                    color    = color_m.group(1) if color_m else color_for_user(username)
                    if not color or color == "#":
                        color = color_for_user(username)
                    text = msg_m.group(1).strip()
                    ts   = int(time.time() * 1000)
                    ukey = username.lower()

                    with lock:
                        if ukey not in viewer_data:
                            viewer_data[ukey] = {
                                'username':      username,
                                'color':         color,
                                'first_seen':    ts,
                                'last_seen':     ts,
                                'last_message':  text,
                                'message_count': 1,
                                'stream_sessions': {current_session}
                            }
                        else:
                            v = viewer_data[ukey]
                            v['last_seen']     = ts
                            v['last_message']  = text
                            v['message_count'] = v.get('message_count', 0) + 1
                            v.setdefault('stream_sessions', set()).add(current_session)
                            if color and color != "#":
                                v['color'] = color

        except Exception as e:
            print(f"[IRC] Déconnecté ({e}), reconnexion dans 5s...")
            time.sleep(5)

# ─── SERVEUR ──────────────────────────────────────────────────
HTML = open(Path(__file__).parent / "index.html", encoding="utf-8").read()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))

        elif self.path == "/ghosts":
            data = json.dumps(get_ghosts(), ensure_ascii=False)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data.encode("utf-8"))

        elif self.path == "/stats":
            with lock:
                ghosts = get_ghosts()
                stats = {
                    "total_viewers":  len(viewer_data),
                    "total_ghosts":   len(ghosts),
                    "ghost_days":     GHOST_DAYS,
                    "min_streams":    GHOST_MIN_STREAMS,
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(stats).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def log_message(self, f, *a): pass

def autosave():
    while True:
        time.sleep(60)
        save()

if __name__ == "__main__":
    print("=" * 50)
    print("  93Famax — Cimetière du Chat")
    print(f"  Channel : #{CHANNEL}")
    print(f"  Fantôme après : {GHOST_DAYS} jours d'absence")
    print(f"  Min streams   : {GHOST_MIN_STREAMS}")
    print(f"  Port          : {PORT}")
    print("=" * 50)

    load()
    threading.Thread(target=irc_reader, daemon=True).start()
    threading.Thread(target=autosave, daemon=True).start()

    print(f"[Cemetery] Serveur sur http://0.0.0.0:{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
