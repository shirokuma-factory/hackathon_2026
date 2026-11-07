"""data/tariffs の読み込みと、区間・出典の定義。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ROUTE_OTHER

# 必ず持つメタデータ項目（受入条件 G06）。applies_from は資料に記載がなければ null
REQUIRED_METADATA = ("carrier", "service", "source_url", "checked_on", "tax", "scope_note", "verified_by")

# 宅配・路線便・ボックス便の表は、実在の会社の運賃の抜粋が data/tariffs/private/（git に入れない）にあればそれを、
# なければ架空の見本（data/tariffs/sample_*.json）を読む。見本のとき、読んだ辞書の is_sample が True になる
TARIFF_FILES = {
    "takuhai": "sagawa_kanto.json",
    "rosen": "nittsu_tsumiawase.json",
    "jitbox": "yamato_jitbox.json",
    "kashikiri": "mlit_standard_fare_kanto.json",
}
SAMPLE_FILES = {
    "takuhai": "sample_parcel.json",
    "rosen": "sample_ltl.json",
    "jitbox": "sample_jitbox.json",
}
PRIVATE_DIR = "private"


# 貸切の距離制運賃の運輸局別の表（SPEC §5.5 とその訂正）。既定は関東
KASHIKIRI_REGION_FILES = {
    "関東": "mlit_standard_fare_kanto.json",
    "近畿": "mlit_standard_fare_kinki.json",
}
WAITING_FILE = "mlit_standard_fare_waiting.json"


@dataclass(frozen=True)
class Route:
    id: str
    label: str
    km: int | None  # other は None（利用者が入力）
    origin_in_kanto: bool | None


# 想定の道路距離（§5.1）。距離は想定値
ROUTES: dict[str, Route] = {
    "kanto_kansai": Route("kanto_kansai", "関東 → 関西", 500, True),
    "kanto_tohoku": Route("kanto_tohoku", "関東 → 東北", 350, True),
    "sapporo_tokyo": Route("sapporo_tokyo", "札幌圏 → 東京", 1200, False),
    ROUTE_OTHER: Route(ROUTE_OTHER, "その他", None, None),
}


@dataclass(frozen=True)
class Source:
    """目安額に添える出典。"""

    name: str
    url: str
    applies_from: str | None
    checked_on: str


def data_dir() -> Path:
    """data/ の場所。環境変数 SHIPCOMPARE_DATA_DIR があればそれを使う。"""
    env = os.environ.get("SHIPCOMPARE_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data"


def tariff_path(key: str, base: Path | None = None) -> tuple[Path, bool]:
    """表のファイルの場所と、見本（架空の表）かどうか。private/ に実在の表があればそれを優先する。"""
    root = (base or data_dir()) / "tariffs"
    if key in SAMPLE_FILES:
        private = root / PRIVATE_DIR / TARIFF_FILES[key]
        if private.is_file():
            return private, False
        return root / SAMPLE_FILES[key], True
    return root / TARIFF_FILES[key], False


def load_tariff(key: str, base: Path | None = None) -> dict[str, Any]:
    """key は TARIFF_FILES のキー（takuhai / rosen / jitbox / kashikiri）。
    宅配・路線便・ボックス便で架空の見本を読んだときは、is_sample が True になる。"""
    path, is_sample = tariff_path(key, base)
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    if key in SAMPLE_FILES:
        t["is_sample"] = is_sample
    return t


def is_sample_tariff(key: str, base: Path | None = None) -> bool:
    """その表が架空の見本かどうか。画面や出力で「架空の表」と示すのに使う。"""
    return tariff_path(key, base)[1]


def load_kashikiri(region: str = "関東", base: Path | None = None) -> dict[str, Any]:
    """貸切の距離制運賃の表を運輸局の地域で選ぶ（既定は関東）。"""
    if region not in KASHIKIRI_REGION_FILES:
        raise KeyError(f"貸切の表がない地域です: {region}")
    path = (base or data_dir()) / "tariffs" / KASHIKIRI_REGION_FILES[region]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_waiting_tariff(base: Path | None = None) -> dict[str, Any]:
    """待機時間料・積込料・取卸料の表（SPEC §23）。"""
    with open((base or data_dir()) / "tariffs" / WAITING_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_vehicle_decks(base: Path | None = None) -> dict[str, Any]:
    """貸切の車格ごとの荷台の寸法・最大積載量（data/vehicle_decks.json）。積付け・貸切の候補・比べるページ・空きを探すが共通で読む。"""
    with open((base or data_dir()) / "vehicle_decks.json", encoding="utf-8") as f:
        return json.load(f)


def deck_of(deck_id: str, base: Path | None = None) -> dict[str, Any]:
    """荷台の ID（box_2t_short など）の寸法。"""
    for d in load_vehicle_decks(base)["decks"]:
        if d["id"] == deck_id:
            return d
    raise KeyError(f"荷台 {deck_id!r} は data/vehicle_decks.json にありません")


def deck_view(deck_id: str, base: Path | None = None) -> dict[str, Any]:
    """荷台の ID の寸法に、footprint_mm（長さ, 幅）と floor_m2（床の面積。長さ × 幅、m²、小数 2 桁）を添える。
    床の面積は必ず寸法の積（別の値を持たない。SPEC §39）。"""
    d = dict(deck_of(deck_id, base))
    d["footprint_mm"] = [d["length_mm"], d["width_mm"]]
    d["floor_m2"] = round(d["length_mm"] * d["width_mm"] / 1_000_000, 2)
    return d


def deck_2t(base: Path | None = None) -> dict[str, Any]:
    """2t ショートの荷台（洗い出し N7 の唯一の値）。deck_view に同じ。"""
    return deck_view(load_vehicle_decks(base)["tariff_class_deck"]["2t"], base)


FERRY_ONLY_FILE = "ferry_only_destinations.json"


def load_ferry_only(base: Path | None = None) -> dict[str, Any]:
    """道路で本州とつながっていない着地の一覧（data/geo/ferry_only_destinations.json）。"""
    with open((base or data_dir()) / "geo" / FERRY_ONLY_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_sample_tariffs(base: Path | None = None) -> dict[str, dict[str, Any]]:
    """宅配・路線便・ボックス便を必ず架空の見本で読む（実在の表があっても使わない）。試験が、手元に実在の表があるかどうかに左右されないために使う。"""
    out: dict[str, dict[str, Any]] = {}
    for key in TARIFF_FILES:
        if key in SAMPLE_FILES:
            with open((base or data_dir()) / "tariffs" / SAMPLE_FILES[key], encoding="utf-8") as f:
                out[key] = {**json.load(f), "is_sample": True}
        else:
            out[key] = load_tariff(key, base)
    return out


def load_all_tariffs(base: Path | None = None) -> dict[str, dict[str, Any]]:
    return {key: load_tariff(key, base) for key in TARIFF_FILES}


def source_of(tariff: dict[str, Any]) -> Source:
    return Source(
        name=f"{tariff['carrier']} {tariff['service']}",
        url=tariff["source_url"],
        applies_from=tariff.get("applies_from"),
        checked_on=tariff["checked_on"],
    )


def missing_metadata(tariff: dict[str, Any]) -> list[str]:
    """足りないメタデータ項目の名前。"""
    return [k for k in REQUIRED_METADATA if not tariff.get(k)]
