"""Pinpoint - a GeoGuessr-style party game for 2-10 players.

Run:  python server.py            (default port 8000, or $PORT when hosted)
      python server.py --port 9000

Players open the site, then create a game or join one with its 4-letter code.
Several groups can play on the same server at once.
Uses only the Python standard library.
"""
import argparse
import json
import math
import os
import random
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from locations import ANCHORS

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
USER_AGENT = "PinpointPartyGame/1.0 (private multiplayer geography game)"
MAX_PLAYERS = 10
MIN_PLAYERS = 2
OFFLINE_AFTER = 30  # seconds without a poll before a player counts as gone
COLORS = ["#f2a93b", "#4fb3a9", "#e5604d", "#8f7cf0", "#5aa7e8",
          "#e86fb0", "#9bd15a", "#f07f3a", "#c9b458", "#7fd6f5"]


def load_api_key():
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
    if key:
        return key
    path = os.path.join(ROOT, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return (json.load(f).get("google_maps_api_key") or "").strip()
    return ""


API_KEY = load_api_key()
# The address players use. Render sets RENDER_EXTERNAL_URL itself. It is sent as the
# Referer on server-side Google calls, so a key limited to this website still works.
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")


class KeyProblem(Exception):
    """Google refused the API key: retrying with other places won't help."""
MODE = "streetview" if API_KEY else "photos"


# ---------------------------------------------------------------- geography

def haversine_km(a, b):
    r = 6371.0088
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp = p2 - p1
    dl = math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def score_for(km):
    # Same curve as GeoGuessr's world map: 5000 at the spot, ~1840 at 1500 km.
    if km < 0.025:
        return 5000
    return round(5000 * math.exp(-km / 1492.7))


def http_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def jitter(anchor, spread):
    lat, lng = anchor[0], anchor[1]
    return {"lat": max(-85, min(85, lat + random.uniform(-spread, spread))),
            "lng": ((lng + random.uniform(-spread, spread) + 180) % 360) - 180}


def find_streetview(anchor):
    """Snap a jittered anchor to the nearest outdoor Street View panorama.
    The Street View metadata endpoint is free and does not use quota."""
    point = jitter(anchor, 0.25)
    q = urllib.parse.urlencode({
        "location": f"{point['lat']},{point['lng']}",
        "radius": 25000, "source": "outdoor", "key": API_KEY})
    headers = {"Referer": PUBLIC_URL + "/"} if PUBLIC_URL else None
    data = http_json("https://maps.googleapis.com/maps/api/streetview/metadata?" + q, headers)
    if data.get("status") == "REQUEST_DENIED":
        raise KeyProblem(data.get("error_message") or "Google refused the API key.")
    if data.get("status") != "OK":
        return None
    loc = data["location"]
    return {"lat": loc["lat"], "lng": loc["lng"], "panoId": data["pano_id"]}


BAD_WORDS = ("logo", "map", "flag", "coat of arms", "wappen", "portrait", "concert",
             "diagram", "chart", "plan", "screenshot", "signature", "svg", "icon")


def strip_html(s):
    out, inside = [], False
    for ch in s or "":
        if ch == "<":
            inside = True
        elif ch == ">":
            inside = False
        elif not inside:
            out.append(ch)
    return "".join(out).strip()


def find_photos(anchor):
    """Pick 5 landscape photos taken near a jittered anchor on Wikimedia Commons."""
    point = jitter(anchor, 0.04)
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "geosearch",
        "ggsnamespace": 6, "ggscoord": f"{point['lat']}|{point['lng']}",
        "ggsradius": 10000, "ggslimit": 80, "prop": "imageinfo|coordinates",
        "iiprop": "url|mime|size|extmetadata", "iiurlwidth": 1280})
    data = http_json("https://commons.wikimedia.org/w/api.php?" + q)
    pages = (data.get("query") or {}).get("pages") or {}
    good = []
    for p in pages.values():
        info = (p.get("imageinfo") or [{}])[0]
        title = p.get("title", "")
        if info.get("mime") != "image/jpeg":
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        if w < 1000 or w < h * 1.15:
            continue
        if any(b in title.lower() for b in BAD_WORDS):
            continue
        meta = info.get("extmetadata") or {}
        good.append({
            "url": info.get("thumburl") or info.get("url"),
            "page": info.get("descriptionurl"),
            "title": title.replace("File:", ""),
            "author": strip_html((meta.get("Artist") or {}).get("value", ""))[:80] or "Unknown",
            "license": (meta.get("LicenseShortName") or {}).get("value", ""),
            "coord": (p.get("coordinates") or [{}])[0],
        })
    if len(good) < 3:
        return None
    random.shuffle(good)
    # Download the photos now: phones then get them from this server under neutral
    # addresses (file names often give the place away), and broken links are skipped.
    chosen = []
    for c in good[:9]:
        try:
            req = urllib.request.Request(c["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as resp:
                c["data"] = resp.read()
            chosen.append(c)
        except Exception:
            continue
        if len(chosen) == 5:
            break
    if len(chosen) < 3:
        return None
    # The answer is the middle of the chosen photos, so it sits where they were taken.
    pts = [c["coord"] for c in chosen if "lat" in c["coord"]]
    lat = sum(c["lat"] for c in pts) / len(pts) if pts else point["lat"]
    lng = sum(c["lon"] for c in pts) / len(pts) if pts else point["lng"]
    for c in chosen:
        c.pop("coord", None)
    return {"lat": lat, "lng": lng, "photos": chosen}


def make_location(used):
    finder = find_streetview if MODE == "streetview" else find_photos
    pool = [a for a in ANCHORS if a[2] not in used] or ANCHORS
    for _ in range(14):
        anchor = random.choice(pool)
        try:
            loc = finder(anchor)
        except KeyProblem:
            raise
        except Exception as exc:  # network hiccup: try another place
            print("  location lookup failed:", exc)
            loc = None
        if loc:
            loc["place"] = anchor[2]
            used.add(anchor[2])
            return loc
    return None


# ---------------------------------------------------------------- game state

CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I or O, they look like 1 and 0
GAME_IDLE_LIMIT = 3 * 3600


class Game:
    def __init__(self, code):
        self.code = code
        self.last_active = time.time()
        self.lock = threading.RLock()
        self.players = {}       # pid -> {name, color, score, lastSeen, joined}
        self.host = None
        self.settings = {"rounds": 5, "timeLimit": 120, "noMove": False}
        self.reset_match()

    def reset_match(self):
        self.phase = "lobby"    # lobby | loading | round | reveal | final
        self.rounds = []        # each: {loc, guesses, startedAt, endsAt}
        self.round_index = -1
        self.error = ""
        self.match_id = uuid.uuid4().hex[:8]
        for p in self.players.values():
            p["score"] = 0

    # ---- helpers
    def online(self):
        now = time.time()
        return {pid: p for pid, p in self.players.items() if now - p["lastSeen"] < OFFLINE_AFTER}

    def fix_host(self):
        online = self.online()
        if self.host not in online:
            ordered = sorted(online.items(), key=lambda kv: kv[1]["joined"])
            self.host = ordered[0][0] if ordered else None

    def current(self):
        if 0 <= self.round_index < len(self.rounds):
            return self.rounds[self.round_index]
        return None

    def tick(self):
        """Advance time-driven transitions. Called on every request."""
        self.fix_host()
        rnd = self.current()
        if self.phase == "round" and rnd:
            waiting_on = [pid for pid in self.online() if pid in rnd["players"]]
            everyone_done = all(pid in rnd["guesses"] for pid in waiting_on)
            timed_out = rnd["endsAt"] and time.time() >= rnd["endsAt"]
            if everyone_done or timed_out:
                self.reveal()
        if self.phase == "loading" and len(self.rounds) > self.round_index >= 0:
            self.begin_round()

    def begin_round(self):
        rnd = self.rounds[self.round_index]
        rnd["startedAt"] = time.time()
        rnd["players"] = set(self.online())
        limit = self.settings["timeLimit"]
        rnd["endsAt"] = rnd["startedAt"] + limit if limit else None
        self.phase = "round"

    def reveal(self):
        rnd = self.current()
        for pid, p in self.players.items():
            g = rnd["guesses"].get(pid)
            if g is not None:
                p["score"] += g["points"]
        rnd["revealedAt"] = time.time()
        self.phase = "reveal"

    def generate(self, match_id, count):
        used = set()
        for _ in range(count):
            try:
                loc = make_location(used)
            except KeyProblem as exc:
                with self.lock:
                    if self.match_id == match_id:
                        self.error = f"Google Street View refused the API key: {exc}"
                print("  API key problem:", exc)
                return
            with self.lock:
                if self.match_id != match_id:
                    return  # match was reset while we were looking
                if not loc:
                    self.error = ("Could not find a location. Check this computer's "
                                  "internet connection, then return to the lobby and try again.")
                    return
                self.rounds.append({"loc": loc, "guesses": {}, "startedAt": None, "endsAt": None})
                print(f"  round {len(self.rounds)} ready: {loc['place']}")
                self.tick()

    # ---- actions
    def join(self, name):
        name = (name or "").strip()[:20] or "Player"
        if len(self.online()) >= MAX_PLAYERS:
            raise ValueError(f"The game is full ({MAX_PLAYERS} players).")
        taken = {p["color"] for p in self.online().values()}
        color = next((c for c in COLORS if c not in taken), random.choice(COLORS))
        pid = uuid.uuid4().hex
        self.players[pid] = {"name": name, "color": color, "score": 0,
                             "lastSeen": time.time(), "joined": time.time()}
        # Drop long-gone players so their names don't clutter the lobby.
        for gone in [k for k, p in self.players.items() if time.time() - p["lastSeen"] > 600]:
            del self.players[gone]
        self.fix_host()
        return pid

    def require_host(self, pid):
        if pid != self.host:
            raise ValueError("Only the host can do that.")

    def update_settings(self, pid, s):
        self.require_host(pid)
        if self.phase != "lobby":
            raise ValueError("Settings can only change in the lobby.")
        if s.get("rounds") in (3, 5, 10):
            self.settings["rounds"] = s["rounds"]
        if s.get("timeLimit") in (0, 30, 60, 120, 180, 300):
            self.settings["timeLimit"] = s["timeLimit"]
        if "noMove" in s:
            self.settings["noMove"] = bool(s["noMove"])

    def start(self, pid):
        self.require_host(pid)
        if self.phase != "lobby":
            raise ValueError("A match is already running.")
        if len(self.online()) < MIN_PLAYERS:
            raise ValueError(f"You need at least {MIN_PLAYERS} players to start.")
        self.reset_match()
        self.phase = "loading"
        self.round_index = 0
        threading.Thread(target=self.generate, args=(self.match_id, self.settings["rounds"]),
                         daemon=True).start()

    def guess(self, pid, lat, lng):
        rnd = self.current()
        if self.phase != "round" or not rnd:
            raise ValueError("The round is over.")
        if pid not in rnd["players"]:
            raise ValueError("You joined mid-round. You'll play from the next round.")
        if pid in rnd["guesses"]:
            raise ValueError("You already guessed this round.")
        g = {"lat": float(lat), "lng": float(lng)}
        km = haversine_km(g, rnd["loc"])
        rnd["guesses"][pid] = {**g, "km": round(km, 3), "points": score_for(km),
                               "seconds": round(time.time() - rnd["startedAt"], 1)}

    def next_round(self, pid):
        self.require_host(pid)
        if self.phase != "reveal":
            raise ValueError("Wait for the round to finish.")
        if self.round_index + 1 >= self.settings["rounds"]:
            self.phase = "final"
            return
        self.round_index += 1
        self.phase = "loading"

    def to_lobby(self, pid):
        self.require_host(pid)
        self.reset_match()

    def leave(self, pid):
        self.players.pop(pid, None)
        self.fix_host()

    # ---- view
    def view(self, pid):
        online = self.online()
        rnd = self.current()
        show_answers = self.phase in ("reveal", "final")
        rounds_out = []
        for i, r in enumerate(self.rounds):
            if i > self.round_index:
                break
            revealed = i < self.round_index or show_answers
            item = {"index": i, "guessed": list(r["guesses"].keys()),
                    "playing": list(r.get("players", ())),
                    "startedAt": r["startedAt"], "endsAt": r["endsAt"]}
            loc = r["loc"]
            if i == self.round_index:
                # Imagery is needed while playing; coordinates stay hidden until reveal.
                item["panoId"] = loc.get("panoId")
                item["photos"] = [{"url": f"/api/photo?code={self.code}&m={self.match_id}&r={i}&n={j}"}
                                  for j in range(len(loc.get("photos", [])))]
            if revealed:
                item["answer"] = {"lat": loc["lat"], "lng": loc["lng"], "place": loc["place"]}
                item["guesses"] = r["guesses"]
                item["credits"] = [{k: v for k, v in p.items() if k not in ("data", "url")}
                                   for p in loc.get("photos", [])]
            elif pid in r["guesses"]:
                item["myGuess"] = r["guesses"][pid]
            rounds_out.append(item)
        return {
            "code": self.code,
            "you": pid if pid in self.players else None,
            "host": self.host,
            "phase": self.phase,
            "matchId": self.match_id,
            "error": self.error,
            "settings": self.settings,
            "roundIndex": self.round_index,
            "rounds": rounds_out,
            "serverTime": time.time(),
            "players": [{"id": k, "name": p["name"], "color": p["color"], "score": p["score"],
                         "online": k in online}
                        for k, p in sorted(self.players.items(), key=lambda kv: kv[1]["joined"])
                        if k in online or (rnd and k in rnd["guesses"]) or p["score"]],
        }


GAMES = {}
GAMES_LOCK = threading.Lock()


def create_game():
    with GAMES_LOCK:
        for code in [c for c, g in GAMES.items() if time.time() - g.last_active > GAME_IDLE_LIMIT]:
            del GAMES[code]
        while True:
            code = "".join(random.choice(CODE_LETTERS) for _ in range(4))
            if code not in GAMES:
                GAMES[code] = Game(code)
                return GAMES[code]


def find_game(code):
    with GAMES_LOCK:
        return GAMES.get((code or "").strip().upper())


# ---------------------------------------------------------------- http

def lan_addresses():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".webmanifest": "application/manifest+json"}


class Handler(BaseHTTPRequestHandler):
    server_version = "Pinpoint/1.0"

    def log_message(self, fmt, *args):
        pass  # keep the console readable; polling would flood it

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        if url.path == "/api/state":
            pid = (qs.get("pid") or [""])[0]
            game = find_game((qs.get("code") or [""])[0])
            if not game:
                return self.send_json({"error": "That game has ended.", "gone": True}, 404)
            with game.lock:
                if pid in game.players:
                    game.players[pid]["lastSeen"] = game.last_active = time.time()
                game.tick()
                return self.send_json(game.view(pid))
        if url.path == "/api/photo":
            game = find_game((qs.get("code") or [""])[0])
            try:
                with game.lock:
                    r, n = int(qs["r"][0]), int(qs["n"][0])
                    if r > game.round_index or qs["m"][0] != game.match_id:
                        raise ValueError
                    body = game.rounds[r]["loc"]["photos"][n]["data"]
            except Exception:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/api/config":
            return self.send_json({"mode": MODE, "apiKey": API_KEY, "baseUrls": self.base_urls()})
        path = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        full = os.path.normpath(os.path.join(STATIC, path))
        if not full.startswith(STATIC) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def base_urls(self):
        """Addresses friends can open. Behind a host like Render, that's the public URL;
        on a home computer, it's this machine's Wi-Fi address."""
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        name = host.split(":")[0]
        if name in ("localhost", "127.0.0.1", ""):
            port = self.server.server_address[1]
            return [f"http://{ip}:{port}" for ip in lan_addresses()]
        scheme = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        return [f"{scheme}://{host}"]

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send_json({"error": "Bad request."}, 400)
        pid = data.get("pid", "")
        action = urllib.parse.urlparse(self.path).path.removeprefix("/api/")
        if action == "create":
            game = create_game()
            with game.lock:
                pid = game.join(data.get("name"))
                return self.send_json({"pid": pid, "code": game.code})
        game = find_game(data.get("code"))
        if not game:
            return self.send_json({"error": "No game with that code. Check the letters with your host.", "gone": True}, 404)
        try:
            with game.lock:
                game.last_active = time.time()
                if action == "join":
                    pid = game.join(data.get("name"))
                    return self.send_json({"pid": pid, "code": game.code})
                if pid not in game.players:
                    return self.send_json({"error": "You are not in this game. Join again."}, 403)
                game.players[pid]["lastSeen"] = time.time()
                if action == "settings":
                    game.update_settings(pid, data.get("settings") or {})
                elif action == "start":
                    game.start(pid)
                elif action == "guess":
                    game.guess(pid, data["lat"], data["lng"])
                elif action == "next":
                    game.next_round(pid)
                elif action == "lobby":
                    game.to_lobby(pid)
                elif action == "leave":
                    game.leave(pid)
                else:
                    return self.send_json({"error": "Unknown action."}, 404)
                game.tick()
                return self.send_json(game.view(pid))
        except (ValueError, KeyError, TypeError) as exc:
            return self.send_json({"error": str(exc)}, 400)


def main():
    parser = argparse.ArgumentParser(description="Pinpoint party game server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("\n  PINPOINT is running")
    print(f"  Imagery: {'Google Street View' if MODE == 'streetview' else 'Wikimedia Commons photos (add a Google key in config.json for Street View)'}")
    print(f"\n  On this computer:   http://localhost:{args.port}")
    for ip in lan_addresses():
        print(f"  Friends on your Wi-Fi: http://{ip}:{args.port}")
    print("\n  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
