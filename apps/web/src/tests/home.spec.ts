import { expect, test } from '@playwright/test';

test('首页可浏览介绍并进入真实工作台或固定样例', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /看懂政策/ })).toBeVisible();
  await page.getByRole('link', { name: '使用流程' }).click();
  await expect(page.getByRole('heading', { name: '把复杂政策，拆成清晰的步骤' })).toBeVisible();
  await page.getByRole('link', { name: /开始使用工作台/ }).click();
  await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
  await page.goto('/');
  await page.getByRole('link', { name: /先体验固定样例/ }).click();
  await expect(page.getByText('静态合成演示 · 未进行真实资格判断')).toBeVisible();
});

test('首页在移动端没有横向溢出', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await expect(page.getByRole('link', { name: /开始使用工作台/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('轮播自动旋转且减少动态效果时保持静止', async ({ page }) => {
  await page.goto('/');
  const carousel = page.locator('.home-carousel-ring');
  await expect(carousel).toHaveAttribute('data-slide', '0');
  await expect(carousel).toHaveAttribute('data-slide', '1', { timeout: 7000 });
  await expect(page.getByRole('region', { name: /首页图片预留/ }).getByRole('button')).toHaveCount(0);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.waitForTimeout(4500);
  await expect(carousel).toHaveAttribute('data-slide', '0');
  await expect(page.locator('.home-reveal-line > span').first()).toHaveCSS('animation-name', 'none');
});
