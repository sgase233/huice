import { expect, test } from '@playwright/test';

test('选择列表展开时播放缓动，并保留键盘选择', async ({ page }) => {
  await page.goto('/?mode=demo#enterprise');
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
  await page.goto('/?mode=demo#enterprise');
  test.skip(!(await page.evaluate(() => CSS.supports('appearance', 'base-select'))));
  const select = page.getByLabel('来源').first();
  await select.focus();
  await select.press('Space');
  await expect.poll(() => select.evaluate(element => element.matches(':open'))).toBe(true);
  await expect(select.locator('option').first()).toHaveCSS('animation-name', 'none');
  await select.press('Escape');
});
