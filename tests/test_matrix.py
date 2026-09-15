"""§9 mandatory matrix + the six substrate proof points. Scratch only."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from muse_mailbox.canon import canonical_json_bytes  # noqa: E402
from muse_mailbox.client import MailboxClient  # noqa: E402
from muse_mailbox.crypto import (  # noqa: E402
    PeerPublic,
    b64decode,
    b64encode,
    encrypt_to,
    pad_signed,
    try_decrypt,
)
from muse_mailbox.provision import provision_dev  # noqa: E402
from muse_mailbox.relay import Relay  # noqa: E402
from muse_mailbox.store import Store  # noqa: E402
from muse_mailbox.transport import HttpError, HttpTransport, InProcessTransport  # noqa: E402


def _clients(store: Store, relay: Relay | None = None, transport=None):
    bundle = provision_dev(store)
    mid = bundle["mailbox_id"]
    if transport is None:
        assert relay is not None
        transport = InProcessTransport(relay, mid)
    ids = bundle["identities"]

    def mk(name: str, peer: str) -> MailboxClient:
        return MailboxClient(
            participant=name,
            mailbox_id=mid,
            identity=ids[name],
            peer=PeerPublic(
                peer, ids[peer].enc_pub_raw, ids[peer].sign_pub_raw
            ),
            send_token=bundle["tokens"][f"{name}_send"],
            fetch_token=bundle["tokens"][f"{name}_fetch"],
            transport=transport,
        )

    return bundle, mk("aman", "gauri"), mk("gauri", "aman")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class CryptoAndAADTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(self.id().replace(".", "_"))
        # use a real tmp dir
        import tempfile

        self._td = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self._td.name) / "mb.sqlite"))
        self.relay = Relay(self.store)
        self.bundle, self.aman, self.gauri = _clients(self.store, self.relay)

    def tearDown(self) -> None:
        self.store.close()
        self._td.cleanup()

    def test_aad_tamper_matrix(self) -> None:
        env = self.aman.compose("hello aad")
        wire = {k: v for k, v in env.items() if not k.startswith("_")}
        cases = []
        cases.append(("v", {"v": 3}))
        cases.append(("mailbox_id", {"mailbox_id": self.aman.mailbox_id + "x"}))
        cases.append(("message_id", {"message_id": str(uuid.uuid4())}))
        cases.append(("from_id", {"from_id": "gauri"}))
        cases.append(("to_id", {"to_id": "aman"}))
        flipped_eph = bytearray(b64decode(wire["eph"]))
        flipped_eph[0] ^= 0x01
        cases.append(("eph_b64", {"eph_b64": b64encode(bytes(flipped_eph))}))

        for name, overrides in cases:
            with self.subTest(field=name):
                with self.assertRaises(Exception):
                    try_decrypt(
                        mailbox_id=self.aman.mailbox_id,
                        envelope=wire,
                        enc_privs=[self.gauri.identity.enc_priv],
                        aad_overrides=overrides,
                    )

        flipped_ct = bytearray(b64decode(wire["ct"]))
        flipped_ct[0] ^= 0x01
        bad = dict(wire)
        bad["ct"] = b64encode(bytes(flipped_ct))
        with self.assertRaises(Exception):
            try_decrypt(
                mailbox_id=self.aman.mailbox_id,
                envelope=bad,
                enc_privs=[self.gauri.identity.enc_priv],
            )

    def test_roundtrip(self) -> None:
        self.aman.send("ping")
        self.gauri.drain()
        self.assertEqual(self.gauri.state.dispatched[-1]["body"], "ping")
        self.assertEqual(self.gauri.state.process_cursor["aman"], 1)


class SignatureReplayQuarantineTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._td = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self._td.name) / "mb.sqlite"))
        self.relay = Relay(self.store)
        self.bundle, self.aman, self.gauri = _clients(self.store, self.relay)

    def tearDown(self) -> None:
        self.store.close()
        self._td.cleanup()

    def test_signature_tamper_quarantines(self) -> None:
        inner = {
            "body": "forged",
            "from": "aman",
            "from_sign_kid": self.aman.identity.sign_kid,
            "message_id": str(uuid.uuid4()),
            "sender_seq": 1,
            "sent_at": self.aman.compose("x")["_inner"]["sent_at"],
            "to": "gauri",
            "to_enc_kid": self.gauri.identity.enc_kid,
            "type": "message",
            "v": 2,
        }
        canonical = canonical_json_bytes(inner)
        padded = pad_signed(os.urandom(64), canonical)
        eph, nonce, ct = encrypt_to(
            mailbox_id=self.aman.mailbox_id,
            message_id=inner["message_id"],
            from_id="aman",
            to_id="gauri",
            padded=padded,
            recipient_enc_pub=self.gauri.identity.enc_pub_raw,
        )
        env = {
            "v": 2,
            "from": "aman",
            "to": "gauri",
            "message_id": inner["message_id"],
            "sent_at": inner["sent_at"],
            "eph": eph,
            "nonce": nonce,
            "ct": ct,
        }
        result = self.gauri.handle(env)
        self.assertEqual(result, "quarantined")
        self.assertEqual(self.gauri.state.dispatched, [])
        self.assertTrue(any(q["reason"] == "signature" for q in self.gauri.state.quarantined))

    def test_replay_no_second_dispatch(self) -> None:
        env = self.aman.compose("once")
        wire = {k: v for k, v in env.items() if not k.startswith("_")}
        self.assertEqual(self.gauri.handle(wire), "dispatched")
        self.assertEqual(self.gauri.handle(wire), "duplicate")
        # re-wrap: new envelope + new message_id, same sender_seq, valid signature
        env2 = self.aman.compose("once-rewrapped", sender_seq=1)
        wire2 = {k: v for k, v in env2.items() if not k.startswith("_")}
        self.assertEqual(self.gauri.handle(wire2), "replay")
        self.assertEqual(len(self.gauri.state.dispatched), 1)

    def test_undecryptable_does_not_stall(self) -> None:
        before = self.gauri.state.fetch_cursor
        junk = {
            "v": 2,
            "from": "aman",
            "to": "gauri",
            "message_id": str(uuid.uuid4()),
            "sent_at": self.aman.compose("unused-for-timestamp")["_inner"]["sent_at"],
            "eph": b64encode(os.urandom(32)),
            "nonce": b64encode(os.urandom(12)),
            "ct": b64encode(os.urandom(80)),
        }
        # compose() above consumed sender_seq 1; rewind so the real message is seq 1
        self.aman.state.next_sender_seq = 1
        self.aman.transport("POST", junk, token=self.aman.send_token)
        self.aman.send("after junk")
        self.gauri.drain()
        self.assertGreater(self.gauri.state.fetch_cursor, before)
        self.assertEqual(self.gauri.state.fetch_cursor, self.bundle_highest())
        bodies = [d["body"] for d in self.gauri.state.dispatched]
        self.assertEqual(bodies, ["after junk"])
        self.assertTrue(any(q["reason"] == "undecryptable blob" for q in self.gauri.state.quarantined))

    def bundle_highest(self) -> int:
        return InProcessTransport(self.relay, self.aman.mailbox_id)(
            "STATS", None
        )["highest_seq"]

    def test_out_of_order_10_12_11(self) -> None:
        for i in range(1, 10):
            self.assertEqual(self.gauri.handle(self._wire(self.aman.compose(f"m{i}", sender_seq=i))), "dispatched")
        self.assertEqual(self.gauri.state.process_cursor["aman"], 9)
        w10 = self._wire(self.aman.compose("m10", sender_seq=10))
        w12 = self._wire(self.aman.compose("m12", sender_seq=12))
        w11 = self._wire(self.aman.compose("m11", sender_seq=11))
        self.assertEqual(self.gauri.handle(w10), "dispatched")
        self.assertEqual(self.gauri.handle(w12), "buffered")
        self.assertEqual(self.gauri.handle(w11), "dispatched")
        self.assertEqual(self.gauri.state.process_cursor["aman"], 12)
        self.assertEqual(
            [d["sender_seq"] for d in self.gauri.state.dispatched],
            list(range(1, 13)),
        )
        self.assertEqual(len(self.gauri.state.dispatched), 12)

    def _wire(self, env: dict) -> dict:
        return {k: v for k, v in env.items() if not k.startswith("_")}


class AuthPresenceIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._td = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self._td.name) / "mb.sqlite"))
        self.relay = Relay(self.store)
        self.bundle, self.aman, self.gauri = _clients(self.store, self.relay)
        self.tr = InProcessTransport(self.relay, self.aman.mailbox_id)

    def tearDown(self) -> None:
        self.store.close()
        self._td.cleanup()

    def test_presence_oracle(self) -> None:
        stats = self.tr("STATS", None)
        self.assertEqual(set(stats.keys()), {"highest_seq"})
        self.assertEqual(stats["highest_seq"], 0)
        self.aman.send("hello")
        stats2 = self.tr("STATS", None)
        self.assertEqual(stats2["highest_seq"], 1)

    def test_directional_tokens(self) -> None:
        env = self.aman.compose("nope")
        wire = {k: v for k, v in env.items() if not k.startswith("_")}
        # fetch token cannot send
        with self.assertRaises(HttpError) as cm:
            self.tr("POST", wire, token=self.aman.fetch_token)
        self.assertEqual(cm.exception.status, 403)
        # send token cannot fetch
        with self.assertRaises(HttpError) as cm:
            self.tr("GET", None, token=self.aman.send_token)
        self.assertEqual(cm.exception.status, 403)
        # lying from=gauri with aman send token
        wire["from"] = "gauri"
        wire["to"] = "aman"
        with self.assertRaises(HttpError) as cm:
            self.tr("POST", wire, token=self.aman.send_token)
        self.assertEqual(cm.exception.status, 403)
        # gauri fetch cannot read aman-addressed rows — aman send, gauri fetch gets empty
        self.aman.send("secret-for-gauri")
        page = self.tr("GET", None, token=self.aman.fetch_token, since=0, limit=50)
        self.assertEqual(page["messages"], [])

    def test_idempotent_retry_and_conflict(self) -> None:
        env = self.aman.compose("same")
        wire = {k: v for k, v in env.items() if not k.startswith("_")}
        r1 = self.tr("POST", wire, token=self.aman.send_token)
        r2 = self.tr("POST", wire, token=self.aman.send_token)
        self.assertFalse(r1["duplicate"])
        self.assertTrue(r2["duplicate"])
        self.assertEqual(r1["seq"], r2["seq"])
        conflict = dict(wire)
        conflict["ct"] = b64encode(os.urandom(80))
        with self.assertRaises(HttpError) as cm:
            self.tr("POST", conflict, token=self.aman.send_token)
        self.assertEqual(cm.exception.status, 409)

    def test_prune_does_not_reuse_seq(self) -> None:
        self.aman.send("old")
        self.assertEqual(self.store.highest_seq(self.aman.mailbox_id), 1)
        self.store.conn.execute(
            "UPDATE messages SET stored_at = '2000-01-01T00:00:00Z' WHERE mailbox_id = ?",
            (self.aman.mailbox_id,),
        )
        self.store.prune(self.aman.mailbox_id, ttl_days=30)
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE mailbox_id = ?",
                (self.aman.mailbox_id,),
            ).fetchone()["n"],
            0,
        )
        self.assertEqual(self.store.highest_seq(self.aman.mailbox_id), 1)
        self.aman.send("new")
        self.assertEqual(self.store.highest_seq(self.aman.mailbox_id), 2)


class ConcurrencyRestartTests(unittest.TestCase):
    def test_concurrent_posts_kill_restart_tiny_pages(self) -> None:
        import tempfile

        td = tempfile.TemporaryDirectory()
        db = str(Path(td.name) / "mb.sqlite")
        store = Store(db)
        bundle = provision_dev(store, relay_url="http://127.0.0.1")
        store.close()

        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        proc = _start_server(db, port)
        try:
            _wait_port(port)
            tr = HttpTransport(base, bundle["mailbox_id"], timeout=8.0)
            ids = bundle["identities"]

            def client(name: str, peer: str) -> MailboxClient:
                return MailboxClient(
                    participant=name,
                    mailbox_id=bundle["mailbox_id"],
                    identity=ids[name],
                    peer=PeerPublic(peer, ids[peer].enc_pub_raw, ids[peer].sign_pub_raw),
                    send_token=bundle["tokens"][f"{name}_send"],
                    fetch_token=bundle["tokens"][f"{name}_fetch"],
                    transport=tr,
                )

            aman = client("aman", "gauri")
            gauri = client("gauri", "aman")
            envelopes = []
            for i in range(50):
                envelopes.append(("aman", aman.compose(f"a-{i}")))
                envelopes.append(("gauri", gauri.compose(f"g-{i}")))

            accepted: dict[str, dict] = {}
            errors = []

            def post_one(item):
                who, env = item
                wire = {k: v for k, v in env.items() if not k.startswith("_")}
                token = bundle["tokens"][f"{who}_send"]
                delay = os.urandom(1)[0] / 255.0 * 0.05
                time.sleep(delay)
                last = None
                for attempt in range(30):
                    try:
                        res = tr("POST", wire, token=token)
                        return env["message_id"], res
                    except (HttpError, OSError, TimeoutError) as exc:
                        last = exc
                        if isinstance(exc, HttpError) and exc.status in (409,):
                            raise
                        time.sleep(0.05)
                raise RuntimeError(f"give up {env['message_id']}: {last}")

            killer_done = {"killed": False}

            def killer():
                time.sleep(0.08)
                nonlocal proc
                if proc.poll() is None:
                    proc.send_signal(signal.SIGKILL)
                    proc.wait()
                    killer_done["killed"] = True
                    time.sleep(0.05)
                    proc = _start_server(db, port)
                    _wait_port(port)

            with ThreadPoolExecutor(max_workers=32) as pool:
                kill_f = pool.submit(killer)
                futs = [pool.submit(post_one, item) for item in envelopes]
                for fut in as_completed(futs):
                    mid, res = fut.result()
                    accepted[mid] = res
                kill_f.result()

            self.assertTrue(killer_done["killed"])
            self.assertEqual(len(accepted), 100)
            seqs = [r["seq"] for r in accepted.values()]
            self.assertEqual(len(seqs), len(set(seqs)))
            self.assertEqual(sorted(seqs), list(range(1, 101)))

            # idempotent retry of first envelope
            who0, env0 = envelopes[0]
            wire0 = {k: v for k, v in env0.items() if not k.startswith("_")}
            retry = tr("POST", wire0, token=bundle["tokens"][f"{who0}_send"])
            self.assertTrue(retry["duplicate"])
            self.assertEqual(retry["seq"], accepted[env0["message_id"]]["seq"])

            conflict = dict(wire0)
            conflict["ct"] = b64encode(os.urandom(80))
            with self.assertRaises(HttpError) as cm:
                tr("POST", conflict, token=bundle["tokens"][f"{who0}_send"])
            self.assertEqual(cm.exception.status, 409)

            aman.drain(limit=7)
            gauri.drain(limit=7)
            stats = tr("STATS", None)
            self.assertEqual(aman.state.fetch_cursor, stats["highest_seq"])
            self.assertEqual(gauri.state.fetch_cursor, stats["highest_seq"])
            self.assertEqual(len(gauri.state.dispatched), 50)
            self.assertEqual(len(aman.state.dispatched), 50)
            self.assertEqual(len({d["message_id"] for d in gauri.state.dispatched}), 50)
            self.assertEqual(len({d["message_id"] for d in aman.state.dispatched}), 50)
            self.assertEqual(gauri.state.process_cursor["aman"], 50)
            self.assertEqual(aman.state.process_cursor["gauri"], 50)
            self.assertEqual(
                [d["sender_seq"] for d in gauri.state.dispatched], list(range(1, 51))
            )
            self.assertEqual(
                [d["sender_seq"] for d in aman.state.dispatched], list(range(1, 51))
            )
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            td.cleanup()


def _start_server(db: str, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "muse_mailbox.http_server",
            "--db",
            db,
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_port(port: int, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"server did not bind :{port}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
