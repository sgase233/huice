import { expect, test } from '@playwright/test';

test('审核员读取发现状态并在设置冲突时保留输入', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  await page.route('**/api/v1/admin/discovery/settings', route => route.request().method() === 'PUT' ? route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '版本冲突', details: {} }, traceId: 't' } }) : route.fulfill({ headers: { ETag: '"rev-1"', 'Access-Control-Expose-Headers': 'ETag' }, json: { enabled: false, sourceUrls: [], intervalMinutes: 1440, timeZone: 'Asia/Shanghai', nextRunAt: null, limits: { minIntervalMinutes: 360, maxIntervalMinutes: 43200, maxSourceUrls: 20 } } }));
  await page.route('**/api/v1/admin/discovery/runs*', route => route.fulfill({ json: { items: [], nextCursor: null } }));
  await page.goto('/#admin');
  await page.getByRole('button', { name: '读取发现设置' }).click();
  await expect(page.getByText('尚未配置来源。', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '读取运行记录' }).click();
  await expect(page.getByText('尚无运行记录', { exact: false })).toBeVisible();
  await page.getByLabel('来源网址（每行一个）').fill('https://www.gov.cn/');
  await page.getByRole('button', { name: '保存发现设置' }).click();
  await expect(page.getByRole('alert')).toContainText('输入已保留');
  await expect(page.getByLabel('来源网址（每行一个）')).toHaveValue('https://www.gov.cn/');
  await expect(page.getByRole('button', { name: '保存发现设置' })).toBeDisabled();
});

test('候选筛选切换清除旧游标并提示附件未解析', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  await page.route('**/api/v1/admin/policy-candidates**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/c1')) return route.fulfill({ json: { id: 'c1', status: 'pending_review', bodyText: '正文', metadata: { title: '候选一' }, missingFields: [], criteria: [], validationFindings: [], attachmentUrls: ['https://www.gov.cn/a.pdf'], unsupportedAttachmentUrls: [], sourceUrl: 'https://www.gov.cn/', fetchedAt: '2026-11-14T00:00:00Z', contentHash: 'hash', suspectedDuplicateOf: 'c2', suspectedDuplicateSourceUrl: 'https://www.gov.cn/other', versionChanged: true, existingPolicyId: 'p1', existingVersion: 'v1', duplicateOf: 'c0' } });
    return route.fulfill({ json: { items: [{ id: 'c1', title: '候选一', status: 'pending_review', attachmentCount: 1, unsupportedAttachmentCount: 0 }], nextCursor: url.searchParams.has('status') ? null : 'old-cursor' } });
  });
  await page.goto('/#admin');
  await page.getByRole('button', { name: '读取候选' }).click();
  await page.getByRole('button', { name: '候选一 · 待审核' }).click();
  await expect(page.getByText('附件未自动解析', { exact: false })).toBeVisible();
  await expect(page.getByText('疑似与候选 c2 重复', { exact: false })).toContainText('https://www.gov.cn/other');
  await expect(page.getByText('同一来源内容有版本变化', { exact: false })).toContainText('已有版本：v1');
  await page.screenshot({ path: 'test-results/discovery-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: '切换深色模式' }).click();
  await expect(page.getByText('附件未自动解析', { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/discovery-mobile-dark.png', fullPage: true });
  await page.getByLabel('候选状态').selectOption('submitted');
  await expect(page.getByRole('button', { name: '加载更多候选' })).toBeDisabled();
  const requested = page.waitForRequest(r => r.url().includes('policy-candidates?status=submitted'));
  await page.getByRole('button', { name: '读取候选' }).click();
  expect(new URL((await requested).url()).searchParams.has('cursor')).toBe(false);
});

test('未检查任何来源的运行不会显示为没有新政策', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  await page.route('**/api/v1/admin/discovery/runs*', route => route.fulfill({ json: { items: [{ id: 'run', startedAt: '2026-11-14T00:00:00Z', status: 'succeeded', terminalReason: 'no_new_policy', sourcesChecked: 0, sourceStatus: [], newCandidates: 0, duplicates: 0, suspectedDuplicates: 0, failedSources: 0, failedArticles: 0, tokensUnknown: false, tokensUsed: 0 }], nextCursor: null } }));
  await page.goto('/#admin');
  await page.getByRole('button', { name: '读取运行记录' }).click();
  await expect(page.getByText('本轮未检查来源，无法判断是否有新政策', { exact: false })).toBeVisible();
  await expect(page.getByText('没有新政策', { exact: true })).toHaveCount(0);
});

test('保存发现设置携带会话 CSRF 和读取到的版本', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  const settings = { enabled: false, sourceUrls: ['https://www.gov.cn/'], intervalMinutes: 1440, timeZone: 'Asia/Shanghai', nextRunAt: null, limits: { minIntervalMinutes: 360, maxIntervalMinutes: 43200, maxSourceUrls: 20 } };
  await page.route('**/api/v1/admin/discovery/settings', route => route.fulfill({ headers: { ETag: '"rev-1"', 'Access-Control-Expose-Headers': 'ETag' }, json: settings }));
  await page.goto('/#admin');
  await page.getByRole('button', { name: '读取发现设置' }).click();
  await expect(page.getByText('自动发现已停用', { exact: false })).toBeVisible();
  await page.getByLabel('启用自动发现').check();
  await expect(page.getByText('自动发现已停用', { exact: false })).toBeVisible();
  await expect(page.getByText('自动发现已启用', { exact: false })).toHaveCount(0);
  await page.getByLabel('抓取周期（分钟）').fill('720');
  const pending = page.waitForRequest(r => r.url().endsWith('/discovery/settings') && r.method() === 'PUT');
  await page.getByRole('button', { name: '保存发现设置' }).click();
  const request = await pending;
  expect(request.headers()['if-match']).toBe('"rev-1"');
  expect(request.headers()['x-csrf-token']).toBe('0123456789abcdef');
  expect(request.postDataJSON().intervalMinutes).toBe(720);
  expect(request.postDataJSON().enabled).toBe(true);
  await expect(page.getByRole('status')).toContainText('已保存');
});

const candidate = { id: 'c1', status: 'pending_review', revision: 'revision-1', bodyText: '政策正文', sourceUrl: 'https://www.gov.cn/', fetchedAt: '2026-11-14T00:00:00Z', contentHash: 'hash', metadata: { title: '候选一', version: 'v1' }, missingFields: [], criteria: [{ id: 'criterion', title: '条件一', expression: { op: 'manual_review', reason: '人工确认' }, sourceSpans: [{ sourceId: 'c1', sourceUrl: 'https://www.gov.cn/', sourceVersion: 'candidate', page: null, section: '一', quote: '政策正文', textHash: 'hash' }], reviewFinding: 'none', validationStatus: 'passed' }], validationFindings: [], attachmentUrls: [], unsupportedAttachmentUrls: [] };
const candidateHeaders = { ETag: '"revision-1"', 'Access-Control-Expose-Headers': 'ETag' };

test('候选保存冲突保留编辑并显示服务端版本对照', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  await page.route('**/api/v1/admin/policy-candidates**', route => {
    if (route.request().method() === 'PATCH') return route.fulfill({ status: 409, json: { error: { code: 'VERSION_CONFLICT', message: '版本变化', details: {} } } });
    if (new URL(route.request().url()).pathname.endsWith('/c1')) return route.fulfill({ headers: candidateHeaders, json: candidate });
    return route.fulfill({ json: { items: [{ id: 'c1', title: '候选一', status: 'pending_review', attachmentCount: 0, unsupportedAttachmentCount: 0 }], nextCursor: null } });
  });
  await page.goto('/#admin'); await page.getByRole('button', { name: '读取候选' }).click(); await page.getByRole('button', { name: '候选一 · 待审核' }).click();
  const editor = page.getByRole('region', { name: '候选审核' });
  await editor.getByLabel('发布机关').fill('本地修改');
  await editor.getByRole('button', { name: '保存候选并校验' }).click();
  await expect(editor.getByRole('alert')).toContainText('本地输入已保留');
  await expect(editor.getByLabel('发布机关')).toHaveValue('本地修改');
  await editor.getByRole('button', { name: '读取最新候选' }).click();
  await expect(editor.getByRole('heading', { name: '版本对照' })).toBeVisible();
  await expect(editor.getByLabel('发布机关')).toHaveValue('本地修改');
});

test('提交结果未知后刷新仍使用原幂等键和版本重试', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  const requests: { key: string | undefined; etag: string | undefined }[] = [];
  await page.route('**/api/v1/admin/policies/policy?**', route => route.fulfill({ headers: candidateHeaders, json: { id: 'policy', version: 'v1', title: '草稿', issuer: '测试', revision: 'draft-rev', status: 'pending_review', sources: [], criteria: [], validationFindings: [] } }));
  await page.route('**/api/v1/admin/policy-candidates**', route => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (path.endsWith('/submit')) { requests.push({ key: req.headers()['idempotency-key'], etag: req.headers()['if-match'] }); if (requests.length === 1) return route.abort(); return route.fulfill({ json: { id: 'policy', version: 'v1', title: '草稿', revision: 'draft-rev', status: 'pending_review', sources: [], criteria: [], validationFindings: [] } }); }
    if (path.endsWith('/c1')) return route.fulfill({ headers: candidateHeaders, json: candidate });
    return route.fulfill({ json: { items: [{ id: 'c1', title: '候选一', status: 'pending_review', attachmentCount: 0, unsupportedAttachmentCount: 0 }], nextCursor: null } });
  });
  await page.goto('/#admin'); await page.getByRole('button', { name: '读取候选' }).click(); await page.getByRole('button', { name: '候选一 · 待审核' }).click();
  await page.getByRole('button', { name: '提交为待审草稿' }).click();
  await expect(page.getByRole('alert')).toContainText('提交结果尚不确定');
  page.on('dialog', dialog => dialog.accept());
  await page.reload(); await page.getByRole('button', { name: '读取候选' }).click(); await page.getByRole('button', { name: '候选一 · 待审核' }).click();
  await page.getByRole('button', { name: '按原请求重试提交' }).click();
  await expect(page.getByRole('status')).toContainText('尚未发布');
  await page.getByRole('button', { name: '打开待审草稿' }).click();
  await expect(page.locator('#admin-policy-draft')).toContainText('草稿');
  expect(requests).toHaveLength(2); expect(requests[1]).toEqual(requests[0]);
  expect(requests[0].key).toBeTruthy(); expect(requests[0].etag).toBe('"revision-1"');
});

test('仅修改元数据时不覆盖抽取失败候选的条件，并可丢弃', async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'r', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  const failed = { ...candidate, status: 'extraction_failed', criteria: [] };
  await page.route('**/api/v1/admin/policy-candidates**', route => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (req.method() === 'PATCH') return route.fulfill({ headers: candidateHeaders, json: { ...failed, metadata: req.postDataJSON().metadata } });
    if (path.endsWith('/discard')) return route.fulfill({ headers: candidateHeaders, json: { ...failed, status: 'discarded' } });
    if (path.endsWith('/c1')) return route.fulfill({ headers: candidateHeaders, json: failed });
    return route.fulfill({ json: { items: [{ id: 'c1', title: '候选一', status: 'extraction_failed', attachmentCount: 0, unsupportedAttachmentCount: 0 }], nextCursor: null } });
  });
  await page.goto('/#admin'); await page.getByRole('button', { name: '读取候选' }).click(); await page.getByRole('button', { name: '候选一 · 抽取失败' }).click();
  const editor = page.getByRole('region', { name: '候选审核' });
  await editor.getByLabel('发布机关').fill('测试机构');
  const pending = page.waitForRequest(r => r.method() === 'PATCH' && r.url().endsWith('/policy-candidates/c1'));
  await editor.getByRole('button', { name: '保存候选并校验' }).click();
  expect((await pending).postDataJSON()).not.toHaveProperty('criteriaDrafts');
  await expect(editor.getByRole('status')).toContainText('候选已保存');
  page.on('dialog', dialog => dialog.accept());
  await editor.getByLabel('丢弃原因（可选）').fill('无需处理');
  await editor.getByRole('button', { name: '丢弃候选' }).click();
  await expect(editor.getByRole('status')).toContainText('候选已丢弃');
  await expect(editor.getByRole('button', { name: '丢弃候选' })).toBeDisabled();
});
