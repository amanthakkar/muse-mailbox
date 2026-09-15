# Scratch test results — 2026-09-16

Harness: local Python relay (DO+R2 semantics), no Cloudflare account, no Pi, no real keys.

Command:

```
PYTHONPATH=src python3 -m unittest tests.test_matrix -v
```

```
test_directional_tokens                             ... ok
test_idempotent_retry_and_conflict                  ... ok
test_presence_oracle                                ... ok
test_prune_does_not_reuse_seq                       ... ok
test_concurrent_posts_kill_restart_tiny_pages       ... ok
test_aad_tamper_matrix                              ... ok
test_roundtrip                                      ... ok
test_out_of_order_10_12_11                          ... ok
test_replay_no_second_dispatch                      ... ok
test_signature_tamper_quarantines                   ... ok
test_undecryptable_does_not_stall                   ... ok

Ran 11 tests in ~1.3s
OK
```

## Mapping onto PROTOCOL §9

| # | Contract test | Result | Where |
|---|---|---|---|
| 1 | 2×50 concurrent POSTs, SIGKILL+restart, `limit=7` drain, exactly-once, contiguous cursors, `fetch_cursor == highest_seq`, duplicate retry, 409 conflict | PASS | `ConcurrencyRestartTests` + idempotency test |
| 2 | sender_seq 10,12,11 (after 1..9) buffers then dispatches; cursor ends at 12; no skip/double | PASS | `test_out_of_order_10_12_11` |
| 3 | AAD tamper: `v`, `mailbox_id`, `message_id`, `from`, `to`, `eph`, one bit of `ct` | PASS | `test_aad_tamper_matrix` |
| 4 | Signature tamper → quarantine, never dispatched | PASS | `test_signature_tamper_quarantines` |
| 5 | Replay same envelope + re-wrap same `sender_seq` → no second dispatch | PASS | `test_replay_no_second_dispatch` |
| 6 | Undecryptable blob quarantined; `fetch_cursor` still advances; later message dispatches | PASS | `test_undecryptable_does_not_stall` |
| 7 | Unauthenticated `/stats`; POST moves counter | PASS | `test_presence_oracle` |

## Six substrate proof points

All PASS. See `docs/SUBSTRATE.md`.
