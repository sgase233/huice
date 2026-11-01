"""Add one synthetic grant to the dedicated M2 local integration database only."""
from pathlib import Path
import os
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'services/api'), str(ROOT / 'services/api/tests')]

import psycopg
from app.db import Database
from app.integrations.storage import PolicyRepository
from test_db_evaluation import CREDIT_GATE, INCOME_GATE, publish_policy

url = os.environ.get('HUICE_M2_TEST_DSN', '')
parsed = urlsplit(url)
if parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname != '127.0.0.1' or parsed.port != 55432 or parsed.path != '/huice_m2_phase3_test':
    raise SystemExit('只允许专用回环数据库 127.0.0.1:55432/huice_m2_phase3_test。')
title = '合成联调：资金多问题'
with psycopg.connect(url) as connection:
    if connection.execute('SELECT 1 FROM policy_version WHERE title = %s', (title,)).fetchone():
        raise SystemExit('资金测试政策已存在，不重复导入。')
    if connection.execute("SELECT count(*) FROM policy_version WHERE title LIKE '合成联调：%'").fetchone()[0] < 3:
        raise SystemExit('未找到阶段三合成夹具，拒绝添加。')

db = Database(url)
try:
    repo = PolicyRepository(db, cursor_secret='m2-phase3-isolated-fixture')
    policy_id, _ = publish_policy(repo, expressions=[INCOME_GATE, CREDIT_GATE], title=title, region='CN', issuer='合成测试机构', type='grant')
    print(f'{title}: {policy_id}（合成测试数据，不用于正式政策）')
finally:
    db.close()
