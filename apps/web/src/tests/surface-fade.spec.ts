import { expect, test } from '@playwright/test';
test.beforeEach(async ({ page }) => { await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } })); });

for (const entry of [{ link: '开始使用工作台', target: /#enterprise$/ }, { link: '先体验固定样例', target: /mode=demo#enterprise$/ }]) {
  test(`首页与工作台之间淡出淡入：${entry.link}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.addInitScript(() => {
      const original = Element.prototype.animate;
      Element.prototype.animate = function (keyframes, options) {
        if (this.id === 'root' && typeof options === 'object') {
          const history = JSON.parse(sessionStorage.getItem('test-surface-fades') || '[]');
          history.push({ keyframes, duration: options.duration });
          sessionStorage.setItem('test-surface-fades', JSON.stringify(history));
        }
        return original.call(this, keyframes, options);
      };
    });
    await page.goto('/');
    await page.getByRole('link', { name: new RegExp(entry.link) }).click();
    await expect(page).toHaveURL(entry.target);
    await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
    await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('test-surface-fades') || '[]').length)).toBeGreaterThanOrEqual(2);
    const animations = await page.evaluate(() => JSON.parse(sessionStorage.getItem('test-surface-fades') || '[]'));
    expect(animations.slice(0, 2).map((item: { duration: number }) => item.duration)).toEqual([520, 520]);
    expect(animations[0].keyframes).toEqual([{ opacity: 1 }, { opacity: 0 }]);
    expect(animations[1].keyframes).toEqual([{ opacity: 0 }, { opacity: 1 }]);
    await page.getByRole('link', { name: '惠策协同首页' }).click();
    await expect(page.getByRole('heading', { name: /看懂政策/ })).toBeVisible();
    await expect.poll(() => page.evaluate(() => JSON.parse(sessionStorage.getItem('test-surface-fades') || '[]').length)).toBeGreaterThanOrEqual(4);
  });
}

test('减少动态效果时直接切换首页与工作台', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await page.getByRole('link', { name: /开始使用工作台/ }).click();
  await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
  expect(await page.evaluate(() => document.getElementById('root')!.getAnimations().length)).toBe(0);
});

test('审核未保存内容时取消返回，不播放淡出', async ({ page }) => {
  await page.goto('/#admin');
  await page.getByLabel('标题', { exact: true }).fill('尚未保存');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('link', { name: '惠策协同首页' }).click();
  await expect(page).toHaveURL(/#admin$/);
  await expect(page.getByLabel('标题', { exact: true })).toHaveValue('尚未保存');
  expect(await page.evaluate(() => document.getElementById('root')!.getAnimations().length)).toBe(0);
});

test('审核未保存内容时确认一次后返回首页', async ({ page }) => {
  await page.goto('/#admin');
  await page.getByLabel('标题', { exact: true }).fill('尚未保存');
  let dialogs = 0;
  page.on('dialog', dialog => { dialogs++; void dialog.accept(); });
  await page.getByRole('link', { name: '惠策协同首页' }).click();
  await expect(page.getByRole('heading', { name: /看懂政策/ })).toBeVisible();
  expect(dialogs).toBe(1);
});

test('浏览器后退从工作台回到首页时仍能正常显示', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('link', { name: /开始使用工作台/ }).click();
  await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('heading', { name: /看懂政策/ })).toBeVisible();
  await expect(page.locator('#root')).toHaveCSS('opacity', '1');
});
