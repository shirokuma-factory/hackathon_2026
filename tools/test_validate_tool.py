import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate as v

XML = """<testsuites><testsuite failures="1" errors="0">
<testcase classname="tests.test_a" name="test_x[1]"/>
<testcase classname="tests.test_a" name="test_x[2]"><failure message="m"/></testcase>
<testcase classname="tests.test_a" name="test_spec13_1_ok"/>
<testcase classname="tests.test_a.TestK" name="test_y"/>
</testsuite></testsuites>"""


def test_fingerprint_stable():
    assert v.fingerprint() == v.fingerprint()


def test_junit_parse_parametrized():
    r = v.parse_junit(XML)
    assert r["tests/test_a.py::test_x"] is False
    assert r["tests/test_a.py::test_spec13_1_ok"] is True
    assert r["tests/test_a.py::TestK::test_y"] is True


def test_prefix_matching():
    r = v.parse_junit(XML)
    names = [k.split("::")[-1] for k in r]
    assert any(n.startswith("test_spec13_1_") for n in names)


def png(w, h):
    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))


def test_image_width():
    assert v.image_width(png(1920, 1080)) == 1920
    jpg = b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x0b\x08\x04\x38\x07\x80\x01\x01\x11\x00"
    assert v.image_width(jpg) == 1920
    assert v.image_width(b"abc") is None
