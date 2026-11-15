import { expect, test } from '@playwright/test';
const id = '11111111-1111-4111-8111-111111111111';
const period = { startDate: '2026-01-01', endDate: '2026-12-31' };
test('审核员查看完整度和有缺测的统计', async ({ page }) => {
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { user: { id: 'reviewer', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } });
    if (path.endsWith('/enterprises')) return route.fulfill({ json: { items: [{ id, nameSynthetic: '测试企业', period, ownership: 'other' }], nextCursor: null } });
    if (path.endsWith(`/enterprises/${id}`)) return route.fulfill({ json: { id, nameSynthetic: '测试企业', facts: [], period, factVersion: 'a' } });
    if (path === '/api/v1/assessment-runs') return route.fulfill({ json: { items: [], nextCursor: null } });
    if (path.endsWith('/metrics/completeness')) return route.fulfill({ json: { enterpriseId: id, asOf: '2026-11-14', generatedAt: '2026-11-15', definitionVersion: 'd4.static.v1', groups: [{ key: 'basic', label: '基础信息', filledCount: 0, applicableCount: 1, ratio: 0 }], units: [{ factKey: 'industry', periodStart: period.startDate, periodEnd: period.endDate, status: 'not_answered' }], totals: { filledCount: 0, applicableCount: 1, applicabilityUnknownCount: 1, ratio: 0 }, manualReview: [] } });
    const precise = { state: 'precise' }, partial = { state: 'partial', coverageFrom: '2026-11-15T10:25:18.162238+08:00' }, missing = { state: 'not_collected' };
    if (path.endsWith('/metrics/review-workload')) return route.fulfill({ json: { timeZone: 'Asia/Shanghai', generatedAt: '2026-11-15T12:01:17.491916+08:00', days: [{ date: '2026-11-14', dayOpen: false, newDrafts: 0, reviewsCompleted: null, pendingAtEndOfDay: null, availability: { newDrafts: precise, reviewsCompleted: missing, pendingAtEndOfDay: missing } }] } });
    if (path.endsWith('/metrics/assessment-runs')) return route.fulfill({ json: { timeZone: 'Asia/Shanghai', generatedAt: '2026-11-15T12:01:17.491916+08:00', days: [{ date: '2026-11-14', dayOpen: false, total: 0, succeeded: 0, failed: null, unfinished: null, durationSampleCount: 0, avgDurationMs: null, availability: { total: partial, succeeded: precise, failed: missing, unfinished: missing, durationSampleCount: missing, avgDurationMs: missing } }] } });
    return route.abort();
  });
  await page.goto('/#reviewer');
  await page.getByRole('button', { name: '读取企业列表' }).click();
  await page.getByRole('button', { name: '测试企业' }).click();
  await page.getByRole('button', { name: '读取所选企业完整度' }).click();
  await expect(page.getByText('存在适用性未知，未展示总体比例', { exact: false })).toBeVisible();
  await expect(page.getByText(/待填写/)).toBeVisible();
  await page.getByRole('button', { name: '读取统计' }).click();
  await expect(page.getByRole('heading', { name: '审核工作量', exact: true })).toBeVisible();
  await expect(page.getByText('生成于 2026-11-15 12:01:17', { exact: false })).toHaveCount(2);
  await expect(page.getByText('自 2026-11-15 10:25:18', { exact: false }).first()).toBeVisible();
  await expect(page.locator('.metrics-panel')).not.toContainText('162238');
  await expect(page.getByRole('cell', { name: '未采集' }).first()).toBeVisible();
  await expect(page.getByRole('cell', { name: '0', exact: true }).first()).toBeVisible();
  for (const width of [375, 1440]) { await page.setViewportSize({ width, height: 900 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: `test-results/metrics-${width}.png`, fullPage: true }); }
  await page.getByRole('button', { name: '切换深色模式' }).click(); await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('图表使用接口数据并在缺测或部分覆盖时不计算比例', async ({ page }) => {
  let mode: 'precise' | 'partial' | 'missing' | 'zero' = 'precise';
  const precise = { state: 'precise' }, partial = { state: 'partial' }, missing = { state: 'not_collected' };
  await page.route('**/api/v1/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/auth/me')) return route.fulfill({ json: { user: { id: 'reviewer', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } });
    if (path.endsWith('/metrics/review-workload')) return route.fulfill({ json: { timeZone: 'Asia/Shanghai', generatedAt: '2026-11-15T12:00:00+08:00', days: [{ date: '2026-11-14', dayOpen: false, newDrafts: 4, reviewsCompleted: null, pendingAtEndOfDay: null, availability: { newDrafts: precise, reviewsCompleted: missing, pendingAtEndOfDay: missing } }] } });
    if (path.endsWith('/metrics/assessment-runs')) return route.fulfill({ json: { timeZone: 'Asia/Shanghai', generatedAt: '2026-11-15T12:00:00+08:00', days: [{ date: '2026-11-14', dayOpen: false, total: 5, succeeded: mode === 'zero' ? 0 : 3, failed: mode === 'missing' ? null : mode === 'zero' ? 0 : 1, unfinished: 1, durationSampleCount: 0, avgDurationMs: null, availability: { total: precise, succeeded: mode === 'partial' ? partial : precise, failed: mode === 'missing' ? missing : precise, unfinished: precise, durationSampleCount: precise, avgDurationMs: precise } }] } });
    return route.abort();
  });
  await page.goto('/#reviewer');
  await page.getByRole('button', { name: '读取统计' }).click();
  await expect(page.locator('.metrics-column').first()).toContainText('4');
  await expect(page.getByRole('img', { name: '已结束运行 4 次，成功 3 次，失败 1 次' })).toBeVisible();
  await expect(page.getByText('成功：3 次（75.0%）')).toBeVisible();
  await page.locator('.metrics-panel').screenshot({ path: 'test-results/metrics-charts.png' });
  for (const state of ['partial', 'missing'] as const) {
    mode = state;
    await page.getByRole('button', { name: '读取统计' }).click();
    await expect(page.getByText(/成功或失败数据未完整覆盖/)).toBeVisible();
    await expect(page.locator('.metrics-donut')).toHaveCount(0);
  }
  mode = 'zero';
  await page.getByRole('button', { name: '读取统计' }).click();
  await expect(page.getByText('所选期间没有已结束的运行，暂无比例。')).toBeVisible();
});
