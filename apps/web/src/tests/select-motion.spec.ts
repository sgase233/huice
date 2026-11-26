import { expect, test } from '@playwright/test';
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
});


test('选择列表展开时播放缓动，并保留键盘选择', async ({ page }) => {
  await page.goto('/#enterprise');
  const select = page.getByLabel('来源').first();
  await select.focus();
  await select.press('Space');
  if (await page.evaluate(() => CSS.supports('appearance', 'base-select'))) {
    await expect(select).toHaveCSS('appearance', 'base-select');
    await expect.poll(() => select.evaluate(element => element.matches(':open'))).toBe(true);
    expect(await select.evaluate(element => getComputedStyle(element, '::picker(select)').transitionDuration)).toContain('0.36s');
  }
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(select).toHaveValue('synthetic_material');
});

test('减少动态效果时选项不播放入场动画', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#enterprise');
  test.skip(!(await page.evaluate(() => CSS.supports('appearance', 'base-select'))));
  const select = page.getByLabel('来源').first();
  await select.focus();
  await select.press('Space');
  await expect.poll(() => select.evaluate(element => element.matches(':open'))).toBe(true);
  await expect(select.locator('option').first()).toHaveCSS('animation-name', 'none');
  await select.press('Escape');
});
