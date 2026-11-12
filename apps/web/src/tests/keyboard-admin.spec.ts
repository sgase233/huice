import { expect, test, type Locator, type Page } from '@playwright/test';
const testSource = { id: 'test-source', title: '合成来源', text: '合成引文', sourceUrl: 'https://www.gov.cn/' };
const testSpan = { sourceId: 'test-source', sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', page: 1, section: null, quote: '合成引文', textHash: 'hash' };
test.use({ locale: 'zh-CN' });
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
});

async function tabTo(page: Page, target: Locator) {
  for (let i = 0; i < 160; i++) {
    if (await target.evaluate(element => document.activeElement === element)) return;
    await page.keyboard.press('Tab');
  }
  throw new Error('键盘 Tab 未能到达目标控件');
}

async function expectLabels(page: Page) {
  const missing = await page.locator('main input, main select, main textarea, main button').evaluateAll(elements => elements.filter(element => {
    if (!(element instanceof HTMLElement) || element.getClientRects().length === 0) return false;
    if (element instanceof HTMLButtonElement) return !element.textContent?.trim() && !element.getAttribute('aria-label');
    const control = element as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
    return !control.labels?.length && !control.getAttribute('aria-label') && !control.getAttribute('aria-labelledby');
  }).map(element => element.outerHTML.slice(0, 180)));
  expect(missing).toEqual([]);
}

test('审核员仅用键盘完成草稿编辑、保存和发布', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const criterion = { id: '22222222-2222-4222-8222-222222222222', title: '原条件', expression: { op: 'manual_review', reason: '人工核查' }, sourceSpans: [testSpan], reviewFinding: 'none', validationStatus: 'passed' };
  const draft = { id, version: 'v1', title: '键盘草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [testSource], criteria: [criterion], validationFindings: [] };
  let published = false;
  await page.route('**/api/v1/admin/policies**', async route => {
    const request = route.request();
    if (request.method() === 'PUT') {
      const input = request.postDataJSON();
      return route.fulfill({ headers: { ETag: '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: { draft: { ...draft, criteria: input.criteria.map((item: object) => ({ ...item, validationStatus: 'passed' })) }, criteria: [], validationFindings: [] } });
    }
    if (request.method() === 'POST') { published = true; return route.fulfill({ json: { id, version: 'v1', title: '键盘草稿', status: 'published', criteria: [] } }); }
    if (new URL(request.url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft], nextCursor: null } });
    return route.fulfill({ headers: { ETag: '"old"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#admin');
  await expect(page.getByRole('button', { name: '读取草稿' })).toBeVisible();
  await tabTo(page, page.getByRole('button', { name: '读取草稿' })); await page.keyboard.press('Enter');
  const openDraft = page.getByRole('button', { name: /键盘草稿/ });
  await expect(openDraft).toBeVisible(); await tabTo(page, openDraft); await page.keyboard.press('Enter');
  const title = page.getByRole('group', { name: '审核条件' }).getByLabel('标题', { exact: true });
  await expect(title).toBeVisible(); await tabTo(page, title);
  await page.keyboard.press('Control+A'); await page.keyboard.insertText('键盘修改');
  await tabTo(page, page.getByRole('button', { name: '添加条件', exact: true })); await page.keyboard.press('Enter');
  const added = page.getByRole('group', { name: '审核条件' }).last();
  await expect(added.getByLabel('标题', { exact: true })).toBeFocused();
  await page.keyboard.insertText('嵌套条件');
  const type = added.getByRole('combobox', { name: '类型' });
  await tabTo(page, type); page.once('dialog', dialog => dialog.accept()); await page.keyboard.press('Space'); await page.keyboard.press('Home'); await page.keyboard.press('Enter');
  await expect(added.getByRole('group', { name: '规则：且' })).toBeVisible();
  await expectLabels(page);
  for (const index of [0, 1]) {
    const reason = added.getByLabel('人工核查原因').nth(index); await tabTo(page, reason); await page.keyboard.insertText(`键盘核查${index + 1}`);
  }
  await tabTo(page, added.getByRole('button', { name: '添加子条件' })); await page.keyboard.press('Enter');
  const thirdReason = added.getByLabel('人工核查原因').last(); await tabTo(page, thirdReason); await page.keyboard.insertText('待删除');
  const remove = added.getByRole('button', { name: '删除子条件 3' });
  await tabTo(page, remove); page.once('dialog', dialog => dialog.accept()); await page.keyboard.press('Enter');
  await expect(added.getByRole('button', { name: '添加子条件' })).toBeFocused();
  await tabTo(page, added.getByRole('button', { name: '添加引用' })); await page.keyboard.press('Enter');
  await tabTo(page, added.getByLabel('页码')); await page.keyboard.insertText('1');
  await tabTo(page, added.getByLabel('引文（最多 2000 字）')); await page.keyboard.insertText('合成引文');
  const save = page.getByRole('button', { name: '保存并校验' });
  await tabTo(page, save); await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toHaveText('条件已保存，服务端校验结果已更新。');
  const confirmation = page.getByLabel('我已人工核对条件及来源');
  await tabTo(page, confirmation); await page.keyboard.press('Space');
  const publish = page.getByRole('button', { name: '确认发布' });
  await expect(publish).toBeEnabled(); await tabTo(page, publish); await page.keyboard.press('Enter');
  await expect(page.getByRole('status')).toContainText('发布请求已成功'); await expect(page.getByRole('alert')).toHaveCount(0); expect(published).toBe(true);
  await expect(page.getByRole('button', { name: '处理轨迹' })).toHaveCount(0);
});

test('键盘可以填写导入表单、选择正文并抽取', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '键盘导入', issuer: '演示机关', type: 'tax', region: '440300', revision: id, status: 'pending_parse', sources: [testSource], criteria: [], validationFindings: [] };
  let imports = 0;
  await page.route('**/api/v1/admin/policies**', route => {
    if (route.request().url().endsWith('/extract')) return route.fulfill({ headers: { ETag: '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: { draft: { ...draft, status: 'pending_review' }, criteriaDrafts: [], validationFindings: [], usage: { mode: 'fixture', modelVersion: null, inputTokens: 0, outputTokens: 0, elapsedMs: 1 } } });
    imports++;
    return route.fulfill({ headers: { ETag: '"old"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#admin');
  await expect(page.getByRole('button', { name: '导入草稿' })).toBeVisible();
  await expectLabels(page);
  for (const [label, value] of [['版本', 'v1'], ['标题', '键盘导入'], ['发布机关', '演示机关'], ['地区', '440300']] as const) {
    await tabTo(page, page.getByLabel(label, { exact: true })); await page.keyboard.insertText(value);
  }
  for (const label of ['发布日期', '生效日期']) {
    const date = page.getByLabel(label); await tabTo(page, date); await page.keyboard.type('2026'); await page.keyboard.press('ArrowRight'); await page.keyboard.type('01'); await page.keyboard.press('ArrowRight'); await page.keyboard.type('01');
    await expect(date).toHaveValue('2026-01-01');
  }
  for (const label of ['官方原文网址', '官方办理网址']) {
    await tabTo(page, page.getByLabel(label)); await page.keyboard.insertText('https://www.gov.cn/');
  }
  await tabTo(page, page.getByLabel('适用主体（每行一项）')); await page.keyboard.insertText('企业');
  const fileInput = page.getByLabel('政策正文（PDF 或 UTF-8 文本）');
  await tabTo(page, fileInput); await expect(fileInput).toBeFocused();
  const chooser = page.waitForEvent('filechooser'); await page.keyboard.press('Space');
  await (await chooser).setFiles('src/tests/fixtures/phase5-synthetic.txt');
  await tabTo(page, page.getByRole('button', { name: '导入草稿' })); await page.keyboard.press('Enter');
  const extract = page.getByRole('button', { name: '抽取条款' });
  await expect(extract).toBeEnabled(); await tabTo(page, extract); await page.keyboard.press('Enter');
  await expect(page.getByText(/抽取模式：固定样例/)).toBeVisible();
  expect(imports).toBe(1);
});
