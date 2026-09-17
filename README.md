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
Wi‑Fi. The game speaks WebSocket, which Cloudflare Tunnel forwards natively,
so the host just needs to publish port 8082 through their existing tunnel and
the friend needs nothing extra.

**Host, one-time tunnel setup** — add a public hostname to your tunnel:

- Dashboard-managed tunnel: Zero Trust → Networks → Tunnels → your tunnel →
  *Public Hostname* → Add: subdomain `pong`, type **HTTP**,
  URL `localhost:8082` (or `<this PC's LAN IP>:8082` if the tunnel runs on
  another machine).
- Config-file tunnel: add an ingress rule above the catch-all:
  ```yaml
  - hostname: pong.yourdomain.com
    service: http://localhost:8082
  ```
  then restart cloudflared.

**Then every time:**

- Host: `python pong.py`, press ENTER.
- Friend: `python pong.py pong.yourdomain.com`

Address formats the join command accepts: an IP (`192.168.1.20`), an IP with
port (`1.2.3.4:8082`), a hostname (`pong.yourdomain.com` → uses `wss://`), or a
full `ws://` / `wss://` URL.

Other options that also work: [Tailscale](https://tailscale.com) (use the
`100.x.y.z` IP), or forwarding TCP port 8082 on the host's router.

## How it works

The host runs the ball physics and is the referee; the guest sends its
paddle position and draws what the host sends back. Each player's own paddle
moves locally, so your controls never feel laggy. Transport is JSON over a
WebSocket (`websockets` library), ~60 messages per second each way.
