/**
 * Production Worker router. Blind relay — no plaintext, no keys.
 * One Durable Object (idFromName "muse-mailbox/2") holds the single mailbox.
 * Unknown /stats ids return {highest_seq:0} without minting extra DOs.
 */

export interface Env {
  MAILBOX: DurableObjectNamespace;
  BLOBS: R2Bucket;
  ADMIN_SECRET: string;
}

export { MailboxDO } from "./mailbox-do";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "GET" && path === "/health") {
      return json({ ok: true, protocol: "muse-mailbox/2" });
    }

    const stub = env.MAILBOX.get(env.MAILBOX.idFromName("muse-mailbox/2"));

    if (request.method === "POST" && path === "/v1/admin/provision") {
      const ok = await adminOk(request, env);
      if (!ok) return json({ error: "invalid_token", detail: "admin" }, 401);
      return stub.fetch(new Request("https://do/v1/admin/provision", request));
    }

    const parts = path.split("/").filter(Boolean);
    if (parts.length === 4 && parts[0] === "v1" && parts[1] === "mailboxes") {
      return stub.fetch(request);
    }
    return json({ error: "mailbox_not_found", detail: "no such route" }, 404);
  },
};

async function adminOk(request: Request, env: Env): Promise<boolean> {
  const want = env.ADMIN_SECRET;
  if (!want) return false;
  const header = request.headers.get("Authorization") || "";
  const got = header.toLowerCase().startsWith("bearer ")
    ? header.slice(7).trim()
    : "";
  if (!got || got.length !== want.length) return false;
  const a = new TextEncoder().encode(got);
  const b = new TextEncoder().encode(want);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}
