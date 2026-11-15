import { test, expect } from '@playwright/test';
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
});
test('审核员记录标题简洁，轨迹输入与按钮间有焦点空间', async ({ page }) => {
  await page.route('**/api/v1/assessment-runs', route => route.fulfill({ json: { items: [], nextCursor: null } }));
  await page.goto('/#reviewer');
  await expect(page.getByRole('heading', { name: '企业与运行记录', exact: true })).toBeVisible();
  await expect(page.getByText('服务端决定可见范围；此处不提供修改企业事实的操作。')).toHaveCount(0);
  await page.getByRole('button', { name: '处理轨迹' }).click();
  const input = page.getByLabel('选择运行记录'); const button = page.getByRole('button', { name: '读取轨迹' });
  await input.focus();
  const a = await input.boundingBox(); const b = await button.boundingBox();
  expect(a && b && b.y - (a.y + a.height)).toBeGreaterThanOrEqual(12);
});
test('政策导入日期提示与原生输入保持可用', async ({ page }) => {
  await page.goto('/#admin');
  const form = page.getByRole('heading', { name: '导入政策草稿' }).locator('..');
  await expect(form.getByPlaceholder('yyyy/mm/dd', { exact: true })).toHaveCount(3);
  await expect(form.getByPlaceholder('yyyy/mm/dd --:--', { exact: true })).toHaveCount(2);
  const date = page.getByLabel('发布日期', { exact: true });
  await date.fill('2026-11-15'); await expect(date).toHaveValue('2026-11-15');
  await date.clear(); await date.focus(); await expect(date).toHaveAttribute('placeholder', 'yyyy/mm/dd');
  await page.getByLabel('选择发布日').fill('2026-11-16'); await expect(date).toHaveValue('2026-11-16');
  await date.clear(); await date.focus();
  await page.screenshot({ path: 'test-results/policy-date-placeholder.png', fullPage: true });
});

test('导入成功清空表单和文件，失败保留输入', async ({ page }) => {
  let fail = true; let payload = '';
  await page.route('**/api/v1/admin/policies/import', route => { payload = route.request().postData() ?? ''; return fail ? route.fulfill({ status: 422, json: { error: { code: 'VALIDATION_ERROR', message: '检查输入', details: {} } } }) : route.fulfill({ json: { id: '11111111-1111-4111-8111-111111111111', version: 'v1', title: '合成政策', revision: 'r1', status: 'pending_parse', sources: [], criteria: [], validationFindings: [] }, headers: { ETag: '"r1"' } }); });
  await page.goto('/#admin');
  await page.getByLabel('版本', { exact: true }).fill('v1');
  await page.getByLabel('标题', { exact: true }).fill('合成政策');
  await page.getByLabel('发布机关').fill('测试机关');
  await page.getByLabel('地区', { exact: true }).fill('440300');
  await page.getByLabel('发布日期', { exact: true }).fill('2026/01/01');
  await page.getByLabel('生效日期', { exact: true }).fill('2026/01/01');
  await page.getByLabel('官方原文网址').fill('https://www.gov.cn/');
  await page.getByLabel('官方办理网址').fill('https://www.gov.cn/');
  await page.getByLabel('适用主体（每行一项）').fill('企业');
  await page.getByLabel('政策正文（PDF 或 UTF-8 文本）').setInputFiles('src/tests/fixtures/phase5-synthetic.txt');
  await page.getByRole('button', { name: '导入草稿' }).click();
  await expect(page.getByRole('alert')).toContainText('检查输入');
  await expect(page.getByLabel('标题', { exact: true })).toHaveValue('合成政策');
  await expect(page.getByLabel('发布日期', { exact: true })).toHaveValue('2026/01/01');
  fail = false;
  await page.getByRole('button', { name: '导入草稿' }).click();
  await expect(page.getByRole('heading', { name: '合成政策' })).toBeVisible();
  expect(payload).toContain('\"publishedAt\":\"2026-01-01\"');
  await expect(page.getByLabel('标题', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('发布日期', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('政策正文（PDF 或 UTF-8 文本）')).toHaveValue('');
});

test('审核草稿避免重复展示相同标题和引文，操作按钮留有间距', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const quote = '第一条 应纳税所得额不超过100万元。';
  const span = { sourceId: id, sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', page: null, section: '第一条', quote, textHash: 'hash' };
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '测试机关', revision: id, status: 'pending_review', sources: [{ id, title: '正文', sourceUrl: 'https://www.gov.cn/', text: quote }], criteria: [{ id, title: quote, expression: { op: 'manual_review', reason: '人工核对' }, sourceSpans: [span], reviewFinding: 'none', validationStatus: 'passed' }, { id: '22222222-2222-4222-8222-222222222222', title: '其他条件', expression: { op: 'manual_review', reason: '人工核对' }, sourceSpans: [{ ...span, quote: '其他原文', section: '第二条', page: 2 }], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => route.fulfill({ json: route.request().url().includes('/admin/policies/') ? draft : { items: [draft], nextCursor: null }, headers: { ETag: '"revision"', 'Access-Control-Expose-Headers': 'ETag' } }));
  await page.goto('/#admin');
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /合成草稿/ }).click();
  const panel = page.locator('#admin-policy-draft');
  await expect(panel.getByRole('heading', { name: '条件 1 · 校验通过' })).toBeVisible();
  await expect(panel.locator('blockquote').first()).toHaveText(quote);
  await expect(panel.getByText('来源位置：第二条 · 第 2 页')).toBeVisible();
  await expect(panel.getByRole('heading', { name: '其他条件 · 校验通过' })).toBeVisible();
  const add = await panel.getByRole('button', { name: '添加条件', exact: true }).boundingBox();
  const save = await panel.getByRole('button', { name: '保存并校验' }).boundingBox();
  expect(add && save && save.x - (add.x + add.width)).toBeGreaterThanOrEqual(8);
  const check = await panel.getByLabel('我已人工核对条件及来源').boundingBox();
  const publish = await panel.getByRole('button', { name: '确认发布' }).boundingBox();
  expect(check && publish && publish.y - (check.y + check.height)).toBeGreaterThanOrEqual(8);
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 850 });
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    const criterion = panel.locator('.admin-criterion').first();
    for (const name of ['删除引用', '添加引用']) {
      const button = criterion.getByRole('button', { name });
      const buttonBox = await button.boundingBox();
      const borderBox = await button.locator('xpath=ancestor::fieldset[1]').boundingBox();
      expect(buttonBox && borderBox && buttonBox.x - borderBox.x).toBeGreaterThanOrEqual(8);
      expect(buttonBox && borderBox && borderBox.y + borderBox.height - (buttonBox.y + buttonBox.height)).toBeGreaterThanOrEqual(8);
    }
    const outer = await criterion.locator(':scope > fieldset').boundingBox();
    const remove = await criterion.getByRole('button', { name: '删除条件', exact: true }).boundingBox();
    expect(outer && remove && remove.y - (outer.y + outer.height)).toBeGreaterThanOrEqual(8);
    const addAtWidth = await panel.getByRole('button', { name: '添加条件', exact: true }).boundingBox();
    const saveAtWidth = await panel.getByRole('button', { name: '保存并校验' }).boundingBox();
    expect(addAtWidth && saveAtWidth && (saveAtWidth.y > addAtWidth.y ? saveAtWidth.y - (addAtWidth.y + addAtWidth.height) : saveAtWidth.x - (addAtWidth.x + addAtWidth.width))).toBeGreaterThanOrEqual(8);
    const checkAtWidth = await panel.getByLabel('我已人工核对条件及来源').boundingBox();
    const publishAtWidth = await panel.getByRole('button', { name: '确认发布' }).boundingBox();
    expect(checkAtWidth && publishAtWidth && publishAtWidth.y - (checkAtWidth.y + checkAtWidth.height)).toBeGreaterThanOrEqual(8);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await criterion.screenshot({ path: `test-results/review-spacing-${width}.png` });
  }
});
