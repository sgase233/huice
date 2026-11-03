import { test, expect } from '@playwright/test';

test('轨迹页展示未选择、加载、空结果和不存在状态', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route('**/api/v1/assessment-runs/*/trace', async route => {
    if (++calls === 1) { await gate; return route.fulfill({ json: { steps: [] } }); }
    return route.fulfill({ status: 404, json: { error: { code: 'RUN_NOT_FOUND', message: '没有找到对应的判断运行。', details: {} }, traceId: 't' } });
  });
  await page.goto('/#trace');
  await page.getByLabel('管理令牌').fill('test');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await expect(page.getByText(/尚未选择分析运行/)).toBeVisible();
  await page.getByLabel('分析运行 UUID').fill('11111111-1111-4111-8111-111111111111');
  await page.getByRole('button', { name: '读取轨迹' }).click();
  await expect(page.getByRole('button', { name: '正在加载…' })).toBeDisabled();
  release();
  await expect(page.getByText('该运行没有轨迹步骤。')).toBeVisible();
  await page.getByRole('button', { name: '读取轨迹' }).click();
  await expect(page.getByRole('alert')).toContainText('没有找到对应的判断运行');
  await page.getByLabel('分析运行 UUID').clear();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText(/尚未选择分析运行/)).toBeVisible();
});

test('受限轨迹携带内存令牌，退出后不再显示数据', async ({ page }) => {
  let authorization = '';
  await page.route('**/api/v1/assessment-runs/*/trace', async route => {
    authorization = route.request().headers().authorization ?? '';
    await route.fulfill({ json: { steps: [{ sequence: 1, stage: 'evaluation', status: 'ok', inputSummary: '合成输入', outputSummary: '合成输出', objectRefs: [{ type: 'policyVersion', id: '22222222-2222-4222-8222-222222222222' }], policyId: null, policyVersion: null, modelOrRuleVersion: 'test', elapsedMs: 3, errorCode: null, humanAction: null }] } });
  });
  await page.goto('/#trace');
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByLabel('分析运行 UUID').fill('11111111-1111-4111-8111-111111111111');
  await page.getByRole('button', { name: '读取轨迹' }).click();
  await expect(page.getByText('输出：合成输出')).toBeVisible();
  await expect(page.getByRole('heading', { name: '1. 规则判断 · 完成' })).toBeVisible();
  await expect(page.getByText('关联对象：政策版本 · 22222222-2222-4222-8222-222222222222')).toBeVisible();
  expect(authorization).toBe('Bearer test-token');
  await page.screenshot({ path: 'test-results/phase5-trace.png', fullPage: true });
  await page.getByRole('button', { name: '退出管理授权' }).click();
  await expect(page.getByText('输出：合成输出')).toHaveCount(0);
  await expect(page.getByLabel('管理令牌')).toBeVisible();
});

test('轨迹按序展示降级、失败、版本和人工动作', async ({ page }) => {
  const base = { inputSummary: '输入', outputSummary: '输出', objectRefs: [], policyId: null, policyVersion: null, modelOrRuleVersion: 'rule-v2', elapsedMs: 18, errorCode: null, humanAction: null };
  await page.route('**/api/v1/assessment-runs/*/trace', route => route.fulfill({ json: { steps: [
    { ...base, sequence: 2, stage: 'explanation', status: 'retryable_error', errorCode: 'DEPENDENCY_UNAVAILABLE', humanAction: '人工复核' },
    { ...base, sequence: 1, stage: 'citation_validation', status: 'needs_review', policyId: '22222222-2222-4222-8222-222222222222', policyVersion: 'v2' },
    { ...base, sequence: 3, stage: 'extraction', status: 'fatal_error', errorCode: 'FAILED' },
  ] } }));
  await page.goto('/#trace');
  await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByLabel('分析运行 UUID').fill('11111111-1111-4111-8111-111111111111');
  await page.getByRole('button', { name: '读取轨迹' }).click();
  const steps = page.locator('.trace-steps > li');
  await expect(steps).toHaveCount(3);
  await expect(steps.nth(0).getByRole('heading')).toHaveText('1. 引用校验 · 需要审核');
  await expect(steps.nth(0)).toContainText('版本：v2');
  await expect(steps.nth(1).getByRole('heading')).toHaveText('2. 结果解释 · 可重试错误');
  await expect(steps.nth(1)).toContainText('DEPENDENCY_UNAVAILABLE');
  await expect(steps.nth(1)).toContainText('人工复核');
  await expect(steps.nth(1)).toContainText('rule-v2');
  await expect(steps.nth(1)).toContainText('18 ms');
  await expect(steps.nth(2).getByRole('heading')).toHaveText('3. 条款抽取 · 处理失败');
});

for (const status of [200, 401]) {
  test(`退出授权后迟到的轨迹 ${status} 响应不影响新会话`, async ({ page }) => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route('**/api/v1/assessment-runs/*/trace', async route => {
      await gate;
      if (status === 401) return route.fulfill({ status, json: { error: { code: 'UNAUTHORIZED', message: '旧令牌失效', details: {} }, traceId: 'old' } });
      return route.fulfill({ json: { steps: [{ sequence: 1, stage: 'evaluation', status: 'ok', inputSummary: '输入', outputSummary: '旧会话数据', objectRefs: [], policyId: null, policyVersion: null, modelOrRuleVersion: 'v1', elapsedMs: 1, errorCode: null, humanAction: null }] } });
    });
    await page.goto('/#trace');
    await page.getByLabel('管理令牌').fill('old-token'); await page.getByRole('button', { name: '使用令牌' }).click();
    await page.getByLabel('分析运行 UUID').fill('11111111-1111-4111-8111-111111111111');
    const response = page.waitForResponse(result => result.url().includes('/trace'));
    await page.getByRole('button', { name: '读取轨迹' }).click();
    await expect(page.getByRole('button', { name: '正在加载…' })).toBeDisabled();
    await page.getByRole('button', { name: '退出管理授权' }).click();
    await page.getByLabel('管理令牌').fill('new-token'); await page.getByRole('button', { name: '使用令牌' }).click();
    release(); await response;
    await expect(page.getByRole('heading', { name: '智能体处理轨迹' })).toBeVisible();
    await expect(page.getByText('旧会话数据')).toHaveCount(0);
    await expect(page.getByText('管理令牌无效，请重新授权。')).toHaveCount(0);
  });
}

test('审核草稿编辑保存使用当前 ETag，发布需人工确认', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const criterion = { id: '22222222-2222-4222-8222-222222222222', title: '原条件', expression: { op: 'manual_review', reason: '人工核查' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' };
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [], criteria: [criterion], validationFindings: [] };
  let match = '';
  let published: { match: string; body: unknown } | null = null;
  let releaseSave!: () => void;
  const saveGate = new Promise<void>(resolve => { releaseSave = resolve; });
  await page.route('**/api/v1/admin/policies**', async route => {
    const request = route.request();
    if (request.method() === 'POST') {
      published = { match: request.headers()['if-match'], body: request.postDataJSON() };
      return route.fulfill({ json: { id, version: 'v1', title: '合成草稿', status: 'published', criteria: [] } });
    }
    if (request.method() === 'PUT') {
      match = request.headers()['if-match'] ?? '';
      const input = request.postDataJSON();
      await saveGate;
      await route.fulfill({ headers: { ETag: '"revision-2"', 'Access-Control-Expose-Headers': 'ETag' }, json: { draft: { ...draft, criteria: input.criteria.map((c: object) => ({ ...c, validationStatus: 'passed' })) }, criteria: [], validationFindings: [] } });
    } else if (new URL(request.url()).pathname.endsWith('/policies')) await route.fulfill({ json: { items: [draft], nextCursor: null } });
    else await route.fulfill({ headers: { ETag: '"revision-1"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '政策审核' }).click();
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click();
  await page.getByRole('button', { name: /合成草稿/ }).click();
  await page.getByLabel('标题', { exact: true }).last().fill('');
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByRole('alert')).toHaveText('请填写条件标题。');
  await expect(page.getByLabel('标题', { exact: true }).last()).toBeFocused();
  await expect(page.getByLabel('标题', { exact: true }).last()).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByLabel('标题', { exact: true }).last()).toHaveAccessibleDescription('请填写条件标题。');
  await page.getByLabel('标题', { exact: true }).last().fill('修改条件');
  await expect(page.getByLabel('标题', { exact: true }).last()).not.toHaveAttribute('aria-invalid');
  page.once('dialog', dialog => dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(/#admin$/);
  await expect(page.getByLabel('标题', { exact: true }).last()).toHaveValue('修改条件');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '清单与报告' }).click();
  await expect(page.getByLabel('标题', { exact: true }).last()).toHaveValue('修改条件');
  await expect(page.getByRole('button', { name: '确认发布' })).toBeDisabled();
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByLabel('标题', { exact: true }).last()).toBeDisabled();
  releaseSave();
  await expect(page.getByLabel('标题', { exact: true }).last()).toBeEnabled();
  await expect(page.getByRole('button', { name: '保存并校验' })).toBeDisabled();
  expect(match).toBe('"revision-1"');
  await expect(page.getByRole('status')).toHaveText('条件已保存，服务端校验结果已更新。');
  await page.getByLabel('我已人工核对条件及来源').check();
  await expect(page.getByRole('button', { name: '确认发布' })).toBeEnabled();
  await page.getByLabel('标题', { exact: true }).last().fill('再次修改');
  await expect(page.getByLabel('我已人工核对条件及来源')).not.toBeChecked();
  await expect(page.getByRole('button', { name: '确认发布' })).toBeDisabled();
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByRole('button', { name: '保存并校验' })).toBeDisabled();
  await page.getByLabel('我已人工核对条件及来源').check();
  await expect(page.getByRole('button', { name: '确认发布' })).toBeEnabled();
  await page.getByRole('button', { name: '确认发布' }).click();
  await expect(page.getByText(/发布请求已成功/)).toBeVisible();
  expect(published).toEqual({ match: '"revision-2"', body: { version: 'v1', reviewConfirmed: true } });
});

test('版本冲突保留本地修改，读取最新版本后逐条恢复', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const original = { id: '22222222-2222-4222-8222-222222222222', title: '原条件', expression: { op: 'manual_review', reason: '人工核查' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' };
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [], criteria: [original], validationFindings: [] };
  let reads = 0;
  await page.route('**/api/v1/admin/policies**', async route => {
    const req = route.request();
    if (req.method() === 'PUT') return route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '版本冲突', details: {} }, traceId: 'trace' } });
    if (new URL(req.url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft], nextCursor: null } });
    reads++;
    return route.fulfill({ headers: { ETag: reads === 1 ? '"old"' : '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: reads === 1 ? draft : { ...draft, criteria: [{ ...original, title: '远端条件' }] } });
  });
  await page.goto('/#admin');
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click();
  await page.getByRole('button', { name: /合成草稿/ }).click();
  await page.getByLabel('标题', { exact: true }).last().fill('本地条件');
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByText(/本地编辑已保留/)).toBeVisible();
  await page.getByRole('button', { name: '读取最新版本以对照' }).click();
  await expect(page.getByText('服务端：远端条件')).toBeVisible();
  await page.getByRole('button', { name: '以服务端版本为基础，逐条应用本地修改' }).click();
  await expect(page.getByRole('button', { name: '保存并校验' })).toBeDisabled();
  await page.getByRole('button', { name: '应用此条件' }).click();
  await expect(page.getByLabel('标题', { exact: true }).last()).toHaveValue('本地条件');
  await expect(page.getByRole('button', { name: '保存并校验' })).toBeEnabled();
});

test('离开并重新进入后可从草稿列表恢复已保存条件', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  let criterion = { id: '22222222-2222-4222-8222-222222222222', title: '原条件', expression: { op: 'manual_review', reason: '人工核查' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' };
  const draft = () => ({ id, version: 'v1', title: '重入草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [], criteria: [criterion], validationFindings: [] });
  let details = 0;
  await page.route('**/api/v1/admin/policies**', route => {
    const request = route.request();
    if (request.method() === 'PUT') {
      criterion = { ...request.postDataJSON().criteria[0], validationStatus: 'passed' };
      return route.fulfill({ headers: { ETag: '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: { draft: draft(), criteria: [criterion], validationFindings: [] } });
    }
    if (new URL(request.url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft()], nextCursor: null } });
    details++;
    return route.fulfill({ headers: { ETag: '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft() });
  });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /重入草稿/ }).click();
  await page.getByRole('group', { name: '审核条件' }).getByLabel('标题', { exact: true }).fill('保存后恢复');
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByRole('status')).toHaveText('条件已保存，服务端校验结果已更新。');
  await page.getByRole('button', { name: '处理轨迹' }).click();
  await page.getByRole('button', { name: '政策审核' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /重入草稿/ }).click();
  await expect(page.getByRole('group', { name: '审核条件' }).getByLabel('标题', { exact: true })).toHaveValue('保存后恢复');
  expect(details).toBe(2);
});

test('导入结果不确定时先核对列表才能再次导入', async ({ page }) => {
  let imports = 0;
  await page.route('**/api/v1/admin/policies**', async route => {
    if (route.request().method() === 'POST') { imports++; return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '服务不可用', details: {} }, traceId: 't' } }); }
    return route.fulfill({ json: { items: [], nextCursor: null } });
  });
  await page.goto('/#admin');
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByLabel('版本', { exact: true }).fill('v1');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '企业画像' }).click();
  await expect(page.getByLabel('版本', { exact: true })).toHaveValue('v1');
  await page.getByLabel('标题', { exact: true }).fill('合成政策');
  await page.getByLabel('发布机关').fill('演示机关');
  await page.getByLabel('地区', { exact: true }).fill('440300');
  await page.getByLabel('发布日期').fill('2026-01-01');
  await page.getByLabel('生效日期').fill('2026-01-01');
  await page.getByLabel('官方原文网址').fill('https://www.gov.cn/');
  await page.getByLabel('官方办理网址').fill('https://www.gov.cn/');
  await page.getByLabel('政策正文（PDF 或 UTF-8 文本）').setInputFiles('package.json');
  await page.getByRole('button', { name: '导入草稿' }).click();
  await expect(page.getByRole('alert')).toHaveText('至少填写一个适用主体。');
  await expect(page.getByLabel('适用主体（每行一项）')).toBeFocused();
  await expect(page.getByLabel('适用主体（每行一项）')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByLabel('适用主体（每行一项）')).toHaveAccessibleDescription('至少填写一个适用主体。');
  await page.getByLabel('适用主体（每行一项）').fill('企业');
  await expect(page.getByLabel('适用主体（每行一项）')).not.toHaveAttribute('aria-invalid');
  await page.getByLabel('生效日期').fill('2025-12-31');
  await page.getByRole('button', { name: '导入草稿' }).click();
  await expect(page.getByRole('alert')).toContainText('日期');
  await expect(page.getByLabel('生效日期')).toBeFocused();
  await page.getByLabel('生效日期').fill('2026-01-01');
  await page.getByRole('button', { name: '导入草稿' }).click();
  await expect(page.getByText(/导入结果不确定/)).toBeVisible();
  await expect(page.getByRole('button', { name: '导入草稿' })).toBeDisabled();
  await expect(page.getByLabel('版本', { exact: true })).toBeDisabled();
  expect(imports).toBe(1);
  await page.getByRole('button', { name: '读取草稿' }).click();
  await expect(page.getByRole('button', { name: '导入草稿' })).toBeEnabled();
  await expect(page.getByLabel('版本', { exact: true })).toBeEnabled();
});

test('抽取结果不确定时显式重试复用请求键和版本', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_parse', sources: [], criteria: [], validationFindings: [] };
  const keys: string[] = []; const matches: string[] = [];
  await page.route('**/api/v1/admin/policies**', async route => {
    const req = route.request();
    if (req.method() === 'POST') {
      keys.push(req.headers()['idempotency-key']); matches.push(req.headers()['if-match']);
      if (keys.length === 1) return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '暂不可用', details: {} }, traceId: 't' } });
      return route.fulfill({ headers: { ETag: '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: { draft: { ...draft, status: 'pending_review' }, criteriaDrafts: [], validationFindings: [], usage: { mode: 'fixture', modelVersion: null, inputTokens: 0, outputTokens: 0, elapsedMs: 1 } } });
    }
    if (new URL(req.url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft], nextCursor: null } });
    return route.fulfill({ headers: { ETag: '"old"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#admin');
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click();
  await page.getByRole('button', { name: /合成草稿/ }).click();
  await page.getByRole('button', { name: '抽取条款' }).click();
  await expect(page.getByText(/抽取结果不确定/)).toBeVisible();
  await page.getByRole('button', { name: '用原幂等键重试抽取' }).click();
  await expect(page.getByText('演示 · v1 · pending_review')).toBeVisible();
  await expect(page.getByText(/抽取模式：固定样例/)).toBeVisible();
  expect(keys).toHaveLength(2); expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]); expect(matches).toEqual(['"old"', '"old"']);
});

test('审核页表单无横向溢出，键盘可切换焦点', async ({ page }) => {
  await page.goto('/#admin');
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await page.screenshot({ path: 'test-results/phase5-admin-desktop.png', fullPage: true });
  await page.getByLabel('版本', { exact: true }).focus();
  await expect(page.getByLabel('版本', { exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByLabel('标题', { exact: true })).toBeFocused();
});

for (const status of [401, 403]) {
  test(`轨迹授权失败 ${status} 清除受限内容并给出对应提示`, async ({ page }) => {
    let count = 0;
    await page.route('**/api/v1/assessment-runs/*/trace', async route => {
      count++;
      if (count === 1) return route.fulfill({ json: { steps: [{ sequence: 1, stage: 'evaluation', status: 'ok', inputSummary: '输入', outputSummary: '受限输出', objectRefs: [], policyId: null, policyVersion: null, modelOrRuleVersion: 'test', elapsedMs: 1, errorCode: null, humanAction: null }] } });
      return route.fulfill({ status, json: { error: { code: 'DENIED', message: '拒绝', details: {} }, traceId: 'trace' } });
    });
    await page.goto('/#trace');
    await page.getByLabel('管理令牌').fill('secret');
    await page.getByRole('button', { name: '使用令牌' }).click();
    await page.getByLabel('分析运行 UUID').fill('11111111-1111-4111-8111-111111111111');
    await page.getByRole('button', { name: '读取轨迹' }).click();
    await expect(page.getByText('输出：受限输出')).toBeVisible();
    await page.getByRole('button', { name: '读取轨迹' }).click();
    await expect(page.getByText('输出：受限输出')).toHaveCount(0);
    await expect(page.getByText(status === 401 ? '管理令牌无效，请重新授权。' : '演示会话无效或已过期，请从演示入口重新进入。')).toBeVisible();
    await expect(page.getByLabel('管理令牌')).toHaveValue('');
  });
}

test('后台错误显示服务端字段详情和追踪 ID', async ({ page }) => {
  await page.route('**/api/v1/admin/policies**', route => route.fulfill({ status: 422, json: { error: { code: 'VALIDATION_ERROR', message: '校验失败', details: { fieldErrors: [{ field: 'sourceUrl', message: '网址无效' }] } }, traceId: 'trace-test' } }));
  await page.goto('/#admin');
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click();
  await expect(page.getByRole('alert')).toContainText('sourceUrl：网址无效');
  await expect(page.getByRole('alert')).toContainText('trace-test');
});

test('草稿弱 ETag 不允许写入', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '弱版本草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_parse', sources: [], criteria: [], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => new URL(route.request().url()).pathname.endsWith('/policies') ? route.fulfill({ json: { items: [draft], nextCursor: null } }) : route.fulfill({ headers: { ETag: 'W/"old"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft }));
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /弱版本草稿/ }).click();
  await expect(page.getByRole('button', { name: '抽取条款' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '读取最新版本以对照' })).toBeVisible();
  await expect(page.getByText('缺少有效的强 ETag，无法进行写操作。')).toBeVisible();
});

test('编辑多来源引用时保留来源与版本并按原文计算哈希', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const sources = [{ id: 'a', title: '正文', sourceUrl: 'https://www.gov.cn/', text: 'abc' }, { id: 'b', title: '附件', sourceUrl: 'https://www.gov.cn/', text: 'abc' }];
  const span = { sourceId: 'a', sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', page: 1, section: null, quote: 'old', textHash: 'old' };
  const criterion = { id: 'c', title: '条件', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [span], reviewFinding: 'none', validationStatus: 'passed' };
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources, criteria: [criterion], validationFindings: [] };
  let saved: any;
  await page.route('**/api/v1/admin/policies**', async route => {
    const request = route.request();
    if (request.method() === 'PUT') { saved = request.postDataJSON(); return route.fulfill({ headers: { ETag: '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: { draft: { ...draft, criteria: saved.criteria.map((item: object) => ({ ...item, validationStatus: 'passed' })) }, criteria: [], validationFindings: [] } }); }
    if (new URL(request.url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft], nextCursor: null } });
    return route.fulfill({ headers: { ETag: '"old"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /合成草稿/ }).click();
  await page.screenshot({ path: 'test-results/phase5-editor.png', fullPage: true });
  await page.getByRole('button', { name: '添加引用' }).click();
  await expect(page.getByRole('combobox', { name: '来源' }).last()).toBeFocused();
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByRole('textbox', { name: '引文' }).last()).toBeFocused();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '删除引用' }).last().click();
  await expect(page.getByRole('button', { name: '添加引用' })).toBeFocused();
  await page.getByRole('combobox', { name: '来源' }).selectOption('b');
  await page.getByRole('textbox', { name: '引文' }).fill('abc');
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect.poll(() => saved).toBeTruthy();
  expect(saved.criteria[0].sourceSpans[0]).toMatchObject({ sourceId: 'b', sourceVersion: 'v1', quote: 'abc', textHash: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' });
});

test('切换运行时迟到的轨迹响应不能覆盖新结果', async ({ page }) => {
  let releaseFirst!: () => void;
  const firstGate = new Promise<void>(resolve => { releaseFirst = resolve; });
  const makeStep = (text: string) => ({ steps: [{ sequence: 1, stage: 'evaluation', status: 'ok', inputSummary: '输入', outputSummary: text, objectRefs: [], policyId: null, policyVersion: null, modelOrRuleVersion: 'test', elapsedMs: 1, errorCode: null, humanAction: null }] });
  await page.route('**/api/v1/assessment-runs/*/trace', async route => {
    if (route.request().url().includes('11111111-1111-4111-8111-111111111111')) { await firstGate; return route.fulfill({ json: makeStep('旧运行') }); }
    return route.fulfill({ json: makeStep('新运行') });
  });
  await page.goto('/#trace'); await page.getByLabel('管理令牌').fill('token'); await page.getByRole('button', { name: '使用令牌' }).click();
  const input = page.getByLabel('分析运行 UUID');
  await input.fill('11111111-1111-4111-8111-111111111111'); await page.getByRole('button', { name: '读取轨迹' }).click();
  await input.fill('22222222-2222-4222-8222-222222222222'); await page.getByRole('button', { name: '读取轨迹' }).click();
  await expect(page.getByText('输出：新运行')).toBeVisible();
  const oldResponse = page.waitForResponse(response => response.url().includes('11111111-1111-4111-8111-111111111111/trace'));
  releaseFirst(); await oldResponse;
  await expect(page.getByText('输出：旧运行')).toHaveCount(0); await expect(page.getByText('输出：新运行')).toBeVisible();
});

test('发布结果不确定时禁止重复发布并保留核对入口', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const criterion = { id: 'c', title: '条件', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' };
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [], criteria: [criterion], validationFindings: [] };
  let publishes = 0;
  await page.route('**/api/v1/admin/policies**', route => {
    const request = route.request();
    if (request.method() === 'POST') { publishes++; return route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '不可用', details: {} }, traceId: 't' } }); }
    if (new URL(request.url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft], nextCursor: null } });
    return route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /合成草稿/ }).click();
  await page.getByLabel('我已人工核对条件及来源').check(); await page.getByRole('button', { name: '确认发布' }).click();
  await expect(page.getByText(/发布结果不确定/)).toBeVisible();
  await expect(page.getByRole('button', { name: '确认发布' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '读取最新版本以对照' })).toBeVisible();
  expect(publishes).toBe(1);
});

for (const status of ['published', 'closed']) {
  test(`审核页的 ${status} 版本仅供查看`, async ({ page }) => {
    const id = '11111111-1111-4111-8111-111111111111';
    const draft = { id, version: 'v1', title: '只读草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status, sources: [], criteria: [{ id: 'c', title: '条件', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
    await page.route('**/api/v1/admin/policies**', route => new URL(route.request().url()).pathname.endsWith('/policies') ? route.fulfill({ json: { items: [draft], nextCursor: null } }) : route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft }));
    await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
    await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /只读草稿/ }).click();
    await expect(page.getByRole('heading', { name: '只读草稿' })).toBeVisible();
    for (const name of ['抽取条款', '添加条件', '保存并校验', '确认发布']) await expect(page.getByRole('button', { name })).toHaveCount(0);
  });
}

test('嵌套条件删除后焦点返回添加按钮', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '嵌套草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [], criteria: [{ id: 'c', title: '条件', expression: { op: 'and', children: [{ op: 'manual_review', reason: '甲' }, { op: 'manual_review', reason: '乙' }] }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => new URL(route.request().url()).pathname.endsWith('/policies') ? route.fulfill({ json: { items: [draft], nextCursor: null } }) : route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft }));
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /嵌套草稿/ }).click();
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  await expect(page.getByRole('group', { name: '审核条件' }).last().getByLabel('标题', { exact: true })).toBeFocused();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '删除条件', exact: true }).last().click();
  await expect(page.getByRole('button', { name: '添加条件', exact: true })).toBeFocused();
  const rootType = page.getByRole('group', { name: '规则：且' }).getByRole('combobox', { name: '类型' }).first();
  page.once('dialog', dialog => dialog.dismiss());
  await rootType.selectOption('or');
  await expect(rootType).toHaveValue('and');
  await expect(page.getByLabel('人工核查原因')).toHaveCount(2);
  await page.getByRole('button', { name: '添加子条件' }).click();
  await expect(page.getByRole('group', { name: '规则：人工核查' }).last().getByRole('combobox', { name: '类型' })).toBeFocused();
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByLabel('人工核查原因').last()).toBeFocused();
  await expect(page.getByLabel('人工核查原因').last()).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByLabel('人工核查原因').last()).toHaveAccessibleDescription(/.+/);
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '删除子条件 3' }).click();
  await expect(page.getByRole('button', { name: '删除子条件 3' })).toBeFocused();
  await expect(page.getByLabel('人工核查原因')).toHaveCount(3);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '删除子条件 3' }).click();
  await expect(page.getByRole('button', { name: '添加子条件' })).toBeFocused();
  await expect(page.getByLabel('人工核查原因')).toHaveCount(2);
});

test('缺失来源的引用明确提示且不能保存', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '来源不一致草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [{ id: 'a', title: '正文', sourceUrl: 'https://www.gov.cn/', text: 'abc' }], criteria: [{ id: 'c', title: '条件', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [{ sourceId: 'missing', sourceUrl: 'https://www.gov.cn/', sourceVersion: 'v1', page: 1, section: null, quote: 'abc', textHash: 'hash' }], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
  let writes = 0;
  await page.route('**/api/v1/admin/policies**', route => { if (route.request().method() === 'PUT') { writes++; return route.abort(); } return new URL(route.request().url()).pathname.endsWith('/policies') ? route.fulfill({ json: { items: [draft], nextCursor: null } }) : route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft }); });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /来源不一致草稿/ }).click();
  await expect(page.getByRole('combobox', { name: '来源' })).toHaveValue('missing');
  await expect(page.getByRole('option', { name: '来源不可用：missing' })).toHaveCount(1);
  await page.getByRole('textbox', { name: '引文' }).fill('abcd'); await page.getByRole('button', { name: '保存并校验' }).click();
  await expect(page.getByRole('alert')).toContainText('来源不在当前草稿中'); await expect(page.getByRole('combobox', { name: '来源' })).toBeFocused(); expect(writes).toBe(0);
});

test('可视化规则可切换布尔比较与日期并保留输入类型', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const draft = { id, version: 'v1', title: '规则草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [], criteria: [{ id: 'c', title: '条件', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => new URL(route.request().url()).pathname.endsWith('/policies') ? route.fulfill({ json: { items: [draft], nextCursor: null } }) : route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft }));
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /规则草稿/ }).click();
  page.once('dialog', dialog => dialog.accept()); await page.getByRole('combobox', { name: '类型', exact: true }).last().selectOption('compare');
  await page.getByRole('combobox', { name: '事实', exact: true }).selectOption('independentLegalEntity');
  await expect(page.getByRole('combobox', { name: '值', exact: true })).toHaveValue('false');
  await expect(page.getByRole('combobox', { name: '比较符' }).locator('option')).toHaveCount(2);
  page.once('dialog', dialog => dialog.accept()); await page.getByRole('combobox', { name: '类型', exact: true }).last().selectOption('date');
  await page.getByLabel('日期', { exact: true }).fill('2024-02-29');
  await expect(page.getByLabel('日期', { exact: true })).toHaveValue('2024-02-29');
});

test('八类规则在可视化编辑与保存后保持原有结构', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const leaf = { op: 'manual_review', reason: '人工核查' };
  const expressions = [
    { op: 'and', children: [leaf, leaf] }, { op: 'or', children: [leaf, leaf] }, { op: 'not', child: leaf },
    { op: 'compare', factKey: 'independentLegalEntity', period: 'assessment', comparator: 'eq', unit: 'boolean', value: false },
    { op: 'date', factKey: 'establishedDate', period: 'assessment', comparator: 'lte', value: '2020-02-29' },
    { op: 'industry_exclusion', factKey: 'industry', period: 'assessment', excluded: ['F51', 'F52'] },
    { op: 'quarterly_average', metric: 'assets', period: 'assessment', comparator: 'lte', value: '50000000.00', unit: 'CNY' }, leaf,
  ];
  const criteria = expressions.map((expression, index) => ({ id: `c${index}`, title: `条件${index}`, expression, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' }));
  const draft = { id, version: 'v1', title: '八类规则', revision: id, status: 'pending_review', sources: [], criteria, validationFindings: [] };
  let saved: { criteria: Array<{ expression: unknown }> } | undefined;
  await page.route('**/api/v1/admin/policies**', route => {
    if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); return route.fulfill({ headers: { ETag: '"new"', 'Access-Control-Expose-Headers': 'ETag' }, json: { draft, criteria, validationFindings: [] } }); }
    if (new URL(route.request().url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft], nextCursor: null } });
    return route.fulfill({ headers: { ETag: '"old"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /八类规则/ }).click();
  const editors = page.getByRole('group', { name: '审核条件' });
  await expect(editors).toHaveCount(8);
  for (const [index, name] of ['且', '或', '非', '事实比较', '日期比较', '行业排除', '季度平均', '人工核查'].entries()) await expect(editors.nth(index).getByRole('group', { name: `规则：${name}` }).first()).toBeVisible();
  await expect(editors.nth(3).getByRole('combobox', { name: '值', exact: true })).toHaveValue('false');
  await expect(editors.nth(4).getByLabel('日期', { exact: true })).toHaveValue('2020-02-29');
  await expect(editors.nth(5).getByLabel('排除行业代码（每行一个）')).toHaveValue('F51\nF52');
  await expect(editors.nth(6).getByLabel('阈值')).toHaveValue('50000000.00');
  await editors.nth(7).getByLabel('标题', { exact: true }).fill('重新审核');
  await page.getByRole('button', { name: '保存并校验' }).click();
  await expect.poll(() => saved).toBeTruthy();
  expect(saved?.criteria.map(item => item.expression)).toEqual(expressions);
});

test('打开另一草稿失败后不保留旧草稿的写入入口', async ({ page }) => {
  const draft = { id: 'a', version: 'v1', title: '旧草稿', issuer: '演示', type: 'tax', region: '440300', revision: 'r', status: 'pending_parse', sources: [], criteria: [], validationFindings: [] };
  const other = { ...draft, id: 'b', title: '另一草稿' };
  await page.route('**/api/v1/admin/policies**', route => { const path = new URL(route.request().url()).pathname; if (path.endsWith('/policies')) return route.fulfill({ json: { items: [draft, other], nextCursor: null } }); if (path.endsWith('/b')) return route.fulfill({ status: 404, json: { error: { code: 'NOT_FOUND', message: '草稿不存在', details: {} }, traceId: 't' } }); return route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft }); });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /旧草稿/ }).click();
  await expect(page.getByRole('button', { name: '抽取条款' })).toBeEnabled();
  await page.getByRole('button', { name: /另一草稿/ }).click();
  await expect(page.getByRole('alert')).toContainText('草稿不存在');
  await expect(page.getByRole('button', { name: '抽取条款' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '旧草稿' })).toHaveCount(0);
});

test('筛选读取失败不显示旧列表或旧草稿', async ({ page }) => {
  const draft = { id: 'a', version: 'v1', title: '旧草稿', issuer: '演示', type: 'tax', region: '440300', revision: 'r', status: 'pending_parse', sources: [], criteria: [], validationFindings: [] };
  let lists = 0;
  await page.route('**/api/v1/admin/policies**', route => { const path = new URL(route.request().url()).pathname; if (path.endsWith('/policies')) { lists++; return lists === 1 ? route.fulfill({ json: { items: [draft], nextCursor: null } }) : route.fulfill({ status: 503, json: { error: { code: 'UNAVAILABLE', message: '读取失败', details: {} }, traceId: 't' } }); } return route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft }); });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /旧草稿/ }).click();
  await page.getByRole('combobox', { name: '类型', exact: true }).first().selectOption('grant'); await page.getByRole('button', { name: '读取草稿' }).click();
  await expect(page.getByRole('alert')).toContainText('读取失败'); await expect(page.getByRole('button', { name: /旧草稿/ })).toHaveCount(0); await expect(page.getByRole('button', { name: '抽取条款' })).toHaveCount(0);
});

test('更改筛选条件后不能使用旧游标加载更多', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/v1/admin/policies**', route => { calls++; return route.fulfill({ json: { items: [], nextCursor: 'cursor-1' } }); });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click();
  await expect(page.getByRole('button', { name: '加载更多' })).toBeEnabled();
  await page.getByRole('combobox', { name: '类型', exact: true }).first().selectOption('grant');
  await expect(page.getByRole('button', { name: '加载更多' })).toBeDisabled();
  await expect(page.getByText('筛选条件已更改，请重新读取草稿；当前列表仍为上次的结果。')).toBeVisible();
  expect(calls).toBe(1);
});

test('草稿筛选与游标分页保留已加载的结果', async ({ page }) => {
  const first = { id: 'a', version: 'v1', title: '第一页', revision: 'a', status: 'pending_review', sources: [], criteria: [], validationFindings: [] };
  const second = { ...first, id: 'b', revision: 'b', title: '第二页' };
  const queries: string[] = [];
  await page.route('**/api/v1/admin/policies**', route => {
    const url = new URL(route.request().url()); queries.push(url.search);
    return route.fulfill({ json: url.searchParams.has('cursor') ? { items: [second], nextCursor: null } : { items: [first], nextCursor: 'next-page' } });
  });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('combobox', { name: '状态', exact: true }).selectOption('pending_review');
  await page.getByRole('combobox', { name: '类型', exact: true }).selectOption('tax');
  await page.getByRole('button', { name: '读取草稿' }).click();
  await expect(page.getByRole('button', { name: /第一页/ })).toBeVisible();
  await page.getByRole('button', { name: '加载更多' }).click();
  await expect(page.getByRole('button', { name: /第一页/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /第二页/ })).toBeVisible();
  expect(new URLSearchParams(queries[1]).get('cursor')).toBe('next-page');
  expect(new URLSearchParams(queries[1]).get('status')).toBe('pending_review');
  expect(new URLSearchParams(queries[1]).get('type')).toBe('tax');
  await expect(page.getByRole('button', { name: '加载更多' })).toHaveCount(0);
});

test('空列表更改筛选后不声称新条件无结果', async ({ page }) => {
  await page.route('**/api/v1/admin/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click();
  await expect(page.getByText('当前筛选条件下没有草稿。')).toBeVisible();
  await page.getByRole('combobox', { name: '类型', exact: true }).first().selectOption('grant');
  await expect(page.getByText('当前筛选条件下没有草稿。')).toHaveCount(0);
  await expect(page.getByText('筛选条件已更改，请重新读取草稿；当前列表仍为上次的结果。')).toBeVisible();
});

test('草稿详情返回错误版本时不允许写入', async ({ page }) => {
  const draft = { id: 'a', version: 'v1', title: '指定草稿', issuer: '演示', type: 'tax', region: '440300', revision: 'r', status: 'pending_parse', sources: [], criteria: [], validationFindings: [] };
  await page.route('**/api/v1/admin/policies**', route => new URL(route.request().url()).pathname.endsWith('/policies') ? route.fulfill({ json: { items: [draft], nextCursor: null } }) : route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: { ...draft, version: 'v2' } }));
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /指定草稿/ }).click();
  await expect(page.getByRole('alert')).toContainText('草稿身份或版本不匹配');
  await expect(page.getByRole('button', { name: '抽取条款' })).toHaveCount(0);
});

test('发布响应身份错误时禁止重复发布并保留核对入口', async ({ page }) => {
  const id = '11111111-1111-4111-8111-111111111111';
  const criterion = { id: 'c', title: '条件', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [], reviewFinding: 'none', validationStatus: 'passed' };
  const draft = { id, version: 'v1', title: '合成草稿', issuer: '演示', type: 'tax', region: '440300', revision: id, status: 'pending_review', sources: [], criteria: [criterion], validationFindings: [] };
  let publishes = 0;
  await page.route('**/api/v1/admin/policies**', route => {
    const request = route.request();
    if (request.method() === 'POST') { publishes++; return route.fulfill({ json: { id: 'wrong', version: 'v1', title: '错误政策', status: 'published', criteria: [] } }); }
    if (new URL(request.url()).pathname.endsWith('/policies')) return route.fulfill({ json: { items: [draft], nextCursor: null } });
    return route.fulfill({ headers: { ETag: '"r"', 'Access-Control-Expose-Headers': 'ETag' }, json: draft });
  });
  await page.goto('/#admin'); await page.getByLabel('管理令牌').fill('test'); await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByRole('button', { name: '读取草稿' }).click(); await page.getByRole('button', { name: /合成草稿/ }).click();
  await page.getByLabel('我已人工核对条件及来源').check(); await page.getByRole('button', { name: '确认发布' }).click();
  await expect(page.getByText(/发布结果不确定/)).toBeVisible();
  await expect(page.getByRole('button', { name: '确认发布' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '读取最新版本以对照' })).toBeVisible();
  expect(publishes).toBe(1);
});


test('导入响应版本错误时禁止重复导入', async ({ page }) => {
  let imports = 0;
  await page.route('**/api/v1/admin/policies**', async route => {
    if (route.request().method() === 'POST') { imports++; return route.fulfill({ json: { id: 'd', version: 'v2', title: '合成政策', revision: 'r', status: 'pending_parse', sources: [], criteria: [], validationFindings: [] } }); }
    return route.fulfill({ json: { items: [], nextCursor: null } });
  });
  await page.goto('/#admin');
  await page.getByLabel('管理令牌').fill('test-token');
  await page.getByRole('button', { name: '使用令牌' }).click();
  await page.getByLabel('版本', { exact: true }).fill('v1');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '企业画像' }).click();
  await expect(page.getByLabel('版本', { exact: true })).toHaveValue('v1');
  await page.getByLabel('标题', { exact: true }).fill('合成政策');
  await page.getByLabel('发布机关').fill('演示机关');
  await page.getByLabel('地区', { exact: true }).fill('440300');
  await page.getByLabel('发布日期').fill('2026-01-01');
  await page.getByLabel('生效日期').fill('2026-01-01');
  await page.getByLabel('官方原文网址').fill('https://www.gov.cn/');
  await page.getByLabel('官方办理网址').fill('https://www.gov.cn/');
  await page.getByLabel('政策正文（PDF 或 UTF-8 文本）').setInputFiles('package.json');
  await page.getByRole('button', { name: '导入草稿' }).click();
  await expect(page.getByRole('alert')).toHaveText('至少填写一个适用主体。');
  await expect(page.getByLabel('适用主体（每行一项）')).toBeFocused();
  await expect(page.getByLabel('适用主体（每行一项）')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByLabel('适用主体（每行一项）')).toHaveAccessibleDescription('至少填写一个适用主体。');
  await page.getByLabel('适用主体（每行一项）').fill('企业');
  await expect(page.getByLabel('适用主体（每行一项）')).not.toHaveAttribute('aria-invalid');
  await page.getByRole('button', { name: '导入草稿' }).click();
  await expect(page.getByText(/导入结果不确定/)).toBeVisible();
  await expect(page.getByRole('button', { name: '导入草稿' })).toBeDisabled();
  await expect(page.getByLabel('版本', { exact: true })).toBeDisabled();
  expect(imports).toBe(1);
  await page.getByRole('button', { name: '读取草稿' }).click();
  await expect(page.getByRole('button', { name: '导入草稿' })).toBeEnabled();
  await expect(page.getByLabel('版本', { exact: true })).toBeEnabled();
});
