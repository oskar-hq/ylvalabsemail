"""Web-Push ohne App-Store: Benachrichtigungen an die installierte Web-App (iPhone ab iOS 16.4, Android, Desktop).

Umsetzung nach RFC 8291 (Verschlüsselung, aes128gcm) und RFC 8292 (VAPID-Anmeldung beim Push-Dienst).
Der Schlüssel wird beim ersten Start erzeugt und im Datenordner abgelegt.
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


class Vapid:
    def __init__(self, key_path):
        if not os.path.exists(key_path):
            key = ec.generate_private_key(ec.SECP256R1())
            with open(key_path, "wb") as fh:
                fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
            os.chmod(key_path, 0o600)
        with open(key_path, "rb") as fh:
            self.key = serialization.load_pem_private_key(fh.read(), password=None)
        self.public_key = b64u(_raw_public(self.key))

    def auth_header(self, endpoint, subject):
        parts = urllib.parse.urlparse(endpoint)
        header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = b64u(json.dumps({"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + 12 * 3600,
                                  "sub": subject}).encode())
        signing_input = f"{header}.{claims}".encode()
        r, s = decode_dss_signature(self.key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        token = f"{header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        return f"vapid t={token}, k={self.public_key}"


def encrypt(payload, p256dh, auth):
    """Nachricht für genau dieses Gerät verschlüsseln (RFC 8291)."""
    ua_public = b64u_decode(p256dh)
    auth_secret = b64u_decode(auth)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_key)
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + ciphertext


def send(vapid, subscription, message, subject, ttl=86400):
    """Gibt True zurück, wenn zugestellt; False, wenn das Abo nicht mehr gilt (dann löschen)."""
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(),
                   subscription["keys"]["p256dh"], subscription["keys"]["auth"])
    req = urllib.request.Request(subscription["endpoint"], data=body, method="POST", headers={
        "Authorization": vapid.auth_header(subscription["endpoint"], subject),
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": str(ttl), "Urgency": "high",
    })
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return False
        raise
