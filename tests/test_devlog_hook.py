"""DEVLOG のフック（.claude/hooks/devlog.py）が、コミットのコマンドを見分けるかの試験。

11/3 のコミットの多くは `git -c user.name=Claude ... commit` の形で、
文字列 "git commit" を探す前の判定では記録されなかった（docs/DEVLOG.md 11/3 21:51 の件）。
"""
import importlib.util
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "devlog.py"


def _load():
    spec = importlib.util.spec_from_file_location("devlog_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'x'",
        "git add a && git commit -q -F - <<'EOF'\nmsg\nEOF",
        "git -c user.name=Claude commit -m x",
        "git -c user.name=Claude -c user.email=noreply@anthropic.com commit -q -F -",
        "git -C /c/dev/repo commit -m x",
        "git --no-pager commit --amend --no-edit",
    ],
)
def test_commit_commands_are_detected(command):
    assert _load().is_commit(command)


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --format='%h %s' | grep commit",
        "git commit-tree HEAD^{tree}",
        "echo 'see the commit list'",
        "git show -s --format=%s HEAD",
    ],
)
def test_other_commands_are_ignored(command):
    assert not _load().is_commit(command)
