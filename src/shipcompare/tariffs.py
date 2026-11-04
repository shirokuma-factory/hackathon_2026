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

TARIFF_FILES = {
    "takuhai": "sagawa_kanto.json",
    "rosen": "nittsu_tsumiawase.json",
    "jitbox": "yamato_jitbox.json",
    "kashikiri": "mlit_standard_fare_kanto.json",
}


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


def load_tariff(key: str, base: Path | None = None) -> dict[str, Any]:
    """key は TARIFF_FILES のキー（takuhai / rosen / jitbox / kashikiri）。"""
    path = (base or data_dir()) / "tariffs" / TARIFF_FILES[key]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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
