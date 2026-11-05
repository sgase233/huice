import { expect, test } from '@playwright/test';

test('工作台按钮从指针位置扩散波纹并在松手后回弹', async ({ page }) => {
  await page.goto('/?mode=demo#enterprise');
  const button = page.getByRole('button', { name: '添加一项信息' });
  await button.scrollIntoViewIfNeeded();
  const box = (await button.boundingBox())!;
  await page.mouse.move(box.x + 12, box.y + 12);
  await page.mouse.down();
  await expect(button).toHaveClass(/is-rippling/);
  expect(await button.evaluate(el => parseFloat(el.style.getPropertyValue('--ripple-x')))).toBeCloseTo(12, 0);
  await expect(button).toHaveCSS('scale', '0.96');
  await page.mouse.up();
  await expect(button).toHaveCSS('scale', '1');
  await expect(page.getByRole('button', { name: '移除此项信息' }).first()).toBeVisible();
});

test('减少动态效果时不播放波纹或缩放', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/?mode=demo#enterprise');
  const button = page.getByRole('button', { name: '添加一项信息' });
  await button.hover();
  await page.mouse.down();
  await expect(button).not.toHaveClass(/is-rippling/);
  await expect(button).toHaveCSS('scale', '1');
  await page.mouse.up();
});

test('侧边栏控件不使用按钮动效', async ({ page }) => {
  await page.goto('/?mode=demo#enterprise');
  const toggle = page.getByRole('button', { name: '收起侧边栏' });
  await toggle.hover();
  await page.mouse.down();
  await expect(toggle).not.toHaveClass(/is-rippling/);
  await expect(toggle).toHaveCSS('scale', 'none');
  await page.mouse.up();
});
