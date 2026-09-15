/**
 * Production-shaped Worker (NOT DEPLOYED).
 *
 * Mapping to the scratch harness:
 *   Worker fetch router     → muse_mailbox.relay.Relay.handle
 *   Durable Object (one/mb) → mailbox lock + SQLite mailboxes/idempotency/messages
 *   R2                      → blobs table
 *
 * Proof points 1–3 require the DO input gate (single-threaded per mailbox).
 * KV cannot provide them (see docs/SUBSTRATE.md).
 */

export interface Env {
  MAILBOX: DurableObjectNamespace;
  BLOBS: R2Bucket;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length !== 4 || parts[0] !== "v1" || parts[1] !== "mailboxes") {
      return json({ error: "mailbox_not_found", detail: "no such route" }, 404);
    }
    const mailboxId = parts[2];
    const stub = env.MAILBOX.get(env.MAILBOX.idFromName(mailboxId));
    return stub.fetch(request);
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export { MailboxDO } from "./mailbox-do";
