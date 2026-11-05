"""公開リポジトリに出してはいけないものが入っていないかを、毎回の試験で確かめる。

tools/validate.py の検査（秘密情報、PC 固有のパス、作成者の呼び名、改行）を試験から呼ぶ。
validate.py の採点は手で回すものなので、ここで常に回して、混入をその場で止める。
GitHub Actions（.github/workflows/test.yml）でも、push のたびに同じ試験が回る。
"""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_validate():
    spec = importlib.util.spec_from_file_location("validate_tool", ROOT / "tools" / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V = _load_validate()


def _tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if out.returncode != 0:
        pytest.skip("git が使えない環境")
    return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]


def test_no_env_file_is_tracked():
    bad = [p for p in _tracked()
           if Path(p).name != ".env.example" and (Path(p).name == ".env" or Path(p).name.startswith(".env."))]
    assert bad == []


def test_no_secret_like_strings():
    assert V.line_hits(V.SECRET_RES) == []


def test_no_local_paths_or_nickname():
    assert V.line_hits(V.PATH_RES) == []


# validate.py に無い形の秘密情報も見る（Anthropic・GitHub・Google・Hugging Face のトークン、JWT）。
EXTRA_SECRET_RES = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\."),
]


def test_no_extra_token_formats():
    assert V.line_hits(EXTRA_SECRET_RES) == []
