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

test('画像操作按钮随主题保持清晰对比', async ({ page }) => {
  await page.goto('/?mode=demo#enterprise');
  for (const [theme, background, foreground] of [['light', 'rgb(23, 23, 23)', 'rgb(255, 255, 255)'], ['dark', 'rgb(237, 237, 237)', 'rgb(23, 23, 23)']] as const) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    for (const name of ['添加一项信息', '保存当前样本']) {
      const button = page.getByRole('button', { name, exact: true });
      await expect(button).toHaveCSS('background-color', background);
      await expect(button).toHaveCSS('color', foreground);
    }
  }
});
