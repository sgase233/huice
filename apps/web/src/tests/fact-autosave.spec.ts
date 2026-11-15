import { expect, test } from '@playwright/test';
const eid = '11111111-1111-4111-8111-111111111111';
const period = { startDate: '2026-01-01', endDate: '2026-12-31' };
const tag = (v: string) => `"${v.repeat(64)}"`;
const headers = (v: string) => ({ ETag: tag(v), 'Access-Control-Expose-Headers': 'ETag' });
const auth = { user: { id: 'u', username: 'u', role: 'enterprise', enterpriseId: eid }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' };

test('画像自动保存和精确期间删除使用最新版本', async ({ page }) => {
  let version = 'a';
  let facts: any[] = [
    { key: 'region', value: '440300', unit: 'code', period, source: 'user_input', verified: false },
    { key: 'industry', value: 'C39', unit: 'code', period, source: 'user_input', verified: false },
    { key: 'taxableIncome', value: '100', unit: 'CNY', period, source: 'user_input', verified: false },
  ];
  const profile = () => ({ id: eid, nameSynthetic: '测试企业', period, factVersion: version, facts });
  const requests: string[] = [];
  await page.route('**/api/v1/**', route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname.endsWith('/auth/me')) return route.fulfill({ json: auth });
    if (url.pathname.endsWith(`/enterprises/${eid}`)) return route.fulfill({ json: profile(), headers: headers(version) });
    if (url.pathname.endsWith('/facts/taxableIncome')) {
      expect(req.headers()['if-match']).toBe(tag(version));
      requests.push(req.method());
      if (req.method() === 'PATCH') { facts = facts.map(f => f.key === 'taxableIncome' ? { ...f, ...req.postDataJSON() } : f); version = 'b'; }
      else if (req.method() === 'DELETE') { expect(url.searchParams.get('periodStart')).toBe(period.startDate); expect(url.searchParams.get('periodEnd')).toBe(period.endDate); facts = facts.filter(f => f.key !== 'taxableIncome'); version = 'c'; }
      return route.fulfill({ json: profile(), headers: headers(version) });
    }
    return route.abort();
  });
  await page.goto('/#enterprise');
  const card = page.locator('.fact-card').filter({ has: page.locator('select').filter({ has: page.locator('option[value="taxableIncome"]') }) }).nth(2);
  await card.getByLabel('值', { exact: true }).fill('250');
  await expect.poll(() => requests.length).toBe(1);
  await expect(page.getByRole('status')).toContainText('信息已保存');
  await card.getByRole('button', { name: '移除此项信息' }).hover();
  await expect(card.locator('.fact-delete-icon')).toBeVisible();
  await card.screenshot({ path: 'test-results/fact-delete-hover.png' });
  await card.getByRole('button', { name: '移除此项信息' }).click();
  await expect(page.locator('.fact-slot.is-removing')).toHaveCount(1);
  await expect.poll(() => requests.length).toBe(2);
  await expect(page.locator('.fact-card')).toHaveCount(2);
  expect(requests).toEqual(['PATCH', 'DELETE']);
});

test('删除冲突后读取最新画像并保留卡片供核对', async ({ page }) => {
  let reads = 0;
  const facts = [
    { key: 'region', value: '440300', unit: 'code', period, source: 'user_input', verified: false },
    { key: 'industry', value: 'C39', unit: 'code', period, source: 'user_input', verified: false },
    { key: 'taxableIncome', value: '100', unit: 'CNY', period, source: 'user_input', verified: false },
  ];
  await page.route('**/api/v1/**', route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname.endsWith('/auth/me')) return route.fulfill({ json: auth });
    if (url.pathname.endsWith(`/enterprises/${eid}`)) { reads++; return route.fulfill({ json: { id: eid, nameSynthetic: '测试企业', period, factVersion: 'a', facts }, headers: headers(reads === 1 ? 'a' : 'b') }); }
    if (url.pathname.endsWith('/facts/taxableIncome')) return route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '版本冲突', details: {} } } });
    return route.abort();
  });
  await page.goto('/#enterprise');
  await expect(page.locator('.fact-card')).toHaveCount(3);
  const initialReads = reads;
  await page.locator('.fact-card').nth(2).getByRole('button', { name: '移除此项信息' }).click();
  await expect(page.getByRole('alert')).toContainText('删除未确认');
  expect(reads).toBeGreaterThan(initialReads);
  await expect(page.locator('.fact-card')).toHaveCount(3);
});

test('新增卡片填写期间不会提前保存', async ({ page }) => {
  let writes = 0;
  const facts = [
    { key: 'region', value: '440300', unit: 'code', period, source: 'user_input', verified: false },
    { key: 'industry', value: 'C39', unit: 'code', period, source: 'user_input', verified: false },
  ];
  await page.route('**/api/v1/**', route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname.endsWith('/auth/me')) return route.fulfill({ json: auth });
    if (url.pathname.endsWith(`/enterprises/${eid}`)) return route.fulfill({ json: { id: eid, nameSynthetic: '测试企业', period, factVersion: 'a', facts }, headers: headers('a') });
    if (url.pathname.endsWith('/facts/taxableIncome')) { writes++; facts.push({ key: 'taxableIncome', ...req.postDataJSON(), verified: false }); return route.fulfill({ json: { id: eid, nameSynthetic: '测试企业', period, factVersion: 'b', facts }, headers: headers('b') }); }
    return route.abort();
  });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '添加一项信息' }).click();
  const card = page.locator('.fact-card').last();
  await card.getByLabel('信息类型').selectOption('taxableIncome');
  await expect(card.getByLabel('信息类型')).toBeEnabled();
  await page.waitForTimeout(1000);
  expect(writes).toBe(0);
  await card.getByLabel('期间开始').fill('2026-02-01');
  await page.getByRole('heading', { name: '企业信息与来源' }).click();
  await expect.poll(() => writes).toBe(1);
  await expect(card.getByLabel('期间开始')).toHaveValue('2026-02-01');
});
