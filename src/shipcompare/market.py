"""市場を見る（SPEC §16.1）の計算。物流センサス 2021 の加工値（data/stats/backhaul_2021.json）を読む。

画面（web/）はここで作った JSON を表示するだけで、不足量や S は計算し直さない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .tariffs import data_dir

# 自動車輸送統計年報 2025 年度 営業用普通車（付表 原単位）。資料 08 と同じ値
LOAD_FACTOR_PCT = 39.08  # 積載効率 %
ACTUAL_RATE_PCT = 62.15  # 実車率 %（走行距離のうち荷を積んでいた割合）

MODES = ("truck", "ferry", "rail")
FAR_KM = 200.0


def load_backhaul(base: Path | None = None) -> dict[str, Any]:
    with open((base or data_dir()) / "stats" / "backhaul_2021.json", encoding="utf-8") as f:
        return json.load(f)


def top_pairs(data: dict[str, Any], mode: str = "truck", min_km: float = FAR_KM, n: int = 30) -> list[dict[str, Any]]:
    """直線 min_km 以上の区間を、不足量の大きい順に n 件。同じ不足量なら距離の長い方を先に。"""
    pairs = [p for p in data["modes"][mode]["pairs"] if p["km"] >= min_km]
    pairs.sort(key=lambda p: (-p["shortfall_t"], -p["km"], p["from"], p["to"]))
    return pairs[:n]


def shortfall_share(data: dict[str, Any], mode: str = "truck") -> float:
    """県をまたぐ量のうち、逆向きに同じ量の荷が無い割合（0〜1）。"""
    m = data["modes"][mode]
    return m["shortfall_total_t"] / m["interpref_t"]


def scale_s(pairs: list[dict[str, Any]]) -> float:
    """S = Σ(不足量 × 直線距離) ÷ Σ(両向きの量 × 直線距離)。資料 08 §6。営業用トラックの全区間で計算する。"""
    num = sum(p["shortfall_t"] * p["km"] for p in pairs)
    den = sum((p["go_t"] + p["back_t"]) * p["km"] for p in pairs)
    return num / den


def actual_rate_after(x: float, s: float, current_pct: float = ACTUAL_RATE_PCT) -> float:
    """不足量の割合 x（0〜1）が帰りの区間に載ったときの実車率 %。実車率(後) = 実車率(今) × (1 + x × S)。
    予測ではなく幅の目安（前提は資料 08 §6 の 5 つ）。"""
    return current_pct * (1 + x * s)


def rate_curve(s: float, max_pct: float = 20.0, step_pct: float = 0.5) -> list[list[float]]:
    """画面の slider 用。[[X %, 実車率 %], ...]。"""
    out, k = [], 0
    while k * step_pct <= max_pct + 1e-9:
        x = k * step_pct
        out.append([x, round(actual_rate_after(x / 100, s), 3)])
        k += 1
    return out


def pair_from_label(label: str, pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """JARTIC の区間名（例 '兵庫–東京'、向きなし）に合う区間。多い向き（pairs の from→to）で返す。"""
    parts = label.replace("-", "–").split("–")
    if len(parts) != 2:
        return None
    a, b = (s.strip() for s in parts)

    def short(name: str) -> str:
        return name[:-1] if name.endswith(("県", "都", "府")) else name

    for p in pairs:
        if {short(p["from"]), short(p["to"])} == {a, b}:
            return p
    return None
