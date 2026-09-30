"""NTAG 424 DNA SDM/SUN verification (NXP AN12196).

The tag mirrors an encrypted PICC block (UID + read counter) and a truncated
CMAC into its NDEF URL on every tap. Given the two AES-128 keys the tag was
personalised with, this module decrypts the block and re-computes the CMAC,
which proves the tap came from that physical tag and not from a replayed URL
(the counter is monotonic, the caller keeps the last value per UID).
"""

import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC

PICC_TAG_UID_CTR = 0xC7  # PICCDataTag: UID present + read counter present


def _cmac(key: bytes, msg: bytes) -> bytes:
    c = CMAC(algorithms.AES(key))
    c.update(msg)
    return c.finalize()


def _truncate(mac: bytes) -> bytes:
    """AN12196: the tag mirrors only the odd-indexed bytes of the full CMAC."""
    return bytes(mac[i] for i in range(1, 16, 2))


def decrypt_picc(meta_key: bytes, picc_data: bytes):
    """Decrypt the mirrored PICC block -> (uid, read_counter)."""
    if len(picc_data) != 16:
        raise ValueError(f"picc_data must be 16 bytes, got {len(picc_data)}")
    dec = Cipher(algorithms.AES(meta_key), modes.CBC(b"\x00" * 16)).decryptor()
    plain = dec.update(picc_data) + dec.finalize()
    if plain[0] != PICC_TAG_UID_CTR:
        raise ValueError(f"unexpected PICCDataTag 0x{plain[0]:02x} (wrong key?)")
    uid = plain[1:8]
    ctr = int.from_bytes(plain[8:11], "little")
    return uid, ctr


def session_mac_key(file_key: bytes, uid: bytes, ctr: int) -> bytes:
    """K_SesSDMFileReadMAC = CMAC(file_key, SV2), SV2 per AN12196 ch. 4.2."""
    sv2 = b"\x3c\xc3\x00\x01\x00\x80" + uid + ctr.to_bytes(3, "little")
    return _cmac(file_key, sv2)


def verify(meta_key: bytes, file_key: bytes, picc_data: bytes, cmac: bytes,
           enc_file_data: bytes = b""):
    """Verify one tap. Returns (uid, counter) or raises ValueError."""
    uid, ctr = decrypt_picc(meta_key, picc_data)
    expected = _truncate(_cmac(session_mac_key(file_key, uid, ctr), enc_file_data))
    if not hmac.compare_digest(expected, cmac):
        raise ValueError("CMAC mismatch")          # never log the computed value: it would open the door
    return uid, ctr
