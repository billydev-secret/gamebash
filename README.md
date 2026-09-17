# Pong — two players, online

One file, two buttons. First to 7 wins.

## Setup (both of you, once)

```
pip install pygame-ce websockets
```

Both players need `pong.py` (send your friend the file).

## Play

**Person 1 (host):**
```
python pong.py
```
Press ENTER at the prompt. The window shows the IP your friend should use.

**Person 2 (join):**
```
python pong.py <that IP>
```

Controls: **UP / DOWN** (W / S also work). SPACE to rematch. ESC to quit.

## Playing over the internet (Cloudflare Tunnel)

The IP the host prints is their *local* IP, which only works on the same
Wi-Fi. For the internet, the game is published through an existing Cloudflare
tunnel as **pong.billy-bots.com**. The game speaks WebSocket, which the tunnel
forwards natively, so the friend needs nothing extra.

**Setup (already done):** the tunnel's public hostname `pong` is type HTTP
with URL `http://192.168.174.133:8082` - this PC's LAN address, because the
tunnel runs on another machine on the network. If this PC's IP ever changes,
update that URL (or give the PC a fixed IP in the UniFi controller).

**Then every time:**

- Host (this PC): `python pong.py`, press ENTER.
- Friend: `python pong.py pong.billy-bots.com`

Address formats the join command accepts: an IP (`192.168.1.20`), an IP with
port (`1.2.3.4:8082`), a hostname (`pong.billy-bots.com` -> uses `wss://`), or
a full `ws://` / `wss://` URL.

Troubleshooting: if the friend sees "Could not connect", check the host has the
game open (the tunnel only reaches the game while it's running), and that the
tunnel rule says `http://`, not `https://`.

## How it works

The host runs the ball physics and is the referee; the guest sends its
paddle position and draws what the host sends back. Each player's own paddle
moves locally, so your controls never feel laggy. Transport is JSON over a
WebSocket (`websockets` library), ~60 messages per second each way.
