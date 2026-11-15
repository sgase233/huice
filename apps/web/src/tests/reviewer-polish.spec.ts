import { test, expect } from '@playwright/test';
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
});
test('审核员记录标题简洁，轨迹输入与按钮间有焦点空间', async ({ page }) => {
  await page.goto('/#reviewer');
  await expect(page.getByRole('heading', { name: '企业与运行记录', exact: true })).toBeVisible();
  await expect(page.getByText('服务端决定可见范围；此处不提供修改企业事实的操作。')).toHaveCount(0);
  await page.getByRole('button', { name: '处理轨迹' }).click();
  const input = page.getByLabel('分析记录编号'); const button = page.getByRole('button', { name: '读取轨迹' });
  await input.focus();
  const a = await input.boundingBox(); const b = await button.boundingBox();
  expect(a && b && b.y - (a.y + a.height)).toBeGreaterThanOrEqual(12);
});
test('政策导入日期提示与原生输入保持可用', async ({ page }) => {
  await page.goto('/#admin');
  const form = page.getByRole('heading', { name: '导入政策草稿' }).locator('..');
  await expect(form.getByText('yyyy/mm/dd', { exact: true })).toHaveCount(3);
  await expect(form.getByText('yyyy/mm/dd --:--', { exact: true })).toHaveCount(2);
  const date = page.getByLabel('发布日期', { exact: true });
  await date.fill('2026-11-15'); await expect(date).toHaveValue('2026-11-15');
  await date.clear(); await expect(form.getByText('yyyy/mm/dd', { exact: true })).toHaveCount(3);
  await page.screenshot({ path: 'test-results/policy-date-placeholder.png', fullPage: true });
});
