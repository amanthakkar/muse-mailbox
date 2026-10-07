/**
 * Per-account singleton Durable Object: seq, dedupe, auth, presence.
 * Ciphertext lives in R2. Write order: R2 PUT (key = mailbox/mid/{message_id})
 * THEN DO storage assigns seq. Never the reverse.
 */

export interface Env {
  MAILBOX: DurableObjectNamespace;
  BLOBS: R2Bucket;
  ADMIN_SECRET: string;
}

const MAX_BODY = 256 * 1024;
const MAX_CT = 180 * 1024;
const SEND_RATE = 60;
const FETCH_RATE = 300;
const STALE_SECONDS = 300;
const PAGE_BYTES = 1024 * 1024;
const PREFIX: Record<string, string> = {
  "aman:send": "mm1_as_",
  "aman:fetch": "mm1_af_",
  "gauri:send": "mm1_gs_",
  "gauri:fetch": "mm1_gf_",
};

type RelayErr = { status: number; error: string; detail: string };

class RErr extends Error {
  constructor(public readonly err: RelayErr) {
    super(err.detail);
  }
}

interface Meta {
  mailbox_id: string;
  highest_seq: number;
  created_at: string;
}

interface TokenRow {
  participant: string;
  direction: string;
}

interface IdemRow {
  seq: number;
  payload_hash: string;
}

interface MsgRow {
  seq: number;
  from: string;
  to: string;
  message_id: string;
  payload_hash: string;
  sent_at: string;
  stored_at: string;
  eph: string;
  nonce: string;
}

export class MailboxDO {
  constructor(
    private readonly state: DurableObjectState,
    private readonly env: Env,
  ) {}

  async fetch(request: Request): Promise<Response> {
    return this.state.blockConcurrencyWhile(() => this.route(request));
  }

  private async route(request: Request): Promise<Response> {
    try {
      const url = new URL(request.url);
      const path = url.pathname.replace(/\/+$/, "") || "/";
      if (request.method === "POST" && path.endsWith("/v1/admin/provision")) {
        return json(await this.provision());
      }
      const parts = path.split("/").filter(Boolean);
      if (parts.length !== 4 || parts[0] !== "v1" || parts[1] !== "mailboxes") {
        throw new RErr({ status: 404, error: "mailbox_not_found", detail: "no such route" });
      }
      const mailboxId = parts[2];
      const action = parts[3];
      if (action === "stats" && request.method === "GET") {
        return json(await this.stats(mailboxId));
      }
      if (action === "messages" && request.method === "POST") {
        const raw = await request.arrayBuffer();
        return json(
          await this.submit(mailboxId, request.headers.get("Authorization"), new Uint8Array(raw)),
        );
      }
      if (action === "messages" && request.method === "GET") {
        const since = Number(url.searchParams.get("since") || "0");
        const limit = Number(url.searchParams.get("limit") || "50");
        return json(
          await this.fetchMessages(
            mailboxId,
            request.headers.get("Authorization"),
            since,
            limit,
          ),
        );
      }
      throw new RErr({ status: 404, error: "mailbox_not_found", detail: "no such route" });
    } catch (e) {
      if (e instanceof RErr) {
        const headers: Record<string, string> = { "content-type": "application/json" };
        if (e.err.status === 429) headers["retry-after"] = "1";
        return new Response(JSON.stringify({ error: e.err.error, detail: e.err.detail }), {
          status: e.err.status,
          headers,
        });
      }
      return json({ error: "internal_error", detail: "safe to retry" }, 500);
    }
  }

  private async provision(): Promise<Record<string, unknown>> {
    const existing = await this.state.storage.get<Meta>("meta");
    if (existing) {
      throw new RErr({
        status: 409,
        error: "idempotency_conflict",
        detail: "already provisioned",
      });
    }
    const mailboxId = "mb_" + hex(randomBytes(16));
    const tokens: Record<string, string> = {};
    for (const participant of ["aman", "gauri"] as const) {
      for (const direction of ["send", "fetch"] as const) {
        const tok = PREFIX[`${participant}:${direction}`] + hex(randomBytes(32));
        tokens[`${participant}_${direction}`] = tok;
        const h = await sha256hex(tok);
        await this.state.storage.put<TokenRow>(`token:${h}`, { participant, direction });
      }
    }
    const meta: Meta = {
      mailbox_id: mailboxId,
      highest_seq: 0,
      created_at: rfc3339(new Date()),
    };
    await this.state.storage.put("meta", meta);
    await this.state.storage.setAlarm(Date.now() + 6 * 3600 * 1000);
    return { mailbox_id: mailboxId, tokens };
  }

  async alarm(): Promise<void> {
    await this.prune();
    await this.state.storage.setAlarm(Date.now() + 6 * 3600 * 1000);
  }

  private async stats(mailboxId: string): Promise<{ highest_seq: number }> {
    const meta = await this.state.storage.get<Meta>("meta");
    if (!meta || meta.mailbox_id !== mailboxId) return { highest_seq: 0 };
    return { highest_seq: meta.highest_seq };
  }

  private async auth(
    mailboxId: string,
    header: string | null,
    direction: string,
  ): Promise<TokenRow & { hash: string }> {
    const token = bearer(header);
    if (!token) {
      throw new RErr({ status: 401, error: "invalid_token", detail: "missing bearer" });
    }
    const hash = await sha256hex(token);
    const row = await this.state.storage.get<TokenRow>(`token:${hash}`);
    const meta = await this.state.storage.get<Meta>("meta");
    if (!row) {
      throw new RErr({ status: 401, error: "invalid_token", detail: "unknown token" });
    }
    if (!meta || meta.mailbox_id !== mailboxId) {
      throw new RErr({ status: 404, error: "mailbox_not_found", detail: "mismatch" });
    }
    if (row.direction !== direction) {
      throw new RErr({ status: 403, error: "forbidden", detail: "wrong direction" });
    }
    return { ...row, hash };
  }

  private async rate(hash: string, limit: number): Promise<void> {
    const key = `rate:${hash}`;
    const now = Date.now();
    const hits = ((await this.state.storage.get<number[]>(key)) || []).filter(
      (t) => now - t < 60_000,
    );
    if (hits.length >= limit) {
      throw new RErr({ status: 429, error: "rate_limited", detail: "slow down" });
    }
    hits.push(now);
    await this.state.storage.put(key, hits);
  }

  private async submit(
    mailboxId: string,
    header: string | null,
    body: Uint8Array,
  ): Promise<Record<string, unknown>> {
    if (body.byteLength > MAX_BODY) {
      throw new RErr({ status: 413, error: "payload_too_large", detail: "body exceeds 256 KiB" });
    }
    const binding = await this.auth(mailboxId, header, "send");
    await this.rate(binding.hash, SEND_RATE);
    let env: Record<string, unknown>;
    try {
      env = JSON.parse(new TextDecoder().decode(body)) as Record<string, unknown>;
    } catch {
      throw new RErr({ status: 400, error: "invalid_request", detail: "malformed json" });
    }
    if (typeof env !== "object" || env === null) {
      throw new RErr({ status: 400, error: "invalid_request", detail: "object required" });
    }
    for (const k of ["v", "from", "to", "message_id", "sent_at", "eph", "nonce", "ct"]) {
      if (!(k in env)) {
        throw new RErr({ status: 400, error: "invalid_request", detail: "missing field" });
      }
    }
    if (env.v !== 2) {
      throw new RErr({ status: 400, error: "invalid_request", detail: "v must be 2" });
    }
    const fromId = String(env.from);
    const toId = String(env.to);
    if (fromId !== binding.participant) {
      throw new RErr({ status: 403, error: "forbidden", detail: "from does not match token" });
    }
    const peer = fromId === "aman" ? "gauri" : "aman";
    if (toId !== peer) {
      throw new RErr({ status: 403, error: "forbidden", detail: "to does not match counterparty" });
    }
    let eph: Uint8Array;
    let nonce: Uint8Array;
    let ct: Uint8Array;
    try {
      eph = b64decode(String(env.eph));
      nonce = b64decode(String(env.nonce));
      ct = b64decode(String(env.ct));
    } catch {
      throw new RErr({ status: 400, error: "invalid_request", detail: "invalid base64" });
    }
    if (eph.length !== 32 || nonce.length !== 12) {
      throw new RErr({ status: 400, error: "invalid_request", detail: "eph/nonce length" });
    }
    if (ct.length > MAX_CT) {
      throw new RErr({ status: 413, error: "payload_too_large", detail: "ct exceeds 180 KiB" });
    }
    const messageId = String(env.message_id);
    const pHash = await sha256hex(
      `{"ct":${jsonStr(env.ct)},"eph":${jsonStr(env.eph)},"from":${jsonStr(fromId)},"message_id":${jsonStr(messageId)},"nonce":${jsonStr(env.nonce)},"to":${jsonStr(toId)},"v":2}`,
    );
    const idemKey = `idem:${messageId}`;
    const existing = await this.state.storage.get<IdemRow>(idemKey);
    if (existing) {
      if (existing.payload_hash === pHash) {
        return {
          seq: existing.seq,
          message_id: messageId,
          duplicate: true,
          accepted_at: rfc3339(new Date()),
        };
      }
      throw new RErr({
        status: 409,
        error: "idempotency_conflict",
        detail: "message_id reused with different payload",
      });
    }
    const sentAt = parseRfc3339(String(env.sent_at));
    if (!sentAt) {
      throw new RErr({ status: 400, error: "invalid_request", detail: "bad sent_at" });
    }
    if (Math.abs(Date.now() - sentAt.getTime()) / 1000 > STALE_SECONDS) {
      throw new RErr({ status: 422, error: "stale_timestamp", detail: "sent_at skew > 300s" });
    }
    const meta = await this.state.storage.get<Meta>("meta");
    if (!meta || meta.mailbox_id !== mailboxId) {
      throw new RErr({ status: 404, error: "mailbox_not_found", detail: "mismatch" });
    }
    const r2Key = `${mailboxId}/mid/${messageId}`;
    await this.env.BLOBS.put(r2Key, String(env.ct));
    const seq = meta.highest_seq + 1;
    const storedAt = rfc3339(new Date());
    const row: MsgRow = {
      seq,
      from: fromId,
      to: toId,
      message_id: messageId,
      payload_hash: pHash,
      sent_at: String(env.sent_at),
      stored_at: storedAt,
      eph: String(env.eph),
      nonce: String(env.nonce),
    };
    meta.highest_seq = seq;
    await this.state.storage.put(`msg:${seq}`, row);
    await this.state.storage.put(idemKey, { seq, payload_hash: pHash } satisfies IdemRow);
    await this.state.storage.put("meta", meta);
    return {
      seq,
      message_id: messageId,
      duplicate: false,
      accepted_at: storedAt,
    };
  }

  private async fetchMessages(
    mailboxId: string,
    header: string | null,
    since: number,
    limit: number,
  ): Promise<Record<string, unknown>> {
    const binding = await this.auth(mailboxId, header, "fetch");
    await this.rate(binding.hash, FETCH_RATE);
    if (!Number.isFinite(since) || since < 0) {
      throw new RErr({ status: 400, error: "invalid_request", detail: "since" });
    }
    if (!Number.isFinite(limit) || limit < 1) {
      throw new RErr({ status: 400, error: "invalid_request", detail: "limit" });
    }
    if (limit > 200) limit = 200;
    const meta = await this.state.storage.get<Meta>("meta");
    if (!meta || meta.mailbox_id !== mailboxId) {
      throw new RErr({ status: 404, error: "mailbox_not_found", detail: "mismatch" });
    }
    const kept: Record<string, unknown>[] = [];
    let size = 0;
    for (let seq = Math.floor(since) + 1; seq <= meta.highest_seq && kept.length < limit; seq++) {
      const row = await this.state.storage.get<MsgRow>(`msg:${seq}`);
      if (!row || row.to !== binding.participant) continue;
      const obj = await this.env.BLOBS.get(`${mailboxId}/mid/${row.message_id}`);
      const ct = obj ? await obj.text() : "";
      const piece = ct.length;
      if (kept.length && size + piece > PAGE_BYTES) break;
      kept.push({
        seq: row.seq,
        v: 2,
        from: row.from,
        to: row.to,
        message_id: row.message_id,
        sent_at: row.sent_at,
        stored_at: row.stored_at,
        eph: row.eph,
        nonce: row.nonce,
        ct,
      });
      size += piece;
    }
    return {
      mailbox_id: mailboxId,
      highest_seq: meta.highest_seq,
      messages: kept,
      next_since: kept.length ? kept[kept.length - 1].seq : since,
    };
  }

  private async prune(): Promise<void> {
    const meta = await this.state.storage.get<Meta>("meta");
    if (!meta) return;
    const cutoff = Date.now() - 30 * 86400 * 1000;
    const rows: MsgRow[] = [];
    for (let seq = 1; seq <= meta.highest_seq; seq++) {
      const row = await this.state.storage.get<MsgRow>(`msg:${seq}`);
      if (row) rows.push(row);
    }
    const expired = rows.filter((r) => {
      const t = parseRfc3339(r.stored_at);
      return t !== null && t.getTime() < cutoff;
    });
    let surplus = rows.length - expired.length - 100_000;
    const bySeq = [...rows].sort((a, b) => a.seq - b.seq);
    const toDelete = new Set<number>(expired.map((r) => r.seq));
    for (const r of bySeq) {
      if (surplus <= 0) break;
      if (toDelete.has(r.seq)) continue;
      toDelete.add(r.seq);
      surplus--;
    }
    for (const seq of toDelete) {
      const row = rows.find((r) => r.seq === seq);
      if (!row) continue;
      await this.env.BLOBS.delete(`${meta.mailbox_id}/mid/${row.message_id}`);
      await this.state.storage.delete(`msg:${seq}`);
    }
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function bearer(header: string | null): string | null {
  if (!header) return null;
  const parts = header.split(/\s+/);
  if (parts.length !== 2 || parts[0].toLowerCase() !== "bearer") return null;
  return parts[1] || null;
}

function randomBytes(n: number): Uint8Array {
  const u = new Uint8Array(n);
  crypto.getRandomValues(u);
  return u;
}

function hex(u: Uint8Array): string {
  return [...u].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return hex(new Uint8Array(buf));
}

function jsonStr(v: unknown): string {
  return JSON.stringify(String(v));
}

function rfc3339(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function parseRfc3339(text: string): Date | null {
  const d = new Date(text.endsWith("Z") ? text : text);
  return Number.isNaN(d.getTime()) ? null : d;
}

function b64decode(text: string): Uint8Array {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

