# Part 2 — Network experiments: outbound-only push viability

Empirical tests run 2026-09-15 from the Hatch cloud VM (UTC times).
Prior established facts (not re-tested): VM has no public IP (127.0.0.1 + 198.19.0.2/30 CGNAT);
outbound HTTPS works through an authenticated egress proxy; cloudflared quick-tunnel
registration fails (proxy mangles the TLS handshake); localhost port binding works.

All experiments used only outbound connections. Tiny throwaway scripts only;
no Pi touched, no SSH, no config changed. (Proxy auth material was used transiently
from the environment for the hand-rolled WebSocket client and is redacted from this report.)

---

## Experiment 1 — Long-lived outbound HTTPS

### 1a. httpbin delay/90 (planned test)

Command (sanity first, then the real test):
```
$ curl -sS -o /dev/null -w 'time_total=%{time_total}s http_code=%{http_code}\n' --max-time 30 https://httpbin.org/delay/3
time_total=7.027996s http_code=200          # /delay/3 works; ~4s proxy overhead

$ curl -sS -o /dev/null -w 'time_total=%{time_total}s http_code=%{http_code} exit=%{exitcode}\n' --max-time 120 https://httpbin.org/delay/90
start 16:28:09
time_total=13.896691s http_code=200 exit=0
curl exit code: 0
end 16:28:23
```

**Caveat (important):** httpbin.org's `/delay/{n}` endpoint caps server-side delay at
**10 seconds**. The 90 s request therefore only proved a ~14 s connection, not a 90 s one.
This experiment as designed is inconclusive about >60 s connections — see 1b/1c for the
real long-hold tests.

### 1b. httpbin drip (110 s idle-ish hold)

Command:
```
$ curl -sS -o /dev/null -w 'time_total=%{time_total}s http_code=%{http_code} size=%{size_download}\n' \
    --max-time 130 "https://httpbin.org/drip?duration=110&numbytes=10&delay=0&code=200"
drip_start=16:29:42
time_total=100.998514s http_code=200 size=10
curl exit: 0
drip_end=16:31:23
```

Result: a ~101 s **near-idle** outbound HTTPS connection (only 10 bytes total,
i.e. no keep-alive traffic to speak of) survived through the proxy with a clean
HTTP 200. (Requested 110 s; observed 101 s — likely httpbin-side timing/capping,
still a >100 s idle hold.)

### 1c. Public SSE stream held open (streaming hold)

Command:
```
$ timeout 25 curl -sS -N --max-time 20 -A "network-test/1.0" https://stream.wikimedia.org/v2/stream/recentchange > wikimedia.out
start 16:28:45
exit: 28
curl: (28) Operation timed out after 20001 milliseconds with 1191795 bytes received
end 16:29:05
```
- 761 SSE `data:` events received in 20 s (~1.2 MB) — continuous streaming, no proxy interference.
- Exit 28 is curl's own `--max-time` cutoff firing as designed; the stream was healthy at cutoff.

---

## Experiment 2 — SSE client capability (localhost)

Tiny Python `http.server` on 127.0.0.1, `Content-Type: text/event-stream`,
3 events 2 s apart then closes.

Command:
```
$ curl -sS -N --max-time 30 http://127.0.0.1:33713/events -w '\ntime_total=%{time_total}s http_code=%{http_code}\n'
start 16:28:16
event: test
data: event-1 at 1789489696.36

event: test
data: event-2 at 1789489698.36

event: test
data: event-3 at 1789489700.36

event: done
data: stream-end

time_total=6.001811s http_code=200
curl exit: 0
end 16:28:22
```

Result: `curl -N` (and by extension any stdlib HTTP client) consumes SSE streams
correctly — events arrive incrementally, stream close detected.

---

## Experiment 3 — Public SSE endpoint (outbound)

- `https://sse.dev/test` (cited in http4s/http4s#6694): **dead** —
  `curl: (52) Empty reply from server` (DNS resolves, server returns nothing).
- `https://stream.wikimedia.org/v2/stream/recentchange` (confirmed live by multiple
  independent sources, e.g. github.com/gadallah7011/end-to-end-wikidata-streaming-pipeline,
  MediaWiki docs): **works** — 761 events / 1.2 MB in 20 s (see 1c).

---

## Experiment 4 — HTTP long-polling viability

A mailbox long-poll design needs the server to hold the GET open until a message
arrives or a heartbeat timeout fires. Evidence:

1. **Idle hold (1b):** a ~101 s near-idle HTTPS connection (10 bytes total) survived
   through the proxy with HTTP 200 — this is the shape of a long-poll wait.
2. **Streaming hold (1c):** chunked HTTPS response held open and flowing for 20 s,
   761 events / 1.2 MB — long-polling is a slower variant of this shape.
3. **Background python holder (6b):** 60.5 s outbound HTTPS held from a Python process,
   10,903 lines received.

No proxy behavior observed that would break long-polling. A server-side heartbeat
every ~30–45 s would keep well clear of the verified 101 s ceiling.

---

## Experiment 5 — WebSocket

VM client inventory (before any test):
```
$ python3 -c "import websockets"   -> ModuleNotFoundError: No module named 'websockets'
$ python3 -c "import websocket"    -> ModuleNotFoundError: No module named 'websocket'
$ which wscat                       -> (not found)
$ curl --version                     -> curl 8.5.0
$ echo test | curl -N wss://echo.websocket.org -> curl: (1) Protocol "wss" not supported or disabled in libcurl
```
No pre-installed WebSocket client exists. Per the "do not fake it" rule, a minimal
RFC 6455 text-frame client was hand-written in stdlib Python (CONNECT via the egress
proxy from env, TLS wrap, HTTP upgrade, masked text frame) and pointed at the public
echo server `wss://echo.websocket.org` (Ably; documented at websocket.org/tools/websocket-echo-server;
sends a banner frame first, then echoes).

Test 1 (raw frames):
```
CONNECT: HTTP/1.1 200 Connection Established
UPGRADE: HTTP/1.1 101 Switching Protocols
ECHO opcode=1 payload=b'Request served by 4d896d95b55478' roundtrip=0.00s   # banner frame
```

Test 2 (loop frames until own payload returns):
```
ECHO RECEIVED after 2 frames, roundtrip=0.11s
RESULT: VERIFIED-ECHO
```

Result: full WebSocket round-trip works through the egress proxy:
CONNECT → TLS → 101 upgrade → masked text frame → echo in 0.11 s.

---

## Experiment 6 — Background process survival

### 6a. `sleep 300`
Launched via exec background at 16:29:08 UTC:
```
$ (background) echo "sleep300_start=$(date -u +%T) pid=$$"; sleep 300; echo "sleep300_end=$(date -u +%T) SURVIVED"
sleep300_start=16:29:08 pid=11166
sleep300_end=16:34:08 SURVIVED
```
- Alive at 16:29:44 (39 s in) and 16:32:44 (3:36 in) — verified via `ps`.
- Completed the full 300 s normally at 16:34:08.

### 6b. Background Python holding an outbound HTTPS connection (60 s)
```
$ (background) python3 - << 'EOF'
import urllib.request, time
t0 = time.time(); n = 0
req = urllib.request.Request("https://stream.wikimedia.org/v2/stream/recentchange",
                             headers={"User-Agent": "network-test/1.0"})
with urllib.request.urlopen(req, timeout=30) as r:
    for line in r:
        n += 1
        if time.time() - t0 > 60: break
print(f"pyholder: survived {time.time()-t0:.1f}s, received {n} lines")
EOF
pyholder_start=16:29:42 pid=11279
pyholder: survived 60.5s, received 10903 lines
pyholder_end=16:30:43
```
Result: a background Python process held an outbound HTTPS streaming connection for
60.5 s and received 10,903 SSE lines without interruption.

---

## Experiment 7 — Timeouts (practical limits)

- **httpbin /delay cap:** 10 s server-side (their limit, not ours).
- **Proxy overhead on short requests:** ~4 s added to a 3 s-delayed request (7.03 s total for delay/3).
- **Longest verified idle-ish hold:** 101.0 s (httpbin drip, 10 bytes total, HTTP 200).
- **Longest verified streaming hold:** 60.5 s from a Python process (10,903 lines); 20 s via curl (1.2 MB).
- **No proxy idle timeout observed** at ~101 s. Recommend server-side heartbeat frames
  every ~30–45 s in the mailbox design for margin, though nothing suggests the proxy
  kills connections before at least ~100 s.

---

## Classification

| Capability | Verdict | Evidence |
|---|---|---|
| Long-lived outbound HTTPS connections | **VERIFIED** | ~101 s near-idle hold (httpbin drip, 10 bytes, HTTP 200); 60.5 s streaming hold from Python; 20 s streaming hold via curl. (delay/90 invalidated by httpbin's 10 s /delay cap — 13.9 s measured) |
| SSE consumption | **VERIFIED** | localhost SSE consumed incrementally by curl (3 events, clean close); public Wikimedia SSE stream: 761 events / 1.2 MB in 20 s |
| HTTP long-polling viability | **VERIFIED** | idle-hold shape proven at ~101 s through proxy; long-poll == slow streaming |
| WebSocket support | **VERIFIED** (via hand-rolled stdlib RFC 6455 client) | CONNECT 200 → TLS → 101 upgrade → text echo in 0.11 s against wss://echo.websocket.org; no stock client (no websockets lib, no wscat, curl lacks wss) |
| Background worker survival over minutes | **VERIFIED** | sleep 300 ran full 5 min (alive at 3:36, completed 16:34:08); background Python held outbound HTTPS 60.5 s |
| Practical timeout limits | **VERIFIED** | ~4 s proxy overhead on short requests; no timeout at ~101 s idle-ish; nothing observed kills connections before ~100 s |

---

## Verdict

**Outbound-only "effectively push" is viable from this VM.** Long-polling over HTTPS,
SSE, and even WebSocket all traverse the egress proxy with no observed timeout before
~100 s, and background processes survive minutes while holding connections. Recommend:
server-side heartbeat every ~30–45 s (well inside the verified 101 s ceiling) and a
reconnect-with-backoff loop for the rare drop. WebSocket works but requires shipping
a client implementation — no stock `websockets` module, `wscat`, or curl wss support
exists on the VM, so SSE-over-HTTPS long-poll is the path of least resistance.
