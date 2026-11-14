"""``infra/deploy/seed-e3a-samples.py``：三份 E3a 对账样本的造数。

锁住三件事：一次 run 能出现四态、改事实后读取视图变 `stale`、同一条政策先后发布
v1/v2 时两次 run 的 `policyVersionMap` 各自正确。样本只许在隔离库/本机库跑。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "deploy" / "seed-e3a-samples.py"

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("clean_tables")]


def _load():
    spec = importlib.util.spec_from_file_location("seed_e3a_samples", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("seed_e3a_samples", module)
    spec.loader.exec_module(module)
    return module


seeder = _load()


def test_the_samples_cover_four_states_and_a_stale_run(database_url_schema, tmp_path) -> None:
    out = tmp_path / "manifest.json"

    assert seeder.seed(database_url_schema, out) == 0

    manifest = json.loads(out.read_text(encoding="utf-8"))
    distribution = manifest["distribution"]

    assert distribution["assessmentsLength"] == 4
    assert distribution["counts"] == {
        "satisfied": 1,
        "not_satisfied": 1,
        "insufficient_evidence": 1,
        "conflict": 1,
    }
    # 改过事实之后读取视图里至少一条 stale
    assert distribution["staleCount"] >= 1
    # 每条政策只用了一次，版本映射覆盖四个样本版本
    assert set(distribution["policyVersionMap"].values()) == {
        "E3A-001",
        "E3A-002",
        "E3A-003",
        "E3A-004",
    }


def test_the_logic_tree_sample_uses_two_versions(database_url_schema, tmp_path) -> None:
    out = tmp_path / "manifest.json"
    assert seeder.seed(database_url_schema, out) == 0

    versions = [list(run["policyVersionMap"].values()) for run in json.loads(
        out.read_text(encoding="utf-8")
    )["logicTree"]["runs"]]

    assert versions == [["E3A-GXJS-v1"], ["E3A-GXJS-v2"]]


def test_a_second_run_refuses_to_add_more_sample_data(database_url_schema, tmp_path) -> None:
    assert seeder.seed(database_url_schema, tmp_path / "first.json") == 0
    assert seeder.seed(database_url_schema, tmp_path / "second.json") == 2
