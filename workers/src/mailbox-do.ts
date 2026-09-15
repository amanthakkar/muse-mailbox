/**
 * Per-mailbox Durable Object. Authoritative for seq, dedupe, auth, presence.
 * Ciphertext bytes go to R2; the DO stores the index + payload hash.
 *
 * Write order (crash-safe): PUT R2 object keyed by mailbox_id/message_id first,
 * then DO storage transaction assigns seq. Retry of the same message_id finds
 * the idempotency row and returns the original seq. Never assign seq before
 * the blob is durable — that is how we avoid a seq that points at missing R2.
 *
 * THIS IS A SKETCH. It is not wired to an account and must not be deployed.
 */

export class MailboxDO {
  constructor(private readonly state: DurableObjectState, private readonly env: { BLOBS: R2Bucket }) {}

  async fetch(request: Request): Promise<Response> {
    // Input gate: one request at a time per mailbox — atomic seq.
    return this.state.blockConcurrencyWhile(() => this.route(request));
  }

  private async route(_request: Request): Promise<Response> {
    return new Response(
      JSON.stringify({
        error: "not_deployed",
        detail: "Scratch Worker sketch only. See Python harness for passing tests.",
      }),
      { status: 501, headers: { "content-type": "application/json" } },
    );
  }
}
