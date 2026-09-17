# Live relay (safe fields only)

Deployed by Anton on Hatch, 2026-09-15 ~21:07Z. Verified from the Pi 2026-09-15 21:12Z.

| Field | Value |
|---|---|
| Worker URL | `https://muse-mailbox.REDACTED.workers.dev` |
| mailbox_id | `mb_REDACTED` |
| `/health` | `{"ok":true,"protocol":"muse-mailbox/2"}` |
| `/stats` | `{"highest_seq":0}` only |
| Unauth GET/POST messages | `401 invalid_token` |

Tokens, `ADMIN_SECRET`, and key material are **not** recorded here. They live in Hatch 0600 files (`provision.json`, `held-gauri-tokens.json`).

Hatch note: wrangler migration used `new_sqlite_classes` (free plan). Repo `workers/wrangler.toml` matched after the fact.

Gauri still gated. No Hatch hook until keys + ceremony.
