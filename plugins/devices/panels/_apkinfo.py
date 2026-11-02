"""The version of the published launcher, read from the APK itself (its binary AndroidManifest.xml)."""
import hashlib
import struct
import zipfile
from pathlib import Path


def manifest_attrs(raw: bytes) -> dict:
    """The attributes of the <manifest> tag of a binary AndroidManifest.xml: {name: int or str}."""
    pos = struct.unpack_from("<H", raw, 2)[0]
    strings, out = [], {}
    while pos + 8 <= len(raw):
        kind, hsize, size = struct.unpack_from("<HHI", raw, pos)
        if kind == 0x0001:                                      # the string pool
            count, _, flags, start = struct.unpack_from("<IIII", raw, pos + 8)
            for i in range(count):
                off = pos + start + struct.unpack_from("<I", raw, pos + hsize + 4 * i)[0]
                if flags & 0x100:                               # UTF-8: length in chars, then in bytes
                    off += 2 if raw[off] & 0x80 else 1
                    n = raw[off]
                    off += 1
                    if n & 0x80:
                        n, off = ((n & 0x7F) << 8) | raw[off], off + 1
                    strings.append(raw[off:off + n].decode("utf-8", "replace"))
                else:
                    n = struct.unpack_from("<H", raw, off)[0]
                    strings.append(raw[off + 2:off + 2 + 2 * n].decode("utf-16-le", "replace"))
        elif kind == 0x0102:                                    # the first start tag is <manifest>
            ext = pos + 16
            a_start, a_size, a_count = struct.unpack_from("<HHH", raw, ext + 8)
            for i in range(a_count):
                a = ext + a_start + i * a_size
                name, rawv, _, _, dtype, data = struct.unpack_from("<iiHBBI", raw, a + 4)
                out[strings[name]] = strings[rawv] if rawv >= 0 else (data if dtype == 0x10 else None)
            return out
        pos += size
    return out


def describe(apk: Path) -> dict:
    """{code, name, package, size, sha256} of an APK file, for the panels that update themselves."""
    with zipfile.ZipFile(apk) as z:
        attrs = manifest_attrs(z.read("AndroidManifest.xml"))
    data = apk.read_bytes()
    return {"code": attrs["versionCode"], "name": str(attrs.get("versionName") or ""),
            "package": str(attrs.get("package") or ""), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "path": "/plugins/panels/panel.apk"}
