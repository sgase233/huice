"""Create synthetic published policies for the M2 live test in its dedicated local database.

Uses the backend test fixture, which bypasses citation validation. Never use for real policies.
"""
from pathlib import Path
import os
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'services/api'), str(ROOT / 'services/api/tests')]

from app.db import Database
from app.integrations.storage import PolicyRepository
from test_db_evaluation import INCOME_GATE, INDUSTRY_GATE, publish_policy
import psycopg

url = os.environ.get('HUICE_M2_TEST_DSN', '')
parsed = urlsplit(url)
if parsed.scheme not in ('postgres', 'postgresql') or parsed.hostname != '127.0.0.1' or parsed.port != 55432 or parsed.path != '/huice_m2_phase3_test':
    raise SystemExit('只允许专用回环数据库 127.0.0.1:55432/huice_m2_phase3_test；请设置 HUICE_M2_TEST_DSN。')
with psycopg.connect(url) as connection:
    if connection.execute('SELECT count(*) FROM policy_version').fetchone()[0] != 0:
        raise SystemExit('测试库已有政策，拒绝写入；请使用新的空隔离库。')

db = Database(url)
try:
    repo = PolicyRepository(db, cursor_secret='m2-phase3-isolated-fixture')
    for title, expression in [('合成联调：行业排除', INDUSTRY_GATE), ('合成联调：缺少所得额', INCOME_GATE), ('合成联调：人工核查', {'op': 'manual_review', 'reason': '请人工核查合成条件'})]:
        policy_id, _ = publish_policy(repo, expressions=[expression], title=title, region='CN', issuer='合成测试机构', type='tax')
        print(f'{title}: {policy_id}')
finally:
    db.close()
