"""
webpush: Web Push to the phone's home-screen web app, with nothing but `cryptography`.

The page subscribes through its service worker (web/sw.js) and hands the subscription
(an endpoint at Apple's / Google's / Mozilla's push service plus two keys) to
sphere_web, which keeps it in SUBS_PATH. To notify, a JSON payload is encrypted for
that browser (RFC 8291, aes128gcm) and POSTed to the endpoint, signed with this
server's VAPID key (RFC 8292, generated on first use into KEY_PATH).

Push needs a secure context, so the page has to be opened over sphere_web's HTTPS port;
on iOS it also has to be added to the home screen and opened from there.
"""
import base64
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

STATE_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "floatingsphere")
KEY_PATH = os.path.join(STATE_DIR, "vapid-key.pem")
SUBS_PATH = os.path.join(STATE_DIR, "push-subscriptions.json")
# VAPID "sub": who to contact about this sender. Apple wants a mailto: or https: URL.
SUBJECT = "https://github.com/relidaning/floatingsphere"
# The server POSTs to whatever endpoint a subscription names, so only push services
# are accepted: nobody on the network can make it send requests anywhere else.
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")
SUBS_MAX = 10
TTL_S = 3600  # an undelivered push older than this is stale news; let the service drop it
# A device whose app has polled within this long is looking at the sessions already, so
# it isn't pushed to. The open app polls every 3s; going to the background clears it at once.
ACTIVE_S = 8

_lock = threading.Lock()
_key = None
_active = {}  # endpoint -> when that device's app last polled


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key, data):
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _raw(pub):
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _write_private(path, data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def vapid_key():
    global _key
    with _lock:
        if _key is None:
            try:
                with open(KEY_PATH, "rb") as f:
                    _key = serialization.load_pem_private_key(f.read(), None)
            except FileNotFoundError:
                _key = ec.generate_private_key(ec.SECP256R1())
                _write_private(KEY_PATH, _key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return _key


def public_key():
    """The VAPID public key, as the page's pushManager.subscribe() wants it."""
    return b64u(_raw(vapid_key().public_key()))


def encrypt(payload, p256dh, auth):
    """RFC 8291 aes128gcm body for one browser, a single record."""
    ua_pub, secret = unb64u(p256dh), unb64u(auth)
    eph = ec.generate_private_key(ec.SECP256R1())
    as_pub = _raw(eph.public_key())
    shared = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = _hmac(_hmac(secret, shared), b"WebPush: info\0" + ua_pub + as_pub + b"\1")
    salt = os.urandom(16)
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\0\1")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\0\1")[:12]
    body = AESGCM(cek).encrypt(nonce, payload + b"\2", None)  # \2: last (only) record
    return salt + struct.pack("!IB", 4096, len(as_pub)) + as_pub + body


def vapid_auth(endpoint):
    u = urllib.parse.urlsplit(endpoint)

    def seg(o):
        return b64u(json.dumps(o, separators=(",", ":")).encode())
    signing = seg({"typ": "JWT", "alg": "ES256"}) + "." + seg(
        {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": SUBJECT})
    r, s = decode_dss_signature(vapid_key().sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={signing}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={public_key()}"


# ---------------------------------------------------------------- subscriptions

def valid(sub):
    """A browser PushSubscription.toJSON() pointing at a known push service, or None."""
    try:
        endpoint, keys = sub["endpoint"], sub["keys"]
        u = urllib.parse.urlsplit(endpoint)
        host = u.hostname or ""
        if (u.scheme != "https" or len(endpoint) > 1024
                or not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)):
            return None
        if len(unb64u(keys["p256dh"])) != 65 or len(unb64u(keys["auth"])) != 16:
            return None
        return {"endpoint": endpoint, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]}}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def load_subs():
    try:
        with open(SUBS_PATH) as f:
            return [s for s in json.load(f) if valid(s)]
    except (OSError, ValueError, TypeError):
        return []


def _save_subs(subs):
    _write_private(SUBS_PATH, json.dumps(subs, indent=1).encode())


def add(sub):
    """Keep a subscription; True if this device wasn't subscribed before."""
    with _lock:
        subs = load_subs()
        if any(s["endpoint"] == sub["endpoint"] for s in subs):
            return False
        _save_subs((subs + [dict(sub, added=time.time())])[-SUBS_MAX:])
        return True


def remove(endpoint):
    with _lock:
        subs = load_subs()
        kept = [s for s in subs if s["endpoint"] != endpoint]
        if len(kept) != len(subs):
            _save_subs(kept)


def set_active(endpoint, on=True):
    """The app on this device is open (it polled) or just went to the background."""
    if not isinstance(endpoint, str) or not endpoint:
        return
    if on:
        if len(_active) > 4 * SUBS_MAX:  # a header anyone can send: don't let it grow
            _active.clear()
        _active[endpoint] = time.time()
    else:
        _active.pop(endpoint, None)


def send(sub, data):
    """Push `data` (a dict for sw.js) to one subscription; returns the HTTP status."""
    body = encrypt(json.dumps(data).encode(), sub["keys"]["p256dh"], sub["keys"]["auth"])
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(TTL_S), "Urgency": "high", "Authorization": vapid_auth(sub["endpoint"])})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def send_all(data):
    """Push to every subscribed device, dropping the ones the push service says are gone."""
    subs = load_subs()
    if not subs:
        print("push: no subscribed devices", flush=True)
    for sub in subs:
        if time.time() - _active.get(sub["endpoint"], 0) < ACTIVE_S:
            print(f"push: {urllib.parse.urlsplit(sub['endpoint']).hostname}: app open, skipped", flush=True)
            continue
        code = send(sub, data)
        if code in (404, 410):  # unsubscribed / app removed from the home screen
            remove(sub["endpoint"])
        elif not 200 <= code < 300:
            print(f"push: {urllib.parse.urlsplit(sub['endpoint']).hostname} answered {code}", flush=True)
