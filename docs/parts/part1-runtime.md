# Part 1 — Muse Runtime: Inbound/Immediate Notification Investigation

**Date:** 2026-09-15. **Method:** read-only. Read ~/docs/*, the hooks/cron/device tool schemas (loaded live), and connector/skill docs; no Pi/SSH/Cloudflare changes, no accounts created.
**Headline question:** Is there ANY legitimate outbound-only mechanism giving effectively immediate (seconds) notification of an external event?

**Short answer: No.** Every background mechanism in this runtime is polling with documented minimum cadences (5 s for hooks, ~1 min-ish for cron with ±minutes jitter). There is no inbound HTTPS endpoint, no webhook registration, no event subscription, and no push-to-agent wake path. The closest approximation to "immediate" is a 5 s hook poll of an *unauthenticated* mailbox endpoint — and hook scripts run "without secrets or connector credentials," which blocks authenticated/encrypted mailbox polling.

---

## 1. External webhooks into Muse — NOT SUPPORTED

No doc or schema exposes any inbound registration: no webhook URL, no message-injection API, no conversation-resume endpoint, no goal wake API. The closest related statements are all negative:

- `~/docs/scheduling-and-watching.md`: "The same is true for event-based automations: they also work by polling, **not live streams**." and "no process keeps polling while you wait for the user's next message" — i.e. nothing runs continuously between checks.
- `~/docs/channel-availability.md` (Message routing): "You cannot push a one-off message into a provider conversation from any other chat." and "A channel message can only be sent from that channel's own side chat."
- Independent of docs: this VM has no public IP (only 127.0.0.1 and 198.19.0.2/30 CGNAT) — nothing can reach it inbound from the internet; cloudflared quick-tunnel registration is blocked (egress proxy mangles TLS, verified twice). So even an undocumented inbound API would be unreachable.

**Classification: NOT SUPPORTED.** Cannot invoke a worker, trigger a skill, inject a message, or resume a dormant goal from an external HTTPS service.

## 2. Skills: external triggers, residency, scheduling — NOT SUPPORTED

Skills are `SKILL.md` markdown files (e.g. `/opt/hatch/skills/`, `~/workspace/skills/`) that the *agent loads during a running session*. `~/opt/hatch/skills/skill-creator/SKILL.md` defines a skill as "easy to trigger" in the sense of the agent deciding to read it in chat — there is no resident process, no event listener, no registration endpoint, and no scheduler for skills themselves. Skill activation only happens inside an already-running turn: a user message, a cron run, or a hook-worker run. Skills cannot receive external API events (no webhook mechanism exists; grep of all bundled SKILL.md files found no webhook/event-stream/subscribe capability).

**Classification: NOT SUPPORTED** (skills are passive documentation; they execute only when an agent session already exists and loads them).

## 3. Connectors: event streams / subscriptions — NOT SUPPORTED

`~/docs/connectors.md`: "Connectors give you exactly the commands in their skill documentation, nothing more." Every connector is request/response CLI-style (`hatch_gws_cli gmail …`, etc.). No connector skill documents watch/subscribe/push; grep across `/opt/hatch/skills/*/SKILL.md` found only false-positive hits ("unsubscribe" in gmail, "subscriptions" in plaid for billing). The docs also say: "If a command is not documented, it does not exist."

**Classification: NOT SUPPORTED.** No notify-on-new-data mechanism exists on any connector.

## 4. WhatsApp — no external-injection path (NOT SUPPORTED for this purpose)

`~/docs/channels/whatsapp.md`:
- "The WhatsApp Channel only carries messages between the user and Muse" (1:1); "does not support group chats — Muse cannot be added to WhatsApp groups"; "cannot read the user's WhatsApp message history and cannot send messages from the user's personal account."
- Inbound mechanism: "The daemon polls for inbound messages and runs auto-reply (inference + send)." — "Inbound messages are polled on a **1-second cadence** when the channel is paired."

The 1-second poll is the *Meta channel daemon* polling the WhatsApp provider for messages *the user typed in the WhatsApp app*. No external app can cause a message to appear in the Muse WhatsApp conversation, and nothing in the docs lets an external event notify the user's normal WhatsApp account in a way Muse consumes. The user's personal WhatsApp account is explicitly out of reach ("The Messenger companion covers Messenger only, not the user's own WhatsApp account," `~/docs/connectors.md`). Paired-phone text access (Android: "texts reach you as they arrive") covers the user's own SMS, not service webhooks.

**Classification: NOT SUPPORTED** as an external-event path. (1 s cadence exists but only for user↔Muse channel messages.)

## 5. Notifications / OS: worker-caused or worker-received push — NOT SUPPORTED (no wake path)

`~/docs/calls-texts-notifications.md`:
- "There is no way to send an ad-hoc raw push, no test push, and no way to verify delivery." Output lands in chat, which then generates an OS push to the *user's phone* — the reverse direction from what the mailbox needs.
- "Results arrive as a message in chat, and if the app isn't open, a push goes to the phone" (`~/docs/scheduling-and-watching.md`) — still user-bound, not agent-waking.
- No API exists for a worker to receive a push / be woken by an OS event. The only "wake" verb in the runtime is `wake()` in hook scripts, which launches a worker from a poll decision, not from a push.

**Classification: NOT SUPPORTED.** Nothing can push to the runtime to wake it; and with no public IP, no push provider could reach the VM anyway.

## 6. Goals/monitoring cadence: polling only — VERIFIED (and goals alone do nothing)

- `~/docs/goals.md`: "**Real background work on a goal only happens through scheduled jobs. A goal by itself does not run anything.**"
- Hooks schema (`hooks.add`): `poll_interval_secs` minimum **5**, maximum 604800, default 60. Hook description: "A hook has two parts: 1. A Bash polling script ending in `.sh`… polls frequently, filters out routine results, and wakes an agent only when something relevant happens." Script environment: "runs through the normal tracked shell executor with the same base command environment and proxy setup as an ordinary shell command, **but without secrets or connector credentials**." Guidance: "bounded fetches such as `curl --fail --silent --show-error --max-time 10 ...`". `script_timeout_secs`: min 1, max **600**, default 600. Every run must end with exactly one `silent(...)` or `wake(...)` call; `wake` launches the worker asynchronously.
- Cron schema (`cron.add`): `interval.every` pattern `^\d+\s*[smh]$` (example `55s`); no explicit schema minimum, but `~/docs/scheduling-and-watching.md` says "Timing is loose: the check fires around its scheduled time, **give or take minutes, and sometimes early**. Never promise detection 'the moment it happens.'"

So "watch this mailbox and act immediately" in practice means: a hook polling the mailbox at best every 5 s (plus fetch + worker-launch latency, ~seconds). And a real limitation for the *encrypted* mailbox design: a hook script has no secrets, so it cannot hold the mailbox decryption key or a bearer credential — authenticated mailbox polling from a hook is effectively blocked by the secretless-script design. Cron is worse: sub-minute intervals allowed by the pattern, but documented jitter of ±minutes defeats any "immediate" promise.

Blocking/wait instead of polling: **UNVERIFIED**. Nothing in the schema *forbids* a hook script from doing a long-poll (`curl --max-time 580` against a long-polling mailbox endpoint — outbound HTTPS works through the same proxy), and `script_timeout_secs` up to 600 permits it. But the docs frame hooks as "polling scripts" with "bounded fetches," and no long-poll/wait pattern is documented or supported. Long-polling also still requires credentials the script cannot hold. Treat as not a supported design element.

## Classification table

| Capability | Classification | One-line evidence |
|---|---|---|
| External HTTPS invokes a worker / skill / message injection / goal resume | NOT SUPPORTED | No inbound API anywhere in docs or schemas; `channel-availability.md`: "You cannot push a one-off message into a provider conversation from any other chat." |
| Hook with `poll_interval_secs` = 5 | VERIFIED | Schema: `"minimum": 5, "maximum": 604800` on `poll_interval_secs`; hooks description: "polling script… polls frequently… wakes an agent only when something relevant happens." |
| Hook script blocking/long-poll waiting | UNVERIFIED | Schema allows `script_timeout_secs` up to 600 and outbound HTTPS, but docs only describe "bounded fetches" polling; no wait/block pattern documented. |
| Authenticated mailbox polling from a hook script | NOT SUPPORTED | Hooks description: script "runs… **without secrets or connector credentials**" — no decryption key/bearer token available. |
| Cron sub-minute interval | PARTIALLY VERIFIED | Schema `every` accepts `^\d+\s*[smh]$` (example `55s`), but `scheduling-and-watching.md`: "Timing is loose… give or take minutes" — not seconds-reliable. |
| Skills triggered by external events / resident skills | NOT SUPPORTED | Skills are `SKILL.md` files loaded by an agent in a running session; no listener/registration mechanism; no skill documents event receipt. |
| Skills scheduled by runtime | NOT SUPPORTED | Scheduling attaches to cron jobs/hook workers, which *invoke* agents that then load skills. |
| Connectors: event streams, subscriptions, notify-on-new-data | NOT SUPPORTED | `connectors.md`: "Only commands documented… are available. If a command is not documented, it does not exist." Grep of all SKILL.md found no such mechanism. |
| WhatsApp: external app causes a message in Muse's WhatsApp chat | NOT SUPPORTED | `whatsapp.md`: "only carries messages between the user and Muse"; 1:1 only; no group access; no reading user's own WhatsApp. |
| WhatsApp: external event notifies user's normal WhatsApp in a Muse-consumable way | NOT SUPPORTED | No access to the user's personal WhatsApp account; channel is Meta-hosted 1:1 only. |
| WhatsApp 1-second inbound cadence | VERIFIED | `whatsapp.md`: "Inbound messages are polled on a 1-second cadence when the channel is paired." — but only for user↔Muse channel messages. |
| Worker causes/receives a push that wakes the runtime | NOT SUPPORTED | `calls-texts-notifications.md`: "There is no way to send an ad-hoc raw push, no test push"; pushes go to the user's phone only; no agent-wake path. |
| Goal "watches" by itself | NOT SUPPORTED | `goals.md`: "A goal by itself does not run anything." |
| Goal monitoring actual cadence | VERIFIED (polling) | `scheduling-and-watching.md`: "How it works: **polling, not watching**… The same is true for event-based automations: they also work by polling, **not live streams**." |
| Inbound connectivity to this VM from the internet | NOT SUPPORTED (empirically) | No public IP (127.0.0.1 + 198.19.0.2/30 only); cloudflared quick-tunnel blocked (verified twice). Only inbound-ish path is Pi-initiated bridge. |
| Outbound HTTPS from background jobs (hook scripts) | VERIFIED | Hooks description: script runs with "the same base command environment and proxy setup as an ordinary shell command" — outbound egress works. |

## Implication for the neutral mailbox design

The mailbox must be designed **pull-only from the Muse side**: the fastest legitimate check is a 5 s hook poll of an unauthenticated (or publicly-checkable, e.g. "new-message exists" oracle) mailbox endpoint, then the woken worker pulls and decrypts via its own (approved) session. An *encrypted* mailbox requiring a key for the existence-check cannot be polled by a hook script at all (no secrets in hook env) — so either the mailbox exposes an unauthenticated presence oracle, or latency degrades to cron/hook-worker-held-key territory (minutes). No outbound-only trick gets to "seconds" for the authenticated case.
