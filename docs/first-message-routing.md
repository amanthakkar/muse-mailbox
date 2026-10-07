# First-message routing — experiment and chosen rule

**Status:** experiment validated on fixtures; not yet merged into the live
client. Merging needs the operator's go-ahead (it changes live routing
behavior).

## The problem

When a participant starts a **new** conversation, the first message carries no
threading signals (`in_reply_to`, `to_channel` are both absent). The receiving
side's agent must pick one of its chats to surface it in — and it has no
principled basis for the choice. The old behavior was: fall through to the
sticky `route.json` origin (wherever *I* last sent) or the hook's fallback
(the side chat that happened to originate the hook). Both are accidents of
local state, not decisions. Aman's phrasing: *"you don't know the destination
chat on their end, right?"* — exactly.

## The chosen rule

**A new thread's first message always lands in the recipient's MAIN thread,
with a standard offer-to-move.** The receiving agent surfaces the message in
main plus a one-line affordance:

> New thread — I've put it here in the main thread. Say 'move to \<chat>'
> anytime and I'll shift it there; follow-ups will route to wherever we keep it.

The human's choice is recorded (`thread_map.json`: thread-root message_id →
chosen chat), so follow-ups route correctly. The rule is symmetric — both
sides configure their own `main_chat_id`, and neither side needs to know the
other's chat topology.

Why main, not "best guess": any guess is wrong as often as it is right, and
a wrong guess in a side chat is *invisible* (the human never looks there),
while a message in main is always seen. The offer-to-move makes the default
cheap to correct.

## Protocol surface (v2.3 candidates, backward compatible)

No freeze-breaking changes. Receivers already ignore unknown envelope
fields; the additions are optional:

- `thread_start: true` — sender marks an explicit thread start. Without it,
  a new thread is detected as "no `in_reply_to`, no `to_channel`, not
  ack/marker". Clients MUST thread replies (`in_reply_to`); an unthreaded
  message is treated as a new thread by definition.
- `thread_hint: "<free text>"` — optional, non-authoritative topic label
  from the sender (e.g. `"taxes"`). The receiver may quote it in the
  offer text ("you flagged it 'taxes'") to help the human decide where the
  thread belongs. It never routes by itself.

## Client changes (prototyped, not merged)

In `client/mailbox_routing.py` (prototype at `/tmp/routing_exp/` during the
experiment):

- `main_chat_id()` — from `~/.muse-mailbox/config.json` (`main_chat_id`)
  or `MUSE_MAILBOX_MAIN_CHAT`. Each side configures its own.
- `is_new_thread(payload)` — explicit flag, or no threading signals and not
  ack/marker.
- `resolve_chat()` gains the new branch and now returns
  `(chat_id, reason, offer_move)`. New order:
  1. `to_channel` (explicit per-message destination — still wins),
  2. `in_reply_to` → sent.json entry; the thread's recorded home
     (`thread_map.json`) wins if the human moved it, else the entry's chat,
  3. **new thread → main chat + `offer_move=True`**,
  4. `marker` control messages → main chat, no offer,
  5. `route.json` sticky origin (legacy),
  6. fallback `(None, "fallback")`.
- `record_thread_home(root_mid, chat_id)` — persist the human's choice.
- `record_thread_root(message_id, in_reply_to, thread_start)` — outbound
  helper stamping `thread_root` on sent.json entries so inbound follow-ups
  resolve the thread's home.

Deliberate conservatism: an `in_reply_to` pointing at an *unknown* sent
message falls through to sticky rather than being claimed as a new thread —
a mislabeled reply is more likely than a mislabeled thread start.

## Experiment

Dry runs against recorded fixtures (`MUSE_MAILBOX_HOME` pointed at a temp
dir; no live state touched). All 15 checks pass:

| # | Case | Result |
|---|---|---|
| 1 | new thread, no signals | main + offer |
| 2 | explicit `thread_start: true` | main + offer |
| 3 | new thread with `thread_hint: "taxes"` | main + offer quoting the hint |
| 4 | explicit `to_channel` | wins over new-thread rule |
| 5 | reply in a moved thread | thread_map home |
| 6 | reply in an unmoved thread | sent entry's chat |
| 7 | ack | never surfaced |
| 8 | marker/out | main, no offer |
| 9 | unknown `in_reply_to` | sticky (conservative) |
| 10 | empty payload | main + offer |
| 11–12 | offer text with/without hint | correct |
| 13 | `record_thread_home` round-trip | persisted |
| 14–15 | `record_thread_root` inheritance | reply inherits root; start becomes root |

## Behavior changes to be aware of (on merge)

- A new thread arriving while sticky points at a side chat now goes to
  **main** instead of that side chat. This is the intended fix, but it is a
  visible change.
- `resolve_chat()`'s return shape changes from 2-tuple to 3-tuple; all
  callers (hook worker, safety net, daemon ack routing) must be updated
  together.
- Each side must set `main_chat_id` in config; without it the new branch is
  skipped and behavior is unchanged (safe default).

## Open questions

1. Should the offer-to-move expire (e.g. only offered on the first
   message, not repeated)?
2. When the human moves a thread, should the agent forward the earlier
   messages or just continue from the new chat? (Current answer: continue;
   history stays where it was.)
3. Cross-runtime: does the peer's runtime have a "main thread" concept, or
   does it need a different default?

## Peer input (Iko)

*Pending — consult sent 2026-10-06 (relay seq 472). To be filled in from her
reply: how her runtime handles inbound first messages with no known
destination chat today; what would make onboarding seamless for a
non-technical second human in her setup; any objection to the
main-thread-default + offer-to-move rule.*
