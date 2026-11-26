import { expect, test } from '@playwright/test';
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
});


test('复选框支持键盘切换，并在选中时绘制对勾', async ({ page }) => {
  await page.goto('/#enterprise');
  const checkbox = page.getByRole('checkbox', { name: '未知' }).first();
  await checkbox.focus();
  await checkbox.press('Space');
  await expect(checkbox).toBeChecked();
  await expect(checkbox).toHaveCSS('background-color', 'rgb(23, 23, 23)');
  const animation = await checkbox.evaluate(element => {
    const style = getComputedStyle(element, '::after');
    return { name: style.animationName, duration: style.animationDuration, content: style.content };
  });
  expect(animation).toEqual({ name: 'checkbox-check', duration: '0.4s', content: '""' });
  await checkbox.press('Space');
  await expect(checkbox).not.toBeChecked();
  expect(await checkbox.evaluate(element => getComputedStyle(element, '::after').content)).toBe('none');
});

test('减少动态效果时仍显示选中对勾', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#enterprise');
  const checkbox = page.getByRole('checkbox', { name: '未知' }).first();
  await checkbox.check();
  await expect(checkbox).toBeChecked();
  const style = await checkbox.evaluate(element => ({ content: getComputedStyle(element, '::after').content, duration: getComputedStyle(element, '::after').animationDuration }));
  expect(style.content).toBe('""');
  expect(parseFloat(style.duration)).toBeLessThan(0.001);
});
