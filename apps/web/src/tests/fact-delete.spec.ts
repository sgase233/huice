import { expect, test } from '@playwright/test';

test('删除按钮展开显示中文并删除对应的信息卡片', async ({ page }) => {
  await page.goto('/?mode=demo#enterprise');
  const cards = page.locator('.fact-card');
  const card = cards.nth(2);
  const button = card.getByRole('button', { name: '移除此项信息' });
  await expect(cards).toHaveCount(3);
  await expect(cards.nth(0).getByRole('button', { name: '移除此项信息' })).toHaveCount(0);
  await expect(cards.nth(1).getByRole('button', { name: '移除此项信息' })).toHaveCount(0);
  await button.hover();
  await expect(button).toHaveCSS('width', '140px');
  await expect(button.locator('.fact-delete-label')).toHaveCSS('opacity', '1');
  await button.focus();
  await page.keyboard.press('Enter');
  await expect(cards).toHaveCount(2);
  await expect(page.getByRole('combobox', { name: '信息类型' })).toHaveCount(2);
});

test('触屏设备始终显示删除文字', async ({ browser }) => {
  const context = await browser.newContext({ hasTouch: true, isMobile: true, viewport: { width: 375, height: 812 } });
  const page = await context.newPage();
  await page.goto('/?mode=demo#enterprise');
  const button = page.getByRole('button', { name: '移除此项信息' });
  await expect(button.locator('.fact-delete-label')).toHaveCSS('opacity', '1');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await button.scrollIntoViewIfNeeded();
  expect(await page.locator('.fact-card').last().evaluate(element => element.getAnimations().length)).toBe(0);
  await button.tap();
  await expect(page.locator('.fact-card')).toHaveCount(2);
  await context.close();
});
