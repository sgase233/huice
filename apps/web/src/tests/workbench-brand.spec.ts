import { expect, test } from '@playwright/test';
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
});

for (const url of ['/?mode=demo#enterprise', '/#enterprise']) {
  test(`工作台标识返回首页：${url}`, async ({ page }) => {
    await page.goto(url);
    const logo = page.getByRole('link', { name: '惠策协同首页' });
    await expect(logo.locator('.brand-mark')).toHaveText('惠');
    await logo.click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('heading', { name: /看懂政策/ })).toBeVisible();
    await expect(page.locator('.home-brand-mark')).toHaveText('惠');
  });
}

test('审核内容未保存时取消从标识返回首页', async ({ page }) => {
  await page.goto('/#admin');
  await page.getByLabel('标题', { exact: true }).fill('尚未保存');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: '惠策协同首页' }).click();
  await expect(page).toHaveURL(/#admin$/);
  await expect(page.getByLabel('标题', { exact: true })).toHaveValue('尚未保存');
});

test('移动端工作台顶部标识与切换操作保持可见', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/#enterprise');
  await expect(page.getByRole('link', { name: '惠策协同首页' })).toBeVisible();
  await expect(page.getByRole('button', { name: /切换.*色模式/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
