"""tools/video/record.py の、ブラウザを使わない部分（台本の検査、コマの表示時間、concat のリスト）の試験。"""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "video"))
import record  # noqa: E402

GOOD = [{"id": "A", "url_path": "/", "steps": [{"wait": 1}, {"click": "#a"}, {"type": ["#q", "x"]},
                                                {"paste": ["#q", "y"]}, {"eval": "1"}, {"scroll": [None, 10]},
                                                {"scroll": ["#l", -5]}, {"wait_for": "#z"}]}]


def test_shipped_takes_are_valid():
    data = json.loads((ROOT / "tools" / "video" / "takes.json").read_text(encoding="utf-8"))
    assert record.validate_takes(data) == []


def test_good_takes():
    assert record.validate_takes(GOOD) == []
    assert record.validate_takes([dict(GOOD[0], size="1280x720", network_log=True, ready="#x")]) == []


def test_bad_takes():
    def errs(mod):
        t = copy.deepcopy(GOOD)
        mod(t[0])
        return record.validate_takes(t)
    assert errs(lambda t: t.pop("id"))
    assert errs(lambda t: t.update(url_path="index.html"))
    assert errs(lambda t: t.update(size="big"))
    assert errs(lambda t: t.update(network_log="yes"))
    assert errs(lambda t: t.update(steps=[]))
    assert errs(lambda t: t.update(steps=[{"jump": 1}]))
    assert errs(lambda t: t.update(steps=[{"wait": -1}]))
    assert errs(lambda t: t.update(steps=[{"type": ["#q"]}]))
    assert errs(lambda t: t.update(steps=[{"scroll": ["#a", "x"]}]))
    assert errs(lambda t: t.update(steps=[{"wait": 1, "click": "#a"}]))
    assert record.validate_takes([]) and record.validate_takes({"id": "A"})
    assert len(record.validate_takes(GOOD + GOOD)) == 1  # id の重複


def test_parse_size():
    assert record.parse_size(None) == (1920, 1080)
    assert record.parse_size("1280x720") == (1280, 720)
    assert record.parse_size([800, 600]) == (800, 600)


def test_frame_durations_follow_timestamps():
    d = record.frame_durations([100.0, 100.5, 102.0], 103.0)
    assert d == [0.5, 1.5, 1.0]
    assert abs(sum(d) - 3.0) < 1e-9  # 録画全体の長さが保たれる


def test_frame_durations_no_drift_and_zero():
    # 1/30 秒より短い間隔のコマは 0 になり、合計は丸め誤差でずれない
    ts = [0.0, 0.01, 0.1, 0.2005, 0.3]
    d = record.frame_durations(ts, 1.0)
    assert d[0] == 0 and abs(sum(d) - 1.0) < 1 / 30
    assert record.frame_durations([], 1.0) == []
    assert record.frame_durations([5.0], 5.0) == [1 / 30]  # すぐ終わっても 1 コマは見せる


def test_concat_list():
    text = record.concat_list(["a.jpg", "b.jpg", "c.jpg"], [0.5, 0.0, 1.0])
    assert text.splitlines() == ["file 'a.jpg'", "duration 0.500000", "file 'c.jpg'", "duration 1.000000", "file 'c.jpg'"]
    assert "\r" not in text


def test_highlight_move_cursor_valid():
    t = dict(GOOD[0], cursor=True, steps=[{"highlight": ["#a", 1.5]}, {"move": "#b"}])
    assert record.validate_takes([t]) == []


def test_highlight_move_cursor_invalid():
    def errs(mod):
        t = copy.deepcopy(GOOD)
        mod(t[0])
        return record.validate_takes(t)
    assert errs(lambda t: t.update(cursor="yes"))
    assert errs(lambda t: t.update(steps=[{"highlight": "#a"}]))
    assert errs(lambda t: t.update(steps=[{"highlight": ["#a", 0]}]))
    assert errs(lambda t: t.update(steps=[{"highlight": ["", 1]}]))
    assert errs(lambda t: t.update(steps=[{"move": ""}]))


def test_cursor_enabled_cli_overrides():
    assert record.cursor_enabled({}, False) is False
    assert record.cursor_enabled({"cursor": True}, False) is True
    assert record.cursor_enabled({"cursor": False}, True) is True


def test_ease_in_out():
    assert record.ease_in_out(0) == 0 and record.ease_in_out(1) == 1
    assert abs(record.ease_in_out(0.5) - 0.5) < 1e-9
    assert record.ease_in_out(0.25) < 0.25 < record.ease_in_out(0.75) or record.ease_in_out(0.75) > 0.75
    assert record.ease_in_out(-1) == 0 and record.ease_in_out(2) == 1


def test_js_templates_format():
    assert "1500" in record.MOVE_JS % (10, 20, 1500)
    assert "400" in record.RING_JS % (10, 20, 400)
    assert '"#a"' in record.HIGHLIGHT_JS % ('"#a"', 1.5)
