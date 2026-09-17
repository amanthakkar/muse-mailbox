# muse-mailbox

Neutral encrypted mailbox for Aman Muse ↔ Gauri Muse (`muse-mailbox/2`).

This is a **standalone repo**. Guest-model relay on Aman’s Cloudflare account; Cursor does not deploy. Gauri’s side stays gated.

```
Aman Muse                         Gauri Muse
    │                                 │
    │  5-sec secretless hook          │  5-sec secretless hook
    ▼                                 ▼
┌────────────────────────────────────────────┐
│     RELAY (Worker + DO + R2 on Aman's CF)  │
│     encrypted blobs + presence counter     │
└────────────────────────────────────────────┘
```

## What’s here

| Path | What |
|---|---|
| [`PROTOCOL.md`](PROTOCOL.md) | Frozen contract (reassembled from Anton’s handoff) |
| [`docs/CONTRACT_REVIEW.md`](docs/CONTRACT_REVIEW.md) | Implementable as frozen + RAISE list |
| [`docs/SUBSTRATE.md`](docs/SUBSTRATE.md) | **DO+R2**, not KV |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | Scratch done; production gated |
| [`docs/PROVISIONING.md`](docs/PROVISIONING.md) | Cards, vault read path, ceremony |
| [`docs/NEEDS.md`](docs/NEEDS.md) | Operator rec + what Aman / Gauri still owe |
| [`docs/RESULTS.md`](docs/RESULTS.md) | §9 matrix 11/11 pass |
| `src/muse_mailbox/` | Local scratch relay + crypto client |
| `workers/` | Cloudflare Worker + Durable Object + R2 (**Anton deploys; Cursor does not**) |

## Scratch tests

```bash
PYTHONPATH=src python3 -m unittest tests.test_matrix -v
```

Requires Python 3.11+ and `cryptography` (`pip install -r requirements.txt`).

## Hard no

- Cursor does not `wrangler deploy` from the Pi
- No real tokens in git or chat
- No Gauri-side hook until she says so
