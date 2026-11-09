import { expect, test } from '@playwright/test';

test('添加和删除信息时，卡片高度及底部按钮位置平滑变化', async ({ page }) => {
  await page.goto('/?mode=demo#enterprise');
  const add = page.getByRole('button', { name: '添加一项信息' });
  const actions = page.locator('.form-actions');
  const y = () => actions.evaluate(element => element.getBoundingClientRect().top + window.scrollY);
  const before = await y();
  await add.evaluate((element: HTMLButtonElement) => element.click());
  await expect(page.locator('.fact-card')).toHaveCount(4);
  await page.waitForTimeout(140);
  const adding = await y();
  await page.waitForTimeout(350);
  const added = await y();
  expect(adding).toBeGreaterThan(before);
  expect(adding).toBeLessThan(added);

  await page.getByRole('button', { name: '移除此项信息' }).last().evaluate((element: HTMLButtonElement) => element.click());
  await expect(add).toBeDisabled();
  await page.waitForTimeout(140);
  const removing = await y();
  expect(removing).toBeLessThan(added);
  expect(removing).toBeGreaterThan(before);
  await expect(page.locator('.fact-card')).toHaveCount(3);
  await expect(add).toBeEnabled();
  expect(await y()).toBeCloseTo(before, 0);
});

test('减少动态效果时删除立即完成', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/?mode=demo#enterprise');
  await page.getByRole('button', { name: '移除此项信息' }).click();
  await expect(page.locator('.fact-card')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '保存当前样本' })).toBeEnabled();
});

test('删除中间卡片后保留其他卡片的数据', async ({ page }) => {
  await page.goto('/?mode=demo#enterprise');
  const add = page.getByRole('button', { name: '添加一项信息' });
  await add.click();
  await page.locator('.fact-card').last().getByRole('checkbox', { name: '未知' }).uncheck();
  await page.locator('.fact-card').last().getByRole('textbox', { name: '值' }).fill('2025-05-01');
  await page.locator('.fact-card').nth(2).getByRole('button', { name: '移除此项信息' }).click();
  await expect(add).toBeDisabled();
  await expect(page.locator('.fact-card')).toHaveCount(3);
  await expect(page.locator('.fact-card').last().getByRole('textbox', { name: '值' })).toHaveValue('2025-05-01');
  await expect(add).toBeEnabled();
});

for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test(`新增卡片后页面跟随到底部：${reducedMotion}`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.emulateMedia({ reducedMotion });
    await page.goto('/?mode=demo#enterprise');
    const add = page.getByRole('button', { name: '添加一项信息' });
    for (let count = 4; count <= 5; count++) {
      await add.click();
      await expect(page.locator('.fact-card')).toHaveCount(count);
      await expect.poll(async () => page.locator('.form-actions').evaluate(element => element.getBoundingClientRect().bottom <= window.innerHeight)).toBe(true);
      await expect.poll(async () => page.locator('.fact-card').last().evaluate(element => {
        const rect = element.getBoundingClientRect();
        return rect.top >= 0 && rect.bottom <= innerHeight;
      })).toBe(true);
      await expect(page.locator('.fact-slot').last()).not.toHaveClass(/is-entering/);
      const after = await page.locator('.form-actions').evaluate(element => element.getBoundingClientRect().bottom);
      expect(after).toBeLessThanOrEqual(900);
    }
  });
}

test('移动端新增卡片后底部操作保持可见', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/?mode=demo#enterprise');
  await page.getByRole('button', { name: '添加一项信息' }).click();
  await expect(page.locator('.fact-slot').last()).not.toHaveClass(/is-entering/);
  await expect.poll(async () => page.locator('.form-actions').evaluate(element => element.getBoundingClientRect().bottom <= innerHeight)).toBe(true);
});
