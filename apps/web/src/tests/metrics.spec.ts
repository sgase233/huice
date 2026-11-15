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
    const precise = { state: 'precise' }, missing = { state: 'not_collected' };
    if (path.endsWith('/metrics/review-workload')) return route.fulfill({ json: { timeZone: 'Asia/Shanghai', generatedAt: '2026-11-15', days: [{ date: '2026-11-14', dayOpen: false, newDrafts: 0, reviewsCompleted: null, pendingAtEndOfDay: null, availability: { newDrafts: precise, reviewsCompleted: missing, pendingAtEndOfDay: missing } }] } });
    if (path.endsWith('/metrics/assessment-runs')) return route.fulfill({ json: { timeZone: 'Asia/Shanghai', generatedAt: '2026-11-15', days: [{ date: '2026-11-14', dayOpen: false, total: 0, succeeded: 0, failed: null, unfinished: null, durationSampleCount: 0, avgDurationMs: null, availability: { total: precise, succeeded: precise, failed: missing, unfinished: missing, durationSampleCount: missing, avgDurationMs: missing } }] } });
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
  await expect(page.getByRole('cell', { name: '未采集' }).first()).toBeVisible();
  await expect(page.getByRole('cell', { name: '0', exact: true }).first()).toBeVisible();
  for (const width of [375, 1440]) { await page.setViewportSize({ width, height: 900 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({ path: `test-results/metrics-${width}.png`, fullPage: true }); }
  await page.getByRole('button', { name: '切换深色模式' }).click(); await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});
