"""Ed25519 + X25519-ECIES + AES-256-GCM (PROTOCOL.md §3)."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization

from .canon import aad_bytes, canonical_json_bytes

PROTOCOL = "muse-mailbox/2"
HKDF_INFO = b"muse-mailbox/v2/ecies"
PADDING_BUCKETS = (1024, 4096, 16384)
PARTICIPANTS = ("aman", "gauri")


def b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def kid_of(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).digest()[:8].hex()


def fingerprint(
    mailbox_id: str, participant_id: str, enc_pub: bytes, sig_pub: bytes
) -> str:
    h = hashlib.sha256(
        PROTOCOL.encode("ascii")
        + b"\x00"
        + mailbox_id.encode("ascii")
        + b"\x00"
        + participant_id.encode("ascii")
        + b"\x00"
        + enc_pub
        + b"\x00"
        + sig_pub
    ).hexdigest().upper()
    return " ".join(h[i : i + 4] for i in range(0, 64, 4))


def fingerprint_prefix(fp: str, groups: int = 8) -> str:
    return " ".join(fp.split()[:groups])


@dataclass
class Identity:
    participant: str
    sign_priv: Ed25519PrivateKey
    sign_pub_raw: bytes
    enc_priv: X25519PrivateKey
    enc_pub_raw: bytes

    @classmethod
    def generate(cls, participant: str) -> "Identity":
        if participant not in PARTICIPANTS:
            raise ValueError(f"unknown participant {participant!r}")
        sign_priv = Ed25519PrivateKey.generate()
        enc_priv = X25519PrivateKey.generate()
        sign_pub = sign_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        enc_pub = enc_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return cls(participant, sign_priv, sign_pub, enc_priv, enc_pub)

    def private_dict(self) -> dict:
        """Raw 32-byte seeds. Caller must write mode 0600 and never log this."""
        return {
            "v": 2,
            "participant": self.participant,
            "sign_seed": b64encode(
                self.sign_priv.private_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PrivateFormat.Raw,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            ),
            "enc_seed": b64encode(
                self.enc_priv.private_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PrivateFormat.Raw,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            ),
        }

    @classmethod
    def from_private_dict(cls, data: dict) -> "Identity":
        participant = data["participant"]
        if participant not in PARTICIPANTS:
            raise ValueError(f"unknown participant {participant!r}")
        sign_priv = Ed25519PrivateKey.from_private_bytes(b64decode(data["sign_seed"]))
        enc_priv = X25519PrivateKey.from_private_bytes(b64decode(data["enc_seed"]))
        sign_pub = sign_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        enc_pub = enc_priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return cls(participant, sign_priv, sign_pub, enc_priv, enc_pub)

    @property
    def sign_kid(self) -> str:
        return kid_of(self.sign_pub_raw)

    @property
    def enc_kid(self) -> str:
        return kid_of(self.enc_pub_raw)

    def public_dict(self, mailbox_id: str) -> dict:
        return {
            "encryption_public_key": b64encode(self.enc_pub_raw),
            "signing_public_key": b64encode(self.sign_pub_raw),
            "fingerprint": fingerprint(
                mailbox_id, self.participant, self.enc_pub_raw, self.sign_pub_raw
            ),
        }


@dataclass
class PeerPublic:
    participant: str
    enc_pub_raw: bytes
    sign_pub_raw: bytes

    @classmethod
    def from_card_entry(cls, participant: str, entry: dict) -> "PeerPublic":
        return cls(
            participant,
            b64decode(entry["encryption_public_key"]),
            b64decode(entry["signing_public_key"]),
        )

    @property
    def sign_kid(self) -> str:
        return kid_of(self.sign_pub_raw)

    @property
    def enc_kid(self) -> str:
        return kid_of(self.enc_pub_raw)

    def ed25519_public(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(self.sign_pub_raw)

    def x25519_public(self) -> X25519PublicKey:
        return X25519PublicKey.from_public_bytes(self.enc_pub_raw)


def pad_signed(sig: bytes, inner: bytes) -> bytes:
    if len(sig) != 64:
        raise ValueError("signature must be 64 bytes")
    core = sig + inner
    framed = len(core).to_bytes(4, "big") + core
    for bucket in PADDING_BUCKETS:
        if len(framed) <= bucket:
            return framed + b"\x00" * (bucket - len(framed))
    raise ValueError("padded payload exceeds 16384-byte top bucket")


def unpad_signed(plaintext: bytes) -> tuple[bytes, bytes]:
    if len(plaintext) < 4 + 64:
        raise ValueError("plaintext too short")
    length = int.from_bytes(plaintext[:4], "big")
    if length < 64 or 4 + length > len(plaintext):
        raise ValueError("invalid length prefix")
    rest = plaintext[4 + length :]
    if rest != b"\x00" * len(rest):
        raise ValueError("non-zero padding")
    blob = plaintext[4 : 4 + length]
    return blob[:64], blob[64:]


def derive_message_key(shared: bytes, eph_pub: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=eph_pub,
        info=HKDF_INFO,
    ).derive(shared)


def encrypt_to(
    *,
    mailbox_id: str,
    message_id: str,
    from_id: str,
    to_id: str,
    padded: bytes,
    recipient_enc_pub: bytes,
    v: int = 2,
) -> tuple[str, str, str]:
    eph_priv = X25519PrivateKey.generate()
    eph_pub = eph_priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    shared = eph_priv.exchange(X25519PublicKey.from_public_bytes(recipient_enc_pub))
    key = derive_message_key(shared, eph_pub)
    nonce = os.urandom(12)
    eph_b64 = b64encode(eph_pub)
    aad = aad_bytes(
        v=v,
        mailbox_id=mailbox_id,
        message_id=message_id,
        from_id=from_id,
        to_id=to_id,
        eph_b64=eph_b64,
    )
    ct = AESGCM(key).encrypt(nonce, padded, aad)
    return eph_b64, b64encode(nonce), b64encode(ct)


def try_decrypt(
    *,
    mailbox_id: str,
    envelope: dict,
    enc_privs: list[X25519PrivateKey],
    aad_overrides: dict | None = None,
) -> bytes:
    """Decrypt using configured mailbox_id + received routing, unless overridden (tamper tests)."""
    fields = {
        "v": envelope["v"],
        "mailbox_id": mailbox_id,
        "message_id": envelope["message_id"],
        "from_id": envelope["from"],
        "to_id": envelope["to"],
        "eph_b64": envelope["eph"],
    }
    if aad_overrides:
        fields.update(aad_overrides)
    eph_pub = b64decode(envelope["eph"] if "eph_b64" not in (aad_overrides or {}) else fields["eph_b64"])
    # DH always uses the received eph on the envelope unless the test flipped `eph`.
    eph_for_dh = b64decode(envelope["eph"])
    if aad_overrides and "eph_b64" in aad_overrides:
        eph_for_dh = b64decode(aad_overrides["eph_b64"])
    nonce = b64decode(envelope["nonce"])
    ct = b64decode(envelope["ct"])
    aad = aad_bytes(**fields)
    last_err: Exception | None = None
    for priv in enc_privs:
        try:
            shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_for_dh))
            key = derive_message_key(shared, eph_for_dh)
            return AESGCM(key).decrypt(nonce, ct, aad)
        except (InvalidTag, ValueError) as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err
    raise InvalidTag()


def sign_inner(identity: Identity, inner: dict) -> bytes:
    canonical = canonical_json_bytes(inner)
    sig = identity.sign_priv.sign(canonical)
    return pad_signed(sig, canonical)


def verify_inner(peer: PeerPublic, sig: bytes, inner_bytes: bytes) -> dict:
    peer.ed25519_public().verify(sig, inner_bytes)
    inner = __import__("json").loads(inner_bytes.decode("utf-8"))
    if canonical_json_bytes(inner) != inner_bytes:
        raise ValueError("inner payload is not canonical")
    return inner
