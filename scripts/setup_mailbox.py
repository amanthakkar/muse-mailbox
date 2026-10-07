#!/usr/bin/env python3
"""Client-side setup helper for muse-mailbox/2. No relay changes needed.

Commands:
  setup_mailbox.py --init
      Generate this device's Ed25519 signing + X25519 encryption keypairs,
      write ~/.muse-mailbox/identity.json (0600), print the key fingerprint
      for the in-person ceremony. Safe to re-run: refuses to overwrite an
      existing identity (use --force to rotate, then re-run the ceremony).

  setup_mailbox.py --provision --relay URL --admin-secret-env VAR
      Operator helper: create a mailbox and mint the four directional tokens
      via the relay admin endpoint. ADMIN_SECRET is read from the named
      environment variable and never written to disk. Writes
      ~/.muse-mailbox/provision.json (0600).

  setup_mailbox.py --card --for NAME --mailbox ID --relay URL
      Build a per-recipient connection card (the manual path, scripted):
      reads local identity.json + provision.json, emits the card JSON to
      stdout for out-of-band delivery to the peer.

  setup_mailbox.py --redeem INV_CODE
      Placeholder for the invite flow (see docs/INVITE_ROADMAP.md): the relay
      invite endpoints are not implemented yet. Prints the manual steps.

Key formats match PROTOCOL.md: base64 of raw 32-byte keys. Fingerprint:
  SHA-256("muse-mailbox/2" || 0x00 || mailbox_id || 0x00 || participant ||
          0x00 || enc_pub || 0x00 || sig_pub), 16 groups of 4 uppercase hex.
"""
import argparse
import base64
import hashlib
import json
import os
import sys

CLIENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "client")
sys.path.insert(0, os.path.abspath(CLIENT_DIR))
# NOTE: key generation below uses `cryptography` directly and intentionally
# does not import aman_mailbox — this script must stay runnable without
# identity/card/provision files present.

from cryptography.hazmat.primitives.asymmetric import ed25519, x25519  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

HOME = os.path.expanduser("~/.muse-mailbox")
IDENTITY_PATH = os.path.join(HOME, "identity.json")
PROVISION_PATH = os.path.join(HOME, "provision.json")


def _write_0600(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _raw_b64(key):
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _raw_priv_b64(priv):
    return base64.b64encode(priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())).decode()


def fingerprint(mailbox_id, participant, enc_pub_b64, sig_pub_b64):
    h = hashlib.sha256()
    for part in (b"muse-mailbox/2", b"\x00", mailbox_id.encode(), b"\x00",
                 participant.encode(), b"\x00",
                 base64.b64decode(enc_pub_b64), b"\x00",
                 base64.b64decode(sig_pub_b64)):
        h.update(part)
    hexs = h.hexdigest().upper()
    return " ".join(hexs[i:i + 4] for i in range(0, 64, 4))


def cmd_init(args):
    if os.path.exists(IDENTITY_PATH) and not args.force:
        print("identity.json already exists; refusing to overwrite "
              "(use --force to rotate, then re-run the ceremony).",
              file=sys.stderr)
        return 2
    sign_priv = ed25519.Ed25519PrivateKey.generate()
    enc_priv = x25519.X25519PrivateKey.generate()
    import uuid
    ident = {
        "participant": args.name,
        "signing_private_key_b64": _raw_priv_b64(sign_priv),
        "encryption_private_key_b64": _raw_priv_b64(enc_priv),
        "signing_public_key_b64": _raw_b64(sign_priv),
        "encryption_public_key_b64": _raw_b64(enc_priv),
        "signing_kid": "sig-" + uuid.uuid4().hex[:8],
        "encryption_kid": "enc-" + uuid.uuid4().hex[:8],
    }
    _write_0600(IDENTITY_PATH, ident)
    print("wrote %s (0600)" % IDENTITY_PATH)
    print("signing   public: %s" % ident["signing_public_key_b64"])
    print("encryption public: %s" % ident["encryption_public_key_b64"])
    print("kid: %s / %s" % (ident["signing_kid"], ident["encryption_kid"]))
    print()
    print("Next: exchange public keys + kids with your peer IN PERSON, then")
    print("each side confirms the fingerprint over a second channel before")
    print("pinning. (Fingerprint needs the mailbox_id — compute it after")
    print("provisioning with: setup_mailbox.py --fingerprint ...)")
    return 0


def cmd_fingerprint(args):
    ident = json.load(open(IDENTITY_PATH))
    print(fingerprint(args.mailbox, ident["participant"],
                      ident["encryption_public_key_b64"],
                      ident["signing_public_key_b64"]))
    return 0


def cmd_provision(args):
    import requests
    secret = os.environ.get(args.admin_secret_env)
    if not secret:
        print("env var %s is not set" % args.admin_secret_env, file=sys.stderr)
        return 2
    r = requests.post(args.relay.rstrip("/") + "/v1/admin/mailboxes",
                      headers={"Authorization": "Bearer " + secret,
                               "User-Agent": "muse-mailbox/2 setup/1.0"},
                      json={}, timeout=30)
    r.raise_for_status()
    data = r.json()
    _write_0600(PROVISION_PATH, data)
    print("wrote %s (0600)" % PROVISION_PATH)
    print("mailbox_id: %s" % data.get("mailbox_id"))
    print("tokens minted for: %s" % ", ".join(sorted(data.get("tokens", {}))))
    return 0


def cmd_card(args):
    ident = json.load(open(IDENTITY_PATH))
    prov = json.load(open(PROVISION_PATH))
    tokens = prov.get("tokens", {})
    send_key = args.for_ + "_send"
    fetch_key = args.for_ + "_fetch"
    if send_key not in tokens or fetch_key not in tokens:
        print("provision.json has no token pair for %r" % args.for_,
              file=sys.stderr)
        return 2
    card = {
        "protocol": "muse-mailbox/2",
        "relay_url": args.relay,
        "mailbox_id": args.mailbox,
        "owner": args.for_,
        "credentials": {"send": tokens[send_key], "fetch": tokens[fetch_key]},
        "keys": {
            ident["participant"]: {
                "encryption_public_key_b64": ident["encryption_public_key_b64"],
                "signing_public_key_b64": ident["signing_public_key_b64"],
                "encryption_kid": ident["encryption_kid"],
                "signing_kid": ident["signing_kid"],
            }
        },
    }
    print(json.dumps(card, indent=2))
    print("---", file=sys.stderr)
    print("Deliver this card to %s OUT OF BAND (in person preferred)." % args.for_,
          file=sys.stderr)
    print("They add their own keys under keys.<their-name>, then both sides",
          file=sys.stderr)
    print("confirm fingerprints over a second channel before pinning.",
          file=sys.stderr)
    return 0


def cmd_redeem(args):
    print("Invite redemption is not implemented yet.")
    print()
    print("The design is specified in docs/INVITE_ROADMAP.md; the relay")
    print("endpoints (POST /v1/mailboxes/{id}/invites and")
    print("POST /v1/invites/{code}/redeem) are still TODO.")
    print()
    print("Manual path for now:")
    print("  1. Ask the inviter for their connection card (out of band).")
    print("  2. Run: setup_mailbox.py --init --name <your-name>")
    print("  3. Add your public keys + kids under keys.<your-name> in the card.")
    print("  4. Both sides confirm fingerprints over a second channel, then")
    print("     save the card as ~/.muse-mailbox/card.json (0600).")
    return 3


def main():
    ap = argparse.ArgumentParser(description="muse-mailbox/2 setup helper")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--provision", action="store_true")
    ap.add_argument("--card", action="store_true")
    ap.add_argument("--redeem", metavar="INV_CODE")
    ap.add_argument("--fingerprint", action="store_true")
    ap.add_argument("--name", default="aman", help="local participant name")
    ap.add_argument("--for", dest="for_", help="card recipient name")
    ap.add_argument("--mailbox", help="mailbox id")
    ap.add_argument("--relay", help="relay base URL")
    ap.add_argument("--admin-secret-env", default="MAILBOX_ADMIN_SECRET")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.init:
        return cmd_init(args)
    if args.fingerprint:
        if not args.mailbox:
            print("--mailbox is required", file=sys.stderr)
            return 2
        return cmd_fingerprint(args)
    if args.provision:
        if not args.relay:
            print("--relay is required", file=sys.stderr)
            return 2
        return cmd_provision(args)
    if args.card:
        for need in ("for_", "mailbox", "relay"):
            if not getattr(args, need):
                print("--%s is required" % need.replace("_", "-"), file=sys.stderr)
                return 2
        return cmd_card(args)
    if args.redeem:
        return cmd_redeem(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
