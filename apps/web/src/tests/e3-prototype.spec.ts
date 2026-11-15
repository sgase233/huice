import { expect, test } from '@playwright/test';

test('E3 原型区分未知、缺测与真实零，并能切换主题', async ({ page }) => {
  await page.goto('/e3-prototype.html');
  await expect(page.getByText('所有数据均为虚构样例')).toBeVisible();
  await page.getByRole('button', { name: '部分 / 未知' }).click();
  await expect(page.getByText('暂不展示总体百分比', { exact: false })).toBeVisible();
  await expect(page.getByRole('progressbar')).toHaveCount(0);
  await page.getByRole('button', { name: '分析运行趋势' }).click();
  await expect(page.getByRole('img', { name: '已结束运行 37 次，成功 30 次，失败 7 次' })).toBeVisible();
  await expect(page.getByText('可信耗时')).toHaveCount(0);
  await page.getByText('查看逐日数据表').click();
  await expect(page.getByRole('row', { name: '11/12 3 3 0' })).toBeVisible();
  await page.getByRole('button', { name: '部分 / 未知' }).click();
  await expect(page.getByText('未采集；下方展示', { exact: false })).toBeVisible();
  await expect(page.getByRole('img', { name: '已结束运行 22 次，成功 18 次，失败 4 次' })).toBeVisible();
  await page.getByRole('button', { name: '读取失败' }).click();
  await expect(page.getByText('请检查连接后重试。', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '申报时间线' }).click();
  await expect(page.getByText('政策有效期待核实。')).toBeVisible();
  await page.getByRole('button', { name: '本人分析轨迹' }).click();
  await expect(page.getByText('本人轨迹的可见范围待确认', { exact: false })).toBeVisible();
  await page.setViewportSize({ width: 375, height: 800 });
  await page.getByRole('button', { name: '切换深色模式' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
