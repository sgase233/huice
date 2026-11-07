import { expect, test } from '@playwright/test';

test('首页轮播加载三张图片', async ({ page }) => {
  await page.goto('/');
  const images = page.locator('.home-carousel img');
  await expect(images).toHaveCount(3);
  await expect.poll(() => images.evaluateAll(elements => elements.every(element => (element as HTMLImageElement).complete && (element as HTMLImageElement).naturalWidth > 0))).toBe(true);
  expect(await images.evaluateAll(elements => elements.map(element => new URL((element as HTMLImageElement).src).pathname.split('/').pop()))).toEqual(['office.png', 'documents.png', 'team.png']);
});

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
  await expect(page.getByRole('region', { name: /首页图片/ }).getByRole('button')).toHaveCount(3);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.waitForTimeout(4500);
  await expect(carousel).toHaveAttribute('data-slide', '0');
  await expect(page.locator('.home-reveal-line > span').first()).toHaveCSS('animation-name', 'none');
});

test('点击两侧图片或通过键盘切换轮播', async ({ page }) => {
  await page.goto('/');
  const carousel = page.locator('.home-carousel-ring');
  const documents = page.getByRole('button', { name: '查看桌面资料图片' });
  const team = page.getByRole('button', { name: '查看团队讨论图片' });
  const box = (await documents.boundingBox())!;
  await page.mouse.click(box.x + box.width - 8, box.y + box.height / 2);
  await expect(carousel).toHaveAttribute('data-slide', '1');
  await expect(documents).toHaveAttribute('aria-pressed', 'true');
  await team.focus();
  await page.keyboard.press('Enter');
  await expect(carousel).toHaveAttribute('data-slide', '2');
  await expect(team).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(4500);
  await expect(carousel).toHaveAttribute('data-slide', '2');
});

test('移动端可触碰侧边图片切换', async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 375, height: 812 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  await page.goto('/');
  const side = page.getByRole('button', { name: '查看桌面资料图片' });
  const box = (await side.boundingBox())!;
  const carouselBox = (await page.locator('.home-carousel').boundingBox())!;
  await page.touchscreen.tap(Math.min(box.x + box.width - 5, carouselBox.x + carouselBox.width - 5), box.y + box.height / 2);
  await expect(page.locator('.home-carousel-ring')).toHaveAttribute('data-slide', '1');
  await context.close();
});
