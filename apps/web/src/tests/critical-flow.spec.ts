import { expect, test } from '@playwright/test';

test('five-page synthetic flow keeps results separate from profile edits', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', request => requests.push(request.url()));
  await page.goto('/');
  await expect(page.getByText('静态合成演示 · 未进行真实资格判断')).toBeVisible();
  const income = page.locator('fieldset').nth(2);
  await income.getByRole('checkbox', { name: '未知' }).uncheck();
  await income.getByRole('textbox', { name: '值' }).fill('0');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByText('已保存当前合成样本。机会列表已恢复为“尚未分析”。')).toBeVisible();
  await page.getByRole('link', { name: /机会列表/ }).click();
  await expect(page.getByText('尚未分析').first()).toBeVisible();
  await page.getByRole('button', { name: '查看固定示例详情' }).last().click();
  await expect(page.getByText('条款待人工核对').first()).toBeVisible();
  await expect(page.getByText('依据已失效，待复核').first()).toBeVisible();
  await expect(page.getByText('申报窗口已关闭').first()).toBeVisible();
  await expect(page.locator('.evidence-row')).toHaveCount(2);
  await expect(page.getByRole('button', { name: /申报/ })).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: 'test-results/detail-1440.png', fullPage: true });
  await page.getByRole('link', { name: '查看清单与报告示例' }).click();
  await expect(page.getByRole('heading', { name: '准备材料示例' })).toHaveCount(2);
  await expect(page.getByText('税收 · 自行判断')).toBeVisible();
  await expect(page.getByText('资金 · 项目申报')).toBeVisible();
  await page.screenshot({ path: 'test-results/report-1440.png', fullPage: true });
  await page.getByRole('link', { name: /审核预览/ }).click();
  await expect(page.getByRole('button', { name: '审核发布（后续阶段）' })).toBeDisabled();
  await page.getByRole('link', { name: /企业画像/ }).click();
  await income.getByRole('textbox', { name: '值' }).fill('1.234');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await expect(page.getByRole('alert')).toContainText('最多两位小数');
  await income.getByRole('textbox', { name: '值' }).fill('0');
  await page.getByRole('button', { name: '保存当前样本' }).click();
  await page.getByRole('link', { name: /机会列表/ }).click();
  await expect(page.getByText('尚未分析')).toHaveCount(4);
  expect(requests.filter(url => url.includes('/api/v1/'))).toEqual([]);
});

test('all four static states remain labelled and filters change the candidate set', async ({ page }) => {
  await page.goto('/#opportunities');
  await page.getByLabel('政策类型').selectOption('tax');
  await expect(page.getByText('2 项合成候选')).toBeVisible();
  await page.getByLabel('政策类型').selectOption('all');
  const expected = ['还缺资料', '条件初步满足', '不满足条件', '条款待人工核对'];
  for (let index = 0; index < expected.length; index++) {
    await page.getByRole('button', { name: '查看固定示例详情' }).nth(index).click();
    await expect(page.getByText(expected[index]).first()).toBeVisible();
    await page.getByRole('link', { name: /机会列表/ }).click();
  }
});

test('mobile layout has no horizontal overflow and keyboard navigation works', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await page.screenshot({ path: 'test-results/enterprise-375.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: '跳到主要内容' })).toBeFocused();
  await page.getByRole('link', { name: /机会列表/ }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/opportunities-375.png', fullPage: true });
  await page.setViewportSize({ width: 768, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/opportunities-768.png', fullPage: true });
});
