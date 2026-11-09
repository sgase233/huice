import { expect, test } from '@playwright/test';

test('主题切换覆盖首页和工作台，并在刷新及页面跳转后保留', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.getByRole('button', { name: '切换深色模式' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('.home')).toHaveCSS('background-color', 'rgb(23, 23, 23)');
  await page.reload();
  await expect(page.getByRole('button', { name: '切换浅色模式' })).toHaveAttribute('aria-pressed', 'true');
  await page.goto('/?mode=demo#enterprise');
  await expect(page.locator('.fact-card').first()).toHaveCSS('background-color', 'rgb(39, 39, 39)');
  await expect(page.getByLabel('示例企业名称')).toHaveCSS('color', 'rgb(240, 240, 240)');
  await expect(page.locator('.demo-banner')).toHaveCSS('color', 'rgb(255, 225, 165)');
  await page.getByRole('link', { name: '机会列表' }).click();
  await expect(page.locator('.policy-card').first()).toHaveCSS('background-color', 'rgb(32, 32, 32)');
  await page.goto('/#enterprise');
  await expect(page.getByRole('button', { name: '切换浅色模式' })).toBeVisible();
  await page.getByRole('button', { name: '切换浅色模式' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('支持圆形主题切换，不支持时仍可切换', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'no-preference' });
  await page.goto('/?mode=demo#enterprise');
  await page.evaluate(() => {
    const original = Element.prototype.animate;
    Element.prototype.animate = function (...args) {
      if (typeof args[1] === 'object' && args[1]?.pseudoElement === '::view-transition-new(root)') document.body.dataset.themeAnimation = 'circle';
      return original.apply(this, args);
    };
  });
  const supports = await page.evaluate(() => !!document.startViewTransition);
  await page.getByRole('button', { name: '切换深色模式' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  if (supports) await expect(page.locator('body')).toHaveAttribute('data-theme-animation', 'circle');
});

test('移动端顶部切换按钮可见且无横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/?mode=demo#enterprise');
  await expect(page.getByRole('button', { name: /切换.*色模式/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('不支持视图过渡时仍可切换，并默认跟随系统配色', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'no-preference' });
  await page.addInitScript(() => Object.defineProperty(document, 'startViewTransition', { value: undefined }));
  await page.goto('/?mode=demo#enterprise');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: '切换浅色模式' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});
