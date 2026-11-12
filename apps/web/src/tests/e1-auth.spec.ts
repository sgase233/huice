import { expect, test } from '@playwright/test';

const session = (name: string, role: 'enterprise' | 'reviewer', enterpriseId: string | null) => ({ user: { id: name, username: name, role, enterpriseId }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' });
const error = (code: string) => ({ error: { code, message: code, details: {} }, traceId: 'test' });

test('anonymous first visit does not report an expired session', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.goto('/#enterprise');
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByText('会话已失效，请重新登录。')).toHaveCount(0);
});

test('registration validates normalized username and displays server field errors', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.route('**/api/v1/auth/register', route => route.fulfill({ status: 422, json: { error: { code: 'VALIDATION_ERROR', message: '请求内容不合法', details: { fieldErrors: [{ field: 'enterprise.nameSynthetic', message: '名称无效' }] } }, traceId: 'test' } }));
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '注册账号' }).click();
  await page.getByLabel('用户名').fill('中文');
  await page.getByLabel('密码', { exact: true }).fill('password-12345');
  await page.getByLabel('企业名称').fill('合成企业');
  await page.getByLabel('地区代码').fill('440300');
  await page.getByLabel('行业代码').fill('C39');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('用户名须为');
  await page.getByLabel('用户名').fill(' ACME ');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('名称无效');
});

test('reviewer can read trace without exposing it to enterprise accounts', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('reviewer', 'reviewer', null) }));
  await page.route('**/api/v1/assessment-runs/*/trace', route => route.fulfill({ json: { steps: [] } }));
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '处理轨迹' }).click();
  await page.getByLabel('分析记录编号').fill('123e4567-e89b-42d3-a456-426614174000');
  await page.getByRole('button', { name: '读取轨迹' }).click();
  await expect(page.getByText('这条分析记录没有处理步骤。')).toBeVisible();
});

test('unavailable server demo reports its own cause', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/policies**', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '进入服务端演示' }).click();
  await page.getByRole('button', { name: '机会列表' }).click();
  await expect(page.getByRole('alert')).toContainText('该部署未启用服务端演示');
});

test('demo entry restores csrf before retrying a rejected logout', async ({ page }) => {
  let logouts = 0;
  await page.route('**/api/v1/auth/me', route => logouts === 0 ? route.fulfill({ status: 500, json: error('INTERNAL_ERROR') }) : route.fulfill({ json: session('reviewer', 'reviewer', null) }));
  await page.route('**/api/v1/auth/logout', route => {
    logouts++;
    if (logouts === 1) return route.fulfill({ status: 403, json: error('CSRF_REQUIRED') });
    expect(route.request().headers()['x-csrf-token']).toBe('0123456789abcdef');
    return route.fulfill({ status: 204 });
  });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '进入服务端演示' }).click();
  await expect(page.getByText('服务端演示', { exact: true })).toBeVisible();
  expect(logouts).toBe(2);
});

test('password change exposes server field errors', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('first', 'enterprise', null) }));
  await page.route('**/api/v1/auth/password', route => route.fulfill({ status: 422, json: { error: { code: 'VALIDATION_ERROR', message: '请求内容不合法', details: { fieldErrors: [{ field: 'newPassword', message: '密码不符合要求' }] } }, traceId: 'test' } }));
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '账号与退出' }).click();
  await page.getByLabel('当前密码').fill('password-12345');
  await page.getByLabel('新密码').fill('password-67890');
  await page.getByRole('button', { name: '修改密码' }).click();
  await expect(page.getByRole('alert')).toContainText('密码不符合要求');
});

test('bound enterprise can be retried after a failed read', async ({ page }) => {
  let failed = true;
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('first', 'enterprise', 'e1') }));
  await page.route('**/api/v1/enterprises/e1', route => failed ? route.fulfill({ status: 500, json: error('INTERNAL_ERROR') }) : route.fulfill({ json: { id: 'e1', nameSynthetic: '重试企业', period: { startDate: '2026-01-01', endDate: '2026-12-31' }, factVersion: 'a', facts: [] } }));
  await page.goto('/#enterprise');
  await expect(page.getByRole('alert')).toBeVisible();
  failed = false;
  await page.getByRole('button', { name: '重新读取企业' }).click();
  await expect(page.getByText('已保存合成企业：重试企业')).toBeVisible();
});

test('register sends normalized username and required enterprise facts', async ({ page }) => {
  let sent: any;
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.route('**/api/v1/auth/register', route => { sent = route.request().postDataJSON(); return route.fulfill({ status: 201, json: { user: { id: 'a', username: 'acme', role: 'enterprise', enterpriseId: 'e1' } } }); });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '注册账号' }).click();
  await page.getByLabel('用户名').fill(' ACME ');
  await page.getByLabel('密码', { exact: true }).fill('password-12345');
  await page.getByLabel('企业名称').fill('合成企业');
  await page.getByLabel('地区代码').fill('440300');
  await page.getByLabel('行业代码').fill('C39');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await expect(page.getByText('注册成功，请登录。')).toBeVisible();
  expect(sent.username).toBe('acme');
  expect(sent.enterprise.facts.map((f: { key: string }) => f.key)).toEqual(['region', 'industry']);
});

test('reviewer registration explains and preserves optional enterprise binding', async ({ page }) => {
  let sent: any;
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.route('**/api/v1/auth/register', route => { sent = route.request().postDataJSON(); return route.fulfill({ status: 201, json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: 'e1' } } }); });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '注册账号' }).click();
  await expect(page.getByText(/若填写企业资料则会创建并绑定该企业/)).toBeVisible();
  await page.getByLabel('用户名').fill('reviewer');
  await page.getByLabel('密码', { exact: true }).fill('password-12345');
  await page.getByLabel('企业名称').fill('合成企业');
  await page.getByLabel('地区代码').fill('440300');
  await page.getByLabel('行业代码').fill('C39');
  await page.getByLabel('审核员注册密钥（仅审核员）').fill('synthetic-key-12345');
  await page.getByRole('button', { name: '注册', exact: true }).click();
  await expect(page.getByText('注册成功，请登录。')).toBeVisible();
  expect(sent.enterprise.nameSynthetic).toBe('合成企业');
  expect(sent.reviewerSignupKey).toBe('synthetic-key-12345');
});

test('logout clears account data and reviewer navigation follows the next session', async ({ page }) => {
  let login = false;
  let logoutCsrf = '';
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('first', 'enterprise', 'e1') }));
  await page.route('**/api/v1/enterprises/e1', route => route.fulfill({ json: { id: 'e1', nameSynthetic: '旧账号企业', facts: [], period: { startDate: '2026-01-01', endDate: '2026-12-31' }, factVersion: 'v1' } }));
  await page.route('**/api/v1/auth/logout', route => { logoutCsrf = route.request().headers()['x-csrf-token']; return route.fulfill({ status: 204 }); });
  await page.route('**/api/v1/auth/login', route => { login = true; return route.fulfill({ json: session('second', 'reviewer', null) }); });
  await page.goto('/#enterprise');
  await expect(page.getByText('已保存合成企业：旧账号企业')).toBeVisible();
  await expect(page.getByRole('button', { name: '政策审核' })).toHaveCount(0);
  await page.getByRole('button', { name: '账号与退出' }).click();
  await page.getByRole('button', { name: '退出并切换账号' }).click();
  await page.getByLabel('用户名').fill('second');
  await page.getByLabel('密码', { exact: true }).fill('password-12345');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '政策审核' })).toBeVisible();
  await expect(page.getByText('已保存合成企业：旧账号企业')).toHaveCount(0);
  expect(login).toBe(true);
  expect(logoutCsrf).toBe('0123456789abcdef');
});

test('late enterprise response cannot replace the next account', async ({ page }) => {
  let release!: () => void;
  let started!: () => void;
  const blocked = new Promise<void>(done => { release = done; });
  const requested = new Promise<void>(done => { started = done; });
  const profile = (id: string, nameSynthetic: string) => ({ id, nameSynthetic, facts: [], period: { startDate: '2026-01-01', endDate: '2026-12-31' }, factVersion: 'v1' });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('first', 'enterprise', 'old') }));
  await page.route('**/api/v1/enterprises/old', async route => { started(); await blocked; await route.fulfill({ json: profile('old', '旧企业') }).catch(() => {}); });
  await page.route('**/api/v1/enterprises/new', route => route.fulfill({ json: profile('new', '新企业') }));
  await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/auth/login', route => route.fulfill({ json: session('second', 'enterprise', 'new') }));
  await page.goto('/#enterprise');
  await requested;
  await page.getByRole('button', { name: '账号与退出' }).click();
  await page.getByRole('button', { name: '退出并切换账号' }).click();
  await page.getByLabel('用户名').fill('second');
  await page.getByLabel('密码', { exact: true }).fill('password-12345');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByText('已保存合成企业：新企业')).toBeVisible();
  const oldResponse = page.waitForResponse(response => response.url().endsWith('/enterprises/old'));
  release();
  await oldResponse;
  await expect(page.getByText('已保存合成企业：旧企业')).toHaveCount(0);
  await expect(page.getByText('已保存合成企业：新企业')).toBeVisible();
});

test('account mode cannot change while login is pending', async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(done => { release = done; });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.route('**/api/v1/auth/login', async route => { await pending; await route.fulfill({ status: 401, json: error('INVALID_CREDENTIALS') }); });
  await page.goto('/#enterprise');
  await page.getByLabel('用户名').fill('example');
  await page.getByLabel('密码', { exact: true }).fill('password-12345');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '注册账号' })).toBeDisabled();
  await expect(page.getByRole('link', { name: '使用固定前端样例' })).toHaveCount(0);
  release();
  await expect(page.getByRole('button', { name: '注册账号' })).toBeEnabled();
});

test('fact conflict preserves input until the profile is reloaded', async ({ page }) => {
  const period = { startDate: '2026-01-01', endDate: '2026-12-31' };
  const tag1 = `"${'a'.repeat(64)}"`;
  const tag2 = `"${'b'.repeat(64)}"`;
  const tag3 = `"${'c'.repeat(64)}"`;
  const profile = (value: string) => ({ id: 'e1', nameSynthetic: '合成企业', period, factVersion: 'a', facts: [
    { key: 'region', value: '440300', unit: 'code', period, source: 'user_input', verified: false },
    { key: 'industry', value, unit: 'code', period, source: 'user_input', verified: false },
  ] });
  let reads = 0; let writes = 0; let sentTag = '';
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('first', 'enterprise', 'e1') }));
  await page.route('**/api/v1/enterprises/e1', route => { reads++; return route.fulfill({ json: profile('C39'), headers: { ETag: reads === 1 ? tag1 : tag2, 'Access-Control-Expose-Headers': 'ETag' } }); });
  await page.route('**/api/v1/enterprises/e1/facts/industry', route => {
    writes++; sentTag = route.request().headers()['if-match'];
    return writes === 1 ? route.fulfill({ status: 409, json: error('VERSION_CONFLICT') }) : route.fulfill({ json: profile('F51'), headers: { ETag: tag3, 'Access-Control-Expose-Headers': 'ETag' } });
  });
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '编辑行业' }).click();
  await page.getByLabel('值', { exact: true }).fill('F51');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '账号与退出' }).click();
  await expect(page.getByLabel('值', { exact: true })).toHaveValue('F51');
  await page.getByRole('button', { name: '保存该项' }).click();
  await expect.poll(() => writes).toBe(1);
  await expect(page.getByRole('alert')).toContainText('请重新读取画像');
  await expect(page.getByRole('button', { name: '保存该项' })).toBeDisabled();
  await expect(page.getByLabel('值', { exact: true })).toHaveValue('F51');
  await page.getByRole('button', { name: '重新读取画像' }).click();
  await expect(page.getByLabel('值', { exact: true })).toHaveValue('F51');
  await page.getByRole('button', { name: '保存该项' }).click();
  await expect(page.getByRole('status')).toContainText('该项信息已保存');
  expect(sentTag).toBe(tag2);
  expect(writes).toBe(2);
});

test('a reviewer keeps the newly bound enterprise when returning from account settings', async ({ page }) => {
  let saved: any;
  const headers = { ETag: `"${'a'.repeat(64)}"`, 'Access-Control-Expose-Headers': 'ETag' };
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('reviewer', 'reviewer', null) }));
  await page.route('**/api/v1/enterprises', route => { const input = route.request().postDataJSON(); saved = { ...input, id: 'created', factVersion: 'a', facts: input.facts.map((fact: object) => ({ ...fact, verified: false })) }; return route.fulfill({ status: 201, json: saved, headers }); });
  await page.route('**/api/v1/enterprises/created', route => route.fulfill({ json: saved, headers }));
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByText('已保存合成企业：合成企业 A')).toBeVisible();
  await page.getByRole('button', { name: '账号与退出' }).click();
  await page.getByRole('button', { name: '返回工作台' }).click();
  await expect(page.getByText('已保存合成企业：合成企业 A')).toBeVisible();
  await expect(page.getByRole('button', { name: '保存当前样本' })).toHaveCount(0);
});

test('reviewer global enterprise and runs are read-only', async ({ page }) => {
  const period = { startDate: '2026-01-01', endDate: '2026-12-31' };
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('reviewer', 'reviewer', null) }));
  await page.route('**/api/v1/enterprises', route => route.fulfill({ json: { items: [{ id: 'other', nameSynthetic: '其他企业', period, ownership: 'other' }], nextCursor: null } }));
  await page.route('**/api/v1/enterprises/other', route => route.fulfill({ json: { id: 'other', nameSynthetic: '其他企业', period, factVersion: 'a', facts: [{ key: 'industry', value: 'C39', unit: 'code', period, source: 'user_input', verified: false }] } }));
  await page.route('**/api/v1/assessment-runs?*', route => route.fulfill({ json: { items: [{ id: 'pending', enterpriseId: 'other', createdAt: '2026-01-01T00:00:00Z', asOf: '2026-01-01T00:00:00Z', status: 'running', reportAvailable: false }, { id: 'future', enterpriseId: 'other', createdAt: '2026-01-01T00:00:00Z', asOf: '2026-01-01T00:00:00Z', status: 'future', reportAvailable: true }], nextCursor: null } }));
  await page.goto('/#reviewer');
  await page.getByRole('button', { name: '读取企业列表' }).click();
  await page.getByRole('button', { name: '其他企业' }).click();
  await expect(page.getByText('运行中')).toBeVisible();
  await expect(page.getByRole('button', { name: '查看结果' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '下载报告' })).toHaveCount(0);
  await expect(page.getByText('未知状态')).toBeVisible();
  await expect(page.getByRole('button', { name: /保存该项|编辑行业/ })).toHaveCount(0);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await expect(page.getByRole('button', { name: '读取企业列表' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/e1-reviewer-${theme}.png`, fullPage: true });
  }
});

test('enterprise account cannot open reviewer view by hash', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('enterprise', 'enterprise', null) }));
  await page.goto('/#reviewer');
  await expect(page.getByText('此入口仅供审核员使用。')).toBeVisible();
  await expect(page.getByRole('button', { name: '读取企业列表' })).toHaveCount(0);
  await page.goto('/#trace');
  await expect(page.getByText('此入口仅供审核员使用；本人轨迹暂未开放。')).toBeVisible();
  await expect(page.getByRole('button', { name: '读取轨迹' })).toHaveCount(0);
});

test('valid demo identity can enter demo after session restoration is forbidden', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 403, json: error('ROLE_FORBIDDEN') }));
  await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 204 }));
  await page.goto('/#enterprise');
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByText('请登录账号，或进入服务端演示。')).toBeVisible();
  await expect(page.getByText('会话已失效，请重新登录。')).toHaveCount(0);
  await page.getByRole('button', { name: '进入服务端演示' }).click();
  await expect(page.getByText('服务端演示', { exact: true })).toBeVisible();
});

test('gate failure stays distinct from an expired session', async ({ page }) => {
  let expired = false;
  await page.route('**/api/v1/auth/me', route => {
    return !expired ? route.fulfill({ status: 403, json: error('DEMO_SESSION_REQUIRED') }) : route.fulfill({ status: 401, json: error('SESSION_INVALID') });
  });
  await page.goto('/#enterprise');
  await expect(page.getByRole('heading', { name: '演示入口已失效' })).toBeVisible();
  expired = true;
  await page.getByRole('button', { name: '重新检查会话' }).click();
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByText('请求已被账号切换替代。')).toHaveCount(0);
  await expect(page.getByText('会话已失效，请重新登录。')).toBeVisible();
});

test('server demo is separate and shows quota recovery', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.route('**/api/v1/auth/logout', route => route.fulfill({ status: 204 }));
  await page.route('**/api/v1/enterprises', route => route.fulfill({ status: 429, json: error('DEMO_QUOTA_EXCEEDED') }));
  await page.goto('/#enterprise');
  await page.getByRole('button', { name: '进入服务端演示' }).click();
  await expect(page.getByText('服务端演示', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '政策审核' })).toHaveCount(0);
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByRole('alert')).toContainText('演示样本配额已用完');
});

test('account form remains usable in both themes on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ status: 401, json: error('UNAUTHENTICATED') }));
  await page.goto('/#enterprise');
  await expect(page.getByRole('heading', { name: '登录', exact: true })).toBeVisible();
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByLabel('用户名')).toBeVisible();
    await page.screenshot({ path: `test-results/e1-login-${theme}.png`, fullPage: true });
  }
  await page.getByRole('button', { name: '注册账号' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('fact editor is labelled and fits a narrow screen in both themes', async ({ page }) => {
  const period = { startDate: '2026-01-01', endDate: '2026-12-31' };
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: session('first', 'enterprise', 'e1') }));
  await page.route('**/api/v1/enterprises/e1', route => route.fulfill({ json: { id: 'e1', nameSynthetic: '合成企业', period, factVersion: 'a', facts: [{ key: 'region', value: '440300', unit: 'code', period, source: 'user_input', verified: false }, { key: 'industry', value: 'C39', unit: 'code', period, source: 'user_input', verified: false }] }, headers: { ETag: `"${'a'.repeat(64)}"`, 'Access-Control-Expose-Headers': 'ETag' } }));
  await page.goto('/#enterprise');
  await expect(page.getByRole('heading', { name: '企业事实' })).toBeVisible();
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByLabel('信息类型')).toBeVisible();
    await expect(page.getByRole('button', { name: '机会列表' })).toHaveCSS('color', theme === 'dark' ? 'rgb(221, 221, 221)' : 'rgb(61, 61, 61)');
    await page.screenshot({ path: `test-results/e1-facts-${theme}.png`, fullPage: true });
  }
  await page.getByRole('button', { name: '编辑行业' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('值', { exact: true })).toHaveValue('C39');
});
