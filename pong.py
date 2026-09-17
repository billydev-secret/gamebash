"""
Pong for two friends over the internet.

  python pong.py                       -> host a game (you are the LEFT paddle)
  python pong.py 192.168.1.20          -> join a friend on the same network
  python pong.py pong.example.com      -> join a friend through their Cloudflare tunnel

Controls: UP / DOWN (or W / S).  SPACE = rematch.  ESC = quit.
"""
import json
import socket
import struct
import sys
import threading
import time
import random

import pygame
from websockets.sync.client import connect as ws_connect
from websockets.sync.server import serve as ws_serve

W, H = 800, 500
PAD_W, PAD_H = 12, 80
BALL = 12
PAD_SPEED = 440        # px per second
BALL_SPEED = 380       # starting ball speed
BALL_SPEED_MAX = 900
WIN_SCORE = 7
PORT = 8082
FPS = 60

# Public hostname your Cloudflare tunnel forwards to this PC's port 8082.
TUNNEL_HOST = "pong.billy-bots.com"

WHITE = (240, 240, 240)
GREY = (110, 110, 110)
BLACK = (0, 0, 0)


# ----------------------------------------------------------------------------
# Networking: JSON messages over a WebSocket. WebSockets go straight through
# Cloudflare Tunnel / any HTTP proxy, so the guest needs no extra software.
# A reader thread keeps the most recent message; the game loop never blocks.
# ----------------------------------------------------------------------------
class Link:
    def __init__(self, ws):
        self.ws = ws
        self.latest = None
        self.alive = True
        self.closed = threading.Event()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for msg in self.ws:
                try:
                    self.latest = json.loads(msg)
                except ValueError:
                    pass
        except Exception:
            pass
        self.alive = False
        self.closed.set()

    def send(self, obj):
        if not self.alive:
            return
        try:
            self.ws.send(json.dumps(obj, separators=(",", ":")))
        except Exception:
            self.alive = False
            self.closed.set()

    def close(self):
        self.alive = False
        try:
            self.ws.close()
        except Exception:
            pass
        self.closed.set()


def to_ws_url(target):
    """'1.2.3.4' -> ws://1.2.3.4:8082, 'pong.example.com' -> wss://pong.example.com,
    full ws:// or wss:// URLs are used as-is."""
    if "://" in target:
        return target
    host = target.split(":")[0]
    has_port = ":" in target
    is_ip = host.replace(".", "").isdigit() or host == "localhost"
    if is_ip or has_port:
        return "ws://%s:%s" % (host, target.split(":")[1] if has_port else PORT)
    return "wss://%s" % host    # a domain name: assume it's behind a tunnel/HTTPS


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ----------------------------------------------------------------------------
# Sound: tiny square-wave beeps generated in code so there are no asset files.
# ----------------------------------------------------------------------------
class Sounds:
    def __init__(self):
        self.ok = False
        try:
            pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=256)
            self.hit = self._tone(440, 0.05)
            self.wall = self._tone(220, 0.05)
            self.score = self._tone(110, 0.25)
            self.ok = True
        except Exception:
            pass

    def _tone(self, freq, secs, vol=0.25):
        rate = 22050
        n = int(rate * secs)
        half = int(rate / freq / 2)
        amp = int(32767 * vol)
        samples = [amp if (i // half) % 2 == 0 else -amp for i in range(n)]
        return pygame.mixer.Sound(buffer=struct.pack("<%dh" % n, *samples))

    def play(self, name):
        if self.ok:
            getattr(self, name).play()


# ----------------------------------------------------------------------------
# Game state (the host simulates this; the guest just draws what it's sent)
# ----------------------------------------------------------------------------
def fresh_state():
    return {
        "ly": H / 2 - PAD_H / 2, "ry": H / 2 - PAD_H / 2,
        "bx": W / 2, "by": H / 2,
        "ls": 0, "rs": 0,
        "phase": "wait",      # wait | play | over
        "winner": None,
        "ev": 0,              # sound event counter
        "evn": "",
    }


class Sim:
    """Host-side physics. Only the host runs this."""

    def __init__(self):
        self.s = fresh_state()
        self.vx = self.vy = 0.0
        self.serve_at = None
        self.serve_dir = random.choice((-1, 1))

    def reset_match(self):
        s = self.s
        s["ls"] = s["rs"] = 0
        s["winner"] = None
        s["phase"] = "play"
        self.reset_ball(random.choice((-1, 1)))

    def reset_ball(self, direction):
        self.s["bx"], self.s["by"] = W / 2, H / 2
        self.vx = self.vy = 0.0
        self.serve_dir = direction
        self.serve_at = time.time() + 1.0

    def event(self, name):
        self.s["ev"] += 1
        self.s["evn"] = name

    def step(self, dt):
        s = self.s
        if s["phase"] != "play":
            return

        # Serve after the pause
        if self.serve_at is not None and time.time() >= self.serve_at:
            self.serve_at = None
            ang = random.uniform(-0.6, 0.6)
            self.vx = BALL_SPEED * self.serve_dir
            self.vy = BALL_SPEED * ang

        s["bx"] += self.vx * dt
        s["by"] += self.vy * dt

        # Walls
        if s["by"] <= 0:
            s["by"] = 0
            self.vy = abs(self.vy)
            self.event("wall")
        elif s["by"] + BALL >= H:
            s["by"] = H - BALL
            self.vy = -abs(self.vy)
            self.event("wall")

        # Paddles
        ball = pygame.Rect(s["bx"], s["by"], BALL, BALL)
        left = pygame.Rect(30, s["ly"], PAD_W, PAD_H)
        right = pygame.Rect(W - 30 - PAD_W, s["ry"], PAD_W, PAD_H)
        if self.vx < 0 and ball.colliderect(left):
            self._bounce(left, +1)
        elif self.vx > 0 and ball.colliderect(right):
            self._bounce(right, -1)

        # Scoring
        if s["bx"] + BALL < 0:
            s["rs"] += 1
            self.event("score")
            self._after_point(serve_to=-1)
        elif s["bx"] > W:
            s["ls"] += 1
            self.event("score")
            self._after_point(serve_to=+1)

    def _bounce(self, pad, direction):
        s = self.s
        # Where on the paddle did we hit? -1 (top) .. +1 (bottom)
        rel = ((s["by"] + BALL / 2) - (pad.y + PAD_H / 2)) / (PAD_H / 2)
        rel = max(-1.0, min(1.0, rel))
        speed = min(BALL_SPEED_MAX, (self.vx ** 2 + self.vy ** 2) ** 0.5 * 1.06)
        self.vx = direction * speed * max(0.5, 1 - abs(rel) * 0.6)
        self.vy = speed * rel * 0.9
        # Push ball out of the paddle so it can't get stuck
        s["bx"] = pad.right if direction > 0 else pad.left - BALL
        self.event("hit")

    def _after_point(self, serve_to):
        s = self.s
        if s["ls"] >= WIN_SCORE or s["rs"] >= WIN_SCORE:
            s["phase"] = "over"
            s["winner"] = "L" if s["ls"] > s["rs"] else "R"
            s["bx"], s["by"] = W / 2, H / 2
            self.vx = self.vy = 0
        else:
            self.reset_ball(serve_to)


# ----------------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------------
class View:
    def __init__(self, screen):
        self.screen = screen
        self.big = pygame.font.Font(None, 96)
        self.mid = pygame.font.Font(None, 40)
        self.small = pygame.font.Font(None, 26)

    def text(self, font, msg, y, color=WHITE):
        surf = font.render(msg, True, color)
        self.screen.blit(surf, surf.get_rect(center=(W / 2, y)))

    def draw(self, s, me, status_lines=()):
        scr = self.screen
        scr.fill(BLACK)

        # Center line
        for y in range(0, H, 24):
            pygame.draw.rect(scr, GREY, (W / 2 - 2, y + 4, 4, 14))

        # Scores
        ls = self.big.render(str(s["ls"]), True, WHITE)
        rs = self.big.render(str(s["rs"]), True, WHITE)
        scr.blit(ls, ls.get_rect(midtop=(W / 2 - 80, 20)))
        scr.blit(rs, rs.get_rect(midtop=(W / 2 + 80, 20)))

        # Paddles + ball
        pygame.draw.rect(scr, WHITE, (30, s["ly"], PAD_W, PAD_H))
        pygame.draw.rect(scr, WHITE, (W - 30 - PAD_W, s["ry"], PAD_W, PAD_H))
        if s["phase"] == "play":
            pygame.draw.rect(scr, WHITE, (s["bx"], s["by"], BALL, BALL))

        # "You" marker under your score
        x = W / 2 - 80 if me == "L" else W / 2 + 80
        tag = self.small.render("YOU", True, GREY)
        scr.blit(tag, tag.get_rect(center=(x, 104)))

        # Overlay messages
        y = H / 2 - 20 * len(status_lines)
        for i, (font, line) in enumerate(status_lines):
            self.text(font, line, y + i * 40)

        pygame.display.flip()


def paddle_input(keys):
    d = 0
    if keys[pygame.K_UP] or keys[pygame.K_w]:
        d -= 1
    if keys[pygame.K_DOWN] or keys[pygame.K_s]:
        d += 1
    return d


def clamp_pad(y):
    return max(0.0, min(float(H - PAD_H), y))


# ----------------------------------------------------------------------------
# Main loops
# ----------------------------------------------------------------------------
def run(link_or_none, me, host_addr=None):
    pygame.init()
    pygame.display.set_caption("PONG - " + ("host" if me == "L" else "guest"))
    screen = pygame.display.set_mode((W, H))
    clock = pygame.time.Clock()
    view = View(screen)
    sounds = Sounds()

    is_host = me == "L"
    sim = Sim() if is_host else None
    link = link_or_none            # host: filled in when the guest connects
    my_y = H / 2 - PAD_H / 2
    last_ev = 0
    state = fresh_state()
    lost_at = None

    # Host: accept a connection in the background so the window stays alive.
    listener = None
    pending = {}
    if is_host:
        def handler(ws):
            if "link" in pending or link is not None:
                ws.close()          # only one friend at a time
                return
            l = Link(ws)
            pending["link"] = l
            l.closed.wait()         # keep the connection open until it dies

        listener = ws_serve(handler, "", PORT)
        threading.Thread(target=listener.serve_forever, daemon=True).start()

    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0
        dt = min(dt, 0.05)

        space = False
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                running = False
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_ESCAPE:
                    running = False
                elif e.key == pygame.K_SPACE:
                    space = True

        # Move my own paddle locally (feels instant, no lag)
        my_y = clamp_pad(my_y + paddle_input(pygame.key.get_pressed()) * PAD_SPEED * dt)

        status = []

        if is_host:
            if link is None and "link" in pending:
                link = pending.pop("link")
                sim.reset_match()
            s = sim.s
            s["ly"] = my_y
            if link is not None:
                if link.alive:
                    msg = link.latest
                    if msg:
                        s["ry"] = clamp_pad(float(msg.get("y", s["ry"])))
                        if msg.get("space"):
                            space = True
                    if s["phase"] == "over" and space:
                        sim.reset_match()
                    sim.step(dt)
                    link.send(s)
                else:
                    lost_at = lost_at or time.time()
            state = s
            if link is None:
                status = [(view.mid, "Waiting for your friend..."),
                          (view.small, "They should run:  python pong.py %s" % host_addr),
                          (view.small, "(ESC to quit)")]
        else:
            link.send({"y": my_y, "space": space})
            if link.latest:
                state = link.latest
                state["ry"] = my_y          # my paddle: trust local position
            elif link.alive:
                status = [(view.mid, "Connecting...")]
            if not link.alive:
                lost_at = lost_at or time.time()

        # Sound events from the host's sim
        if state["ev"] != last_ev:
            last_ev = state["ev"]
            sounds.play(state["evn"])

        if lost_at:
            status = [(view.mid, "Your friend disconnected."), (view.small, "ESC to quit")]
        elif state["phase"] == "over":
            won = state["winner"] == me
            status = [(view.big, "YOU WIN" if won else "YOU LOSE"),
                      (view.small, "SPACE to play again")]

        view.draw(state, me, status)

    if link:
        link.close()
    if listener:
        listener.shutdown()
    pygame.quit()


def main():
    if len(sys.argv) > 1:
        target = sys.argv[1].strip()
    else:
        target = input("Press ENTER to host, or paste your friend's IP to join: ").strip()

    if target == "":
        ip = local_ip()
        print("Hosting on port %d." % PORT)
        print("  Same network:  python pong.py %s" % ip)
        print("  Over internet: python pong.py %s" % TUNNEL_HOST)
        run(None, "L", host_addr=TUNNEL_HOST)
    else:
        url = to_ws_url(target)
        print("Connecting to %s ..." % url)
        try:
            ws = ws_connect(url, open_timeout=10)
        except Exception as e:
            print("Could not connect: %s" % e)
            print("Make sure your friend is hosting and the address is right (see README.md).")
            sys.exit(1)
        run(Link(ws), "R")


if __name__ == "__main__":
    main()
