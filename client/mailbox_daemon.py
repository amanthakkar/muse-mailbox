#!/usr/bin/env python3
"""Persistent mailbox daemon (Aman side) for muse-mailbox/2.

Polls the public /stats counter (no secrets); when the counter moves, fetches
+ decrypts + verifies via aman_mailbox.Mailbox (same cursors / seen_ids),
then spools each new message as JSON into spool/new/ for the hook's thin wake
worker to deliver. This daemon never wakes anyone itself.

ADAPTIVE POLLING (2026-09-19): to stay inside Cloudflare Workers' free daily
request budget, the daemon polls fast (5s) for FAST_WINDOW_SECS after any
activity, then backs off to POLL_IDLE_SECS (30s) when idle. Activity =
inbound counter movement, our own outbound sends (sender_seq advance in
state.json), or daemon start. Worst-case first-message latency after a long
silence is ~30s instead of ~5s; active conversations keep the fast feel.

Supervised by the muse-mailbox-watch hook script: if this process dies, the
next 5s hook run restarts it. Cursors live in ~/.muse-mailbox/state.json so a
restart never skips or repeats a message.

Spool layout (all 0600):
  spool/new/<relay_seq>.json        freshly fetched, awaiting hook pickup
  spool/pending/<relay_seq>.json    picked up by hook, worker delivering
  spool/delivered/<relay_seq>.json  delivered to main agent

Usage:
  python3 mailbox_daemon.py            # run forever
  python3 mailbox_daemon.py --once     # single poll+fetch cycle, then exit
"""
import fcntl
import json
import os
import sys
import time
import traceback

CLIENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CLIENT_DIR)
from aman_mailbox import Mailbox  # noqa: E402  (reuses tested fetch/decrypt/verify)

import requests  # noqa: E402

from mailbox_routing import (  # noqa: E402  (shared reply-routing contract)
    coerce_seq_set, load_list, resolve_chat, sticky_origin_chat,
)

HOME = os.path.expanduser("~/.muse-mailbox")
SPOOL_NEW = os.path.join(CLIENT_DIR, "spool", "new")
SPOOL_PENDING = os.path.join(CLIENT_DIR, "spool", "pending")
SPOOL_DELIVERED = os.path.join(CLIENT_DIR, "spool", "delivered")
PIDFILE = os.path.join(HOME, "daemon.pid")
LOCKFILE = os.path.join(HOME, "daemon.lock")
_LOG_FH = None  # held open so the instance lock survives for the process lifetime


def acquire_instance_lock():
    """Single-instance guard: non-blocking exclusive flock on LOCKFILE.

    Returns the open file handle (caller must keep it alive). Raises
    SystemExit if another daemon already holds the lock — this fixes the
    2026-09-27 double-start where two hook ticks raced and both daemons
    polled concurrently.
    """
    fh = open(LOCKFILE, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another mailbox_daemon is already running; exiting", file=sys.stderr)
        raise SystemExit(0)
    os.chmod(LOCKFILE, 0o600)
    return fh
LOGFILE = os.path.join(CLIENT_DIR, "daemon.log")
ACKED_PATH = os.path.join(HOME, "acked.json")       # message_ids we acked
CLOSED_THREADS_PATH = os.path.join(HOME, "closed_threads.json")
UA = "muse-mailbox/2 hatch-anton-daemon/1.0"
POLL_FAST_SECS = 5
POLL_IDLE_SECS = 30
FAST_WINDOW_SECS = 120  # stay fast this long after any activity
MAX_LOG_BYTES = 500_000
STATE_PATH = os.path.join(HOME, "state.json")


def read_sender_seq():
    """Our outbound send counter — a send means a reply may come soon."""
    try:
        with open(STATE_PATH) as f:
            return int(json.load(f).get("sender_seq", 0))
    except Exception:
        return 0


def log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(LOGFILE, "a") as f:
            f.write("ts=%s %s\n" % (ts, msg))
        if os.path.getsize(LOGFILE) > MAX_LOG_BYTES:
            with open(LOGFILE) as f:
                lines = f.readlines()[-2000:]
            with open(LOGFILE, "w") as f:
                f.writelines(lines)
    except Exception:
        pass


def get_stats(base, mailbox_id):
    r = requests.get(
        "%s/v1/mailboxes/%s/stats" % (base, mailbox_id),
        headers={"User-Agent": UA},
        timeout=10,
    )
    r.raise_for_status()
    return int(r.json().get("highest_seq", 0))


def spool(m):
    """Write one fetched message to spool/new/. Returns True if spooled."""
    if "body" not in m:
        log("skip seq=%s (%s)" % (m.get("seq"), m.get("error", "buffered")))
        return False
    name = "%d.json" % m["seq"]
    path = os.path.join(SPOOL_NEW, name)
    if os.path.exists(path):
        return False  # already spooled (e.g. after unclean restart)
    payload = {
        "relay_seq": m["seq"],
        "sender_seq": m.get("sender_seq"),
        "sender": "gauri",
        "type": m.get("type"),
        "status": m.get("status"),
        "sent_at": m.get("sent_at"),
        "body": m.get("body"),
        "message_id": m.get("message_id"),
        "in_reply_to": m.get("in_reply_to"),
        "return_to": m.get("return_to"),
        "to_channel": m.get("to_channel"),
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    log("spooled seq=%s sender_seq=%s type=%s" % (m["seq"], m.get("sender_seq"), m.get("type")))
    if m.get("out_of_order"):
        log("OUT-OF-ORDER RELEASE seq=%s sender_seq=%s — sender already past this seq (counter regression); released loudly, continuity guard NOT rewound" % (m["seq"], m.get("sender_seq")))
    return True


def load_acked():
    # Tolerates the same mixed int/dict corruption class as surfaced.json.
    return coerce_seq_set(load_list(ACKED_PATH))


def save_acked(acked):
    tmp = ACKED_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(acked), f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ACKED_PATH)


def record_closed_thread(root_mid, seq):
    try:
        cur = json.load(open(CLOSED_THREADS_PATH))
    except Exception:
        cur = []
    if root_mid not in cur:
        cur.append(root_mid)
        tmp = CLOSED_THREADS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cur, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CLOSED_THREADS_PATH)
    log("THREAD CLOSED by peer: root=%s (marker at relay seq=%s)" % (root_mid, seq))


def route_origin_chat():
    # Canonical sticky origin via the shared routing module (tolerates both
    # the canonical origin_chat_id key and the legacy chat_id key).
    return sticky_origin_chat()


def send_acks(mb, acked):
    """v2.2 read acknowledgments (Gauri cleared 2026-09-24).

    For every spooled non-ack message not yet acked, send
    {type:ack, in_reply_to:<mid>, status:seen} + return_to, consuming
    sender_seq via the normal send path. Acks are never acked.
    Idempotent: acked set persists in ~/.muse-mailbox/acked.json.
    """
    import glob
    changed = False
    paths = (glob.glob(os.path.join(SPOOL_NEW, "*.json")) +
             glob.glob(os.path.join(SPOOL_PENDING, "*.json")) +
             glob.glob(os.path.join(SPOOL_DELIVERED, "*.json")))
    for path in sorted(paths):
        try:
            with open(path) as f:
                p = json.load(f)
        except Exception:
            continue
        mtype = p.get("type") or "message"
        mid = p.get("message_id")
        if not mid or mtype == "ack" or mid in acked:
            continue
        if mtype == "marker":
            record_closed_thread(p.get("in_reply_to"), p.get("relay_seq"))
        # Ack return_to follows the shared routing contract: the ack for a
        # message routes to whichever chat that message belongs to
        # (to_channel -> in_reply_to -> sticky origin -> fallback).
        chat_id, _reason = resolve_chat(p)
        return_to = {"agent": "anton", "channel": chat_id}
        ret = p.get("return_to") or {}
        if ret.get("agent") == "iko" and ret.get("channel"):
            to_channel = {"agent": "iko", "channel": ret["channel"]}
        else:
            to_channel = {"agent": "iko", "channel": "K1"}
        try:
            res = mb.send(None, mtype="ack", status="seen",
                          in_reply_to=mid, return_to=return_to,
                          to_channel=to_channel)
            acked.add(mid)
            changed = True
            log("ack sent: relay_seq=%s acked_mid=%s ack_seq=%s" %
                (p.get("relay_seq"), mid[:8], res.get("seq")))
        except Exception:
            log("ack FAILED for relay_seq=%s mid=%s; will retry:\n%s" %
                (p.get("relay_seq"), mid[:8], traceback.format_exc()))
    if changed:
        save_acked(acked)


def poll_once(mb, last):
    """One stats check; fetch+spool if the counter moved. Returns new last."""
    cur = get_stats(mb.base, mb.mailbox_id)
    if cur > last:
        log("counter %d -> %d, fetching" % (last, cur))
        try:
            msgs = mb.fetch()
        except Exception:
            log("fetch failed:\n" + traceback.format_exc())
            return last  # do not advance; retry next poll
        n = 0
        for m in msgs:
            if spool(m):
                n += 1
        log("fetched %d new, spooled %d" % (len(msgs), n))
        return cur
    if cur < last:
        log("counter went backwards %d -> %d, re-baselining" % (last, cur))
        return cur
    return last


def main():
    global _LOG_FH
    once = "--once" in sys.argv[1:]
    for d in (SPOOL_NEW, SPOOL_PENDING, SPOOL_DELIVERED):
        os.makedirs(d, exist_ok=True)
    if not once:
        # Single-instance guard: exits(0) if another daemon holds the lock.
        _LOG_FH = acquire_instance_lock()
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
        os.chmod(PIDFILE, 0o600)

    mb = Mailbox()
    log("daemon start once=%s mailbox=%s" % (once, mb.mailbox_id))
    acked = load_acked()

    # Drain anything already pending (idempotent via cursors/seen_ids).
    try:
        for m in mb.fetch():
            spool(m)
        send_acks(mb, acked)
    except Exception:
        log("boot fetch failed:\n" + traceback.format_exc())

    try:
        last = get_stats(mb.base, mb.mailbox_id)
    except Exception:
        log("boot stats failed:\n" + traceback.format_exc())
        last = mb.state.get("fetch_cursor", 0)
    log("baseline highest_seq=%d" % last)

    if once:
        poll_once(mb, last)
        send_acks(mb, acked)
        log("once done")
        return

    fails = 0
    last_activity = time.time()  # daemon start counts as activity: fast first
    last_sender_seq = read_sender_seq()
    fast = True
    log("adaptive polling: fast=%ds for %ds after activity, idle=%ds"
        % (POLL_FAST_SECS, FAST_WINDOW_SECS, POLL_IDLE_SECS))
    while True:
        try:
            new_last = poll_once(mb, last)
            if new_last != last:
                last_activity = time.time()  # inbound activity
            last = new_last
            send_acks(mb, acked)  # v2.2: ack newly spooled messages
            cur_sender_seq = read_sender_seq()
            if cur_sender_seq != last_sender_seq:
                last_sender_seq = cur_sender_seq
                last_activity = time.time()  # our own send: reply may follow
            fails = 0
        except Exception:
            fails += 1
            log("poll cycle failed (%d):\n" % fails + traceback.format_exc())
        if fails >= 6:
            interval = 30  # sustained failure backoff (unchanged)
        elif time.time() - last_activity < FAST_WINDOW_SECS:
            interval = POLL_FAST_SECS
        else:
            interval = POLL_IDLE_SECS
        if (interval == POLL_FAST_SECS) != fast:
            fast = (interval == POLL_FAST_SECS)
            log("poll mode -> %s" % ("fast" if fast else "idle"))
        time.sleep(interval)


if __name__ == "__main__":
    main()
