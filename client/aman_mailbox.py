#!/usr/bin/env python3
"""Aman-side muse-mailbox/2 client (Hatch). Send + fetch with E2E crypto.

Secrets live in ~/.muse-mailbox (0600). This file holds no secrets.
Usage:
  python3 aman_mailbox.py send "hello"
  python3 aman_mailbox.py fetch

State (~/.muse-mailbox/state.json) is shared between the send path (CLI) and
the fetch path (persistent daemon). The two sides own disjoint key sets and
merge under an exclusive file lock -- a full rewrite from a stale in-memory
copy once pinned sender_seq and caused replay quarantines on the far side.
  send side owns: sender_seq   (claimed atomically before POST)
  fetch side owns: fetch_cursor, process_cursor, seen_ids
"""
import json, os, sys, base64, uuid, time
import fcntl
from datetime import datetime, timezone

import requests
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import hashlib

HOME = os.path.expanduser("~/.muse-mailbox")
UA = "muse-mailbox/2 hatch-anton/1.0"
PADDING_BUCKETS = [1024, 4096, 16384]

def load(name):
    with open(os.path.join(HOME, name)) as f:
        return json.load(f)

def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def b64e(b): return base64.b64encode(b).decode()
def b64d(s): return base64.b64decode(s)

class Mailbox:
    def __init__(self):
        ident = load("identity.json")
        card = load("card.json")
        prov = load("provision.json")
        self.mailbox_id = ident["mailbox_id"]
        self.base = ident["relay_url"].rstrip("/")
        self.sign_priv = ed25519.Ed25519PrivateKey.from_private_bytes(b64d(ident["signing_private_key_b64"]))
        self.enc_priv = x25519.X25519PrivateKey.from_private_bytes(b64d(ident["encryption_private_key_b64"]))
        self.sign_kid = ident["signing_kid"]
        self.enc_kid = ident["encryption_kid"]
        gk = card["keys"]["gauri"]
        self.gauri_enc_pub = x25519.X25519PublicKey.from_public_bytes(b64d(gk["encryption_public_key_b64"]))
        self.gauri_sign_pub = ed25519.Ed25519PublicKey.from_public_bytes(b64d(gk["signing_public_key_b64"]))
        self.gauri_sign_kid = gk["signing_kid"]
        self.gauri_enc_kid = gk["encryption_kid"]
        self.send_token = prov["tokens"]["aman_send"]
        self.fetch_token = prov["tokens"]["aman_fetch"]
        self.state_path = os.path.join(HOME, "state.json")
        if os.path.exists(self.state_path):
            self.state = json.load(open(self.state_path))
        else:
            self.state = {"fetch_cursor": 0, "process_cursor": {"gauri": 0},
                          "sender_seq": 0, "seen_ids": []}
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA})

    def _merge_save(self, keys):
        """Read-modify-write only `keys` under an exclusive lock.

        The daemon (fetch side) and the CLI (send side) share this file but
        own disjoint keys. Rewriting the whole file from a stale in-memory
        copy clobbers the other side's fields -- that bug pinned sender_seq
        and triggered replay quarantines. Merging only owned keys fixes it.
        """
        with open(self.state_path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                current = json.load(f)
            except Exception:
                current = {}
            for k in keys:
                if k == "seen_ids":
                    merged = current.get("seen_ids", []) + self.state.get("seen_ids", [])
                    current[k] = list(dict.fromkeys(merged))[-10000:]
                elif k == "fetch_cursor":
                    current[k] = max(current.get(k, 0), self.state.get(k, 0))
                elif k == "process_cursor":
                    pc = current.get(k, {})
                    for who, v in self.state.get(k, {}).items():
                        pc[who] = max(pc.get(who, 0), v)
                    current[k] = pc
                else:
                    current[k] = self.state[k]
            f.seek(0)
            f.truncate()
            json.dump(current, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(self.state_path, 0o600)
        with open(self.state_path) as f:
            current = json.load(f)
        for k in keys:
            self.state[k] = current[k]

    def save_fetch_state(self):
        """Persist only fetch-side keys (daemon). Never touches sender_seq."""
        self._merge_save(("fetch_cursor", "process_cursor", "seen_ids"))

    def claim_send_slot(self):
        """Atomically claim the next (sender_seq, message_id).

        The claim is persisted BEFORE the POST, so a retry after an ambiguous
        failure reuses the same pair and the relay dedups by message_id --
        the far side never sees a sender_seq replay.
        """
        mid = str(uuid.uuid4())
        with open(self.state_path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                current = json.load(f)
            except Exception:
                current = {}
            current["sender_seq"] = current.get("sender_seq", 0) + 1
            f.seek(0)
            f.truncate()
            json.dump(current, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(self.state_path, 0o600)
        self.state["sender_seq"] = current["sender_seq"]
        return current["sender_seq"], mid

    def now(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _build_envelope(self, body, mtype, sender_seq, mid,
                        in_reply_to=None, return_to=None, to_channel=None,
                        status=None):
        inner = {"from": "aman", "from_sign_kid": self.sign_kid,
                 "to": "gauri", "to_enc_kid": self.gauri_enc_kid, "message_id": mid,
                 "sender_seq": sender_seq, "sent_at": self.now(),
                 "type": mtype, "v": 2}
        # v2.2 acks carry no body — just {type, in_reply_to, status}.
        if body is not None:
            inner["body"] = body
        if status is not None:
            inner["status"] = status
        # v2.1 threaded replies (optional, backward compatible):
        # - in_reply_to: message_id being answered (thread link)
        # - return_to: {"agent","channel"} — route replies to THIS message here
        # - to_channel: {"agent","channel"} — destination of THIS message,
        #   copied from the answered message's return_to (replies only)
        if in_reply_to:
            inner["in_reply_to"] = in_reply_to
        if return_to:
            inner["return_to"] = return_to
        if to_channel:
            inner["to_channel"] = to_channel
        cbytes = canon(inner)
        sig = self.sign_priv.sign(cbytes)
        payload = sig + cbytes
        framed = len(payload).to_bytes(4, "big") + payload
        bucket = next(b for b in PADDING_BUCKETS if b >= len(framed))
        padded = framed + b"\x00" * (bucket - len(framed))

        eph_priv = x25519.X25519PrivateKey.generate()
        eph_pub = eph_priv.public_key().public_bytes(serialization.Encoding.Raw,
                                                     serialization.PublicFormat.Raw)
        shared = eph_priv.exchange(self.gauri_enc_pub)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub,
                   info=b"muse-mailbox/v2/ecies").derive(shared)
        nonce = os.urandom(12)
        aad = canon({"eph": b64e(eph_pub), "from": "aman", "mailbox_id": self.mailbox_id,
                     "message_id": mid, "to": "gauri", "v": 2})
        ct = AESGCM(key).encrypt(nonce, padded, aad)

        env = {"v": 2, "from": "aman", "to": "gauri", "message_id": mid,
               "sent_at": self.now(), "eph": b64e(eph_pub), "nonce": b64e(nonce),
               "ct": b64e(ct)}
        url = f"{self.base}/v1/mailboxes/{self.mailbox_id}/messages"
        headers = {"Authorization": f"Bearer {self.send_token}"}
        return url, headers, env

    def _post_with_retry(self, url, headers, env, attempts=3):
        """POST the same envelope, retrying only ambiguous failures.

        Every attempt reuses the same message_id + sender_seq: if an earlier
        attempt actually landed, the relay answers duplicate=true and we
        treat it as sent. Never mint a fresh id for a retry.
        """
        last = None
        for i in range(attempts):
            try:
                r = self.s.post(url, headers=headers, json=env, timeout=30)
                if r.status_code == 409:
                    return {"duplicate": True}
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if r.ok and data.get("duplicate"):
                    return data
                r.raise_for_status()
                return data
            except (requests.Timeout, requests.ConnectionError) as e:
                last = e
            except requests.HTTPError as e:
                resp = getattr(e, "response", None)
                if resp is not None and 500 <= resp.status_code < 600:
                    last = e
                else:
                    raise
            time.sleep(2 * (i + 1))
        raise last

    def _reclaim_send_slot(self, sender_seq):
        """Give back a claimed sender_seq after a definitive refusal.

        Only safe when the server definitively rejected the POST (4xx: nothing
        was stored). Never call after ambiguous failures (timeouts/5xx with
        retries exhausted) — the POST may have landed, and reclaiming would
        cause a sender_seq replay on the far side.
        """
        with open(self.state_path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                current = json.load(f)
            except Exception:
                return
            if current.get("sender_seq") == sender_seq:
                current["sender_seq"] = sender_seq - 1
                f.seek(0)
                f.truncate()
                json.dump(current, f)
                f.flush()
                os.fsync(f.fileno())
        os.chmod(self.state_path, 0o600)
        if self.state.get("sender_seq") == sender_seq:
            self.state["sender_seq"] = sender_seq - 1

    def send(self, body, mtype="message", in_reply_to=None, return_to=None,
             to_channel=None, status=None):
        sender_seq, mid = self.claim_send_slot()
        url, headers, env = self._build_envelope(body, mtype, sender_seq, mid,
                                                 in_reply_to, return_to,
                                                 to_channel, status)
        try:
            res = self._post_with_retry(url, headers, env)
        except requests.HTTPError as e:
            resp = getattr(e, "response", None)
            # Definitive refusal (4xx): the relay stored nothing, so reclaim
            # the sender_seq instead of burning it into a phantom gap on the
            # far side's continuity guard (2026-09-28: Iko showed our 51/52
            # never arrived as envelopes — burned by failed sends).
            if resp is not None and 400 <= resp.status_code < 500:
                self._reclaim_send_slot(sender_seq)
            raise
        # Merge our local claim into the receipt: the agent verifies a send
        # by checking the output contains {"seq": N}. On the relay-duplicate
        # path the relay returns the original seq; on a 409 payload conflict
        # there is no seq — mark it explicitly so it can't be mistaken for
        # a verified send.
        receipt = {"sender_seq": sender_seq, "message_id": mid}
        if isinstance(res, dict):
            receipt.update(res)
        if receipt.get("duplicate") and "seq" not in receipt:
            receipt["conflict"] = True
        return receipt

    def fetch(self):
        out = []
        while True:
            since = self.state["fetch_cursor"]
            r = self.s.get(f"{self.base}/v1/mailboxes/{self.mailbox_id}/messages",
                           headers={"Authorization": f"Bearer {self.fetch_token}"},
                           params={"since": since, "limit": 200}, timeout=30)
            r.raise_for_status()
            page = r.json()
            msgs = page.get("messages", [])
            if not msgs:
                self.state["fetch_cursor"] = page["highest_seq"]
                break
            lowest_buffered = None
            for m in msgs:
                res = self._handle(m)
                if res:
                    out.append(res)
                    if res.get("buffered"):
                        bseq = res["seq"]
                        lowest_buffered = bseq if lowest_buffered is None else min(lowest_buffered, bseq)
            if lowest_buffered is not None:
                # Continuity gap in this page: pin the cursor below the
                # lowest buffered seq so it is retried next poll instead of
                # being advanced past and lost.
                self.state["fetch_cursor"] = lowest_buffered - 1
            else:
                self.state["fetch_cursor"] = page["next_since"]
            if page["next_since"] == page["highest_seq"]:
                break
        self.save_fetch_state()
        return out

    def _handle(self, m):
        if m["message_id"] in self.state["seen_ids"]:
            return None
        aad = canon({"v": m["v"], "mailbox_id": self.mailbox_id,
                     "message_id": m["message_id"], "from": m["from"],
                     "to": m["to"], "eph": m["eph"]})
        eph_pub = b64d(m["eph"])
        shared = self.enc_priv.exchange(x25519.X25519PublicKey.from_public_bytes(eph_pub))
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub,
                   info=b"muse-mailbox/v2/ecies").derive(shared)
        try:
            padded = AESGCM(key).decrypt(b64d(m["nonce"]), b64d(m["ct"]), aad)
        except Exception:
            self.state["seen_ids"].append(m["message_id"])
            return {"error": "undecryptable", "seq": m["seq"]}
        ln = int.from_bytes(padded[:4], "big")
        sig, ibytes = padded[4:4+64], padded[4+64:4+ln]
        try:
            self.gauri_sign_pub.verify(sig, ibytes)  # verify BEFORE parse
        except Exception:
            self.state["seen_ids"].append(m["message_id"])
            return {"error": "bad_signature", "seq": m["seq"]}
        inner = json.loads(ibytes)
        assert inner["from"] == m["from"] and inner["to"] == m["to"]
        assert inner["message_id"] == m["message_id"] and inner["to"] == "aman"
        s = inner["sender_seq"]
        pc = self.state["process_cursor"]
        if s == pc["gauri"] + 1:
            pc["gauri"] = s
            self.state["seen_ids"].append(m["message_id"])
            return {"seq": m["seq"], "sender_seq": s, "type": inner["type"],
                    "body": inner.get("body"), "sent_at": inner["sent_at"],
                    "message_id": inner["message_id"],
                    "in_reply_to": inner.get("in_reply_to"),
                    "return_to": inner.get("return_to"),
                    "to_channel": inner.get("to_channel")}
        if s <= pc["gauri"]:
            # Counter regression or duplicate sender_seq on the sender side
            # (e.g. the seq-71 case; 2026-09-27: relay 111 reused sender_seq 49
            # already consumed by relay 109): this seq can never arrive in
            # order — holding it in the continuity buffer deadlocks it forever.
            # Release it out-of-order, loudly, marked seen — and NEVER rewind
            # the continuity guard.
            self.state["seen_ids"].append(m["message_id"])
            return {"seq": m["seq"], "sender_seq": s, "type": inner["type"],
                    "body": inner.get("body"), "sent_at": inner["sent_at"],
                    "message_id": inner["message_id"],
                    "in_reply_to": inner.get("in_reply_to"),
                    "return_to": inner.get("return_to"),
                    "to_channel": inner.get("to_channel"),
                    "out_of_order": True}
        # Genuine gap (s > expected): hold for retry. Do NOT mark seen — the
        # fetch cursor stays pinned below this seq so the message is retried
        # next poll instead of skipped forever.
        return {"seq": m["seq"], "sender_seq": s, "buffered": True}

def _parse_send_args(args):
    """Parse: mb-send send [--in-reply-to MSGID] [--return-to CHANNEL]
    [--to-channel AGENT:CHANNEL] <body...>"""
    in_reply_to = None
    return_to = None
    to_channel = None
    body_parts = []
    i = 0
    while i < len(args):
        if args[i] == "--in-reply-to" and i + 1 < len(args):
            in_reply_to = args[i + 1]
            i += 2
        elif args[i] == "--return-to" and i + 1 < len(args):
            return_to = {"agent": "anton", "channel": args[i + 1]}
            i += 2
        elif args[i] == "--to-channel" and i + 1 < len(args):
            agent, _, channel = args[i + 1].partition(":")
            to_channel = {"agent": agent, "channel": channel or "inbox"}
            i += 2
        else:
            body_parts.append(args[i])
            i += 1
    return " ".join(body_parts) if body_parts else "hello", in_reply_to, \
        return_to, to_channel


def main():
    mb = Mailbox()
    if len(sys.argv) < 2 or sys.argv[1] not in ("send", "fetch", "ack"):
        print("usage: aman_mailbox.py send [--in-reply-to MSGID] [--return-to CHANNEL] [--to-channel AGENT:CHANNEL] <body> | ack <acked_mid> [--return-to CHANNEL] [--to-channel AGENT:CHANNEL] | fetch", file=sys.stderr)
        sys.exit(2)
    # 2026-10-06: _parse_send_args treats unrecognized flags as body text,
    # so `mb-send send --help` once sent the literal string "--help" to the
    # peer (relay seq 292, Oct 5; first occurrence relay 186, Sep 30). A body
    # that looks like a flag is never a real message — refuse it loudly.
    for tok in sys.argv[2:]:
        if tok in ("--help", "-h"):
            print("usage: aman_mailbox.py send [--in-reply-to MSGID] [--return-to CHANNEL] [--to-channel AGENT:CHANNEL] <body> | ack <acked_mid> [--return-to CHANNEL] [--to-channel AGENT:CHANNEL] | fetch", file=sys.stderr)
            sys.exit(2)
    if sys.argv[1] == "send":
        body, in_reply_to, return_to, to_channel = _parse_send_args(sys.argv[2:])
        if body.startswith("-"):
            print("refusing to send flag-like body %r (did you mean --help?)" % body,
                  file=sys.stderr)
            sys.exit(2)
        res = mb.send(body, in_reply_to=in_reply_to, return_to=return_to,
                      to_channel=to_channel)
        # Receipt contract: a verified send ALWAYS prints {"seq": N}.
        # {"duplicate": true, "conflict": true} (409) is NOT a verified send.
        print(json.dumps({k: res[k] for k in
                          ("seq", "message_id", "sender_seq", "duplicate",
                           "conflict") if k in res}))
    elif sys.argv[1] == "ack":
        # v2.2 read acknowledgment: {type:ack, in_reply_to:<mid>, status:seen}
        # plus return_to like a heartbeat. Never ack an ack.
        acked_mid, _, return_to, to_channel = _parse_send_args(sys.argv[2:])
        if not acked_mid or acked_mid == "hello" or acked_mid.startswith("-"):
            print("usage: ack <acked_message_id> [--return-to CHANNEL] [--to-channel AGENT:CHANNEL]", file=sys.stderr)
            sys.exit(2)
        res = mb.send(None, mtype="ack", status="seen", in_reply_to=acked_mid,
                      return_to=return_to, to_channel=to_channel)
        print(json.dumps({k: res[k] for k in
                          ("seq", "message_id", "sender_seq", "duplicate",
                           "conflict") if k in res}))
    elif sys.argv[1] == "fetch":
        msgs = mb.fetch()
        for x in msgs:
            if "body" in x:
                print(json.dumps({"seq": x["seq"], "sender_seq": x.get("sender_seq"),
                                  "type": x.get("type"), "sent_at": x.get("sent_at")}))
                print("BODY<<<\n" + x["body"] + "\n>>>END")
            else:
                print(json.dumps(x))

if __name__ == "__main__":
    main()
