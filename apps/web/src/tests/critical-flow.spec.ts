import { expect, test } from '@playwright/test';

test('工作流程切换时播放过渡，减少动态效果时不播放', async ({ page }) => {
  for (const mode of ['/#enterprise']) {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
    await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
    await page.goto(mode);
    await page.evaluate(() => {
      const original = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        if (this.classList.contains('main-content')) {
          document.body.dataset.workflowAnimations = String(Number(document.body.dataset.workflowAnimations || 0) + 1);
          document.body.dataset.workflowDuration = String(typeof args[1] === 'number' ? args[1] : args[1]?.duration);
        }
        return original.apply(this, args);
      };
    });
    const navigation = page.getByRole('navigation', { name: '主导航' });
    await navigation.getByText('机会列表').click();
    await expect(page.locator('body')).toHaveAttribute('data-workflow-animations', '1');
    await expect(page.locator('body')).toHaveAttribute('data-workflow-duration', '960');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await navigation.getByText('企业画像').click();
    await expect(page.getByRole('heading', { name: '先把企业情况说清楚' })).toBeVisible();
    await expect(page.locator('body')).toHaveAttribute('data-workflow-animations', '1');
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('流程选中背景随导航移动并与当前项对齐', async ({ page }) => {
  for (const width of [375, 1440]) {
    await page.setViewportSize({ width, height: 850 });
    for (const mode of ['/#enterprise']) {
      await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
    await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
      await page.goto(mode);
      const nav = page.getByRole('navigation', { name: '主导航' });
      const indicator = nav.locator('.workflow-indicator');
      const before = await indicator.evaluate(element => (element as HTMLElement).style.transform);
      await nav.getByText('机会列表').click();
      await expect.poll(() => indicator.evaluate(element => (element as HTMLElement).style.transform)).not.toBe(before);
      await expect.poll(() => nav.evaluate(element => {
        const highlight = element.querySelector('.workflow-indicator')!.getBoundingClientRect();
        const selected = element.querySelector('[aria-current="page"]')!.getBoundingClientRect();
        return Math.max(Math.abs(highlight.left - selected.left), Math.abs(highlight.top - selected.top), Math.abs(highlight.width - selected.width));
      })).toBeLessThan(1);
      await page.unrouteAll({ behavior: 'wait' });
    }
  }
});

test('侧边栏收缩后保留导航、悬浮提示及键盘操作', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 850 });
  for (const mode of ['/#enterprise']) {
    await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
    await page.route('**/api/v1/policies**', route => route.fulfill({ json: { items: [], nextCursor: null } }));
    await page.goto(mode);
    const nav = page.getByRole('navigation', { name: '主导航' });
    const before = await nav.boundingBox();
    const firstItem = nav.locator('.dock-item').first();
    const firstTop = (await firstItem.boundingBox())!.y;
    const iconCenters = await nav.locator('.workflow-icon').evaluateAll(icons => icons.map(icon => { const rect = icon.getBoundingClientRect(); return rect.left + rect.width / 2; }));
    const toggleTop = (await nav.getByRole('button', { name: '收起侧边栏' }).boundingBox())!.y;
    const captionTop = (await nav.getByText('工作流程').boundingBox())!.y;
    expect(Math.abs(captionTop - toggleTop)).toBeLessThan(48);
    await nav.getByRole('button', { name: '收起侧边栏' }).click();
    await expect.poll(async () => Math.abs((await firstItem.boundingBox())!.y - firstTop)).toBeLessThan(1);
    const toggle = nav.getByRole('button', { name: '展开侧边栏' });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect.poll(() => nav.evaluate(el => {
      const menu = el.querySelector('.dock-menu')!.getBoundingClientRect();
      const icon = el.querySelector('.workflow-icon')!.getBoundingClientRect();
      return Math.abs((menu.left + menu.width / 2) - (icon.left + icon.width / 2));
    })).toBeLessThan(1);
    await expect.poll(async () => (await nav.boundingBox())!.width).toBeLessThanOrEqual(76);
    expect((await nav.boundingBox())!.width).toBeLessThan(before!.width);
    expect(Math.abs((await toggle.boundingBox())!.y - toggleTop)).toBeLessThan(1);
    expect(Math.abs((await firstItem.boundingBox())!.y - firstTop)).toBeLessThan(1);
    const collapsedCenters = await nav.locator('.workflow-icon').evaluateAll(icons => icons.map(icon => { const rect = icon.getBoundingClientRect(); return rect.left + rect.width / 2; }));
    collapsedCenters.forEach((center, index) => expect(Math.abs(center - iconCenters[index])).toBeLessThan(1));
    const target = nav.getByRole('button', { name: '机会列表' });
    await target.hover();
    await expect(nav.locator('.dock-tip')).toHaveAttribute('data-visible', 'true');
    await expect(nav.locator('.dock-tip')).toHaveText('机会列表');
    await page.mouse.move(600, 100);
    await expect(nav.locator('.dock-tip')).toHaveAttribute('data-visible', 'false');
    await expect(nav.locator('.dock-tip')).toHaveText('机会列表');
    await target.hover();
    await expect.poll(() => target.evaluate(el => parseFloat(el.style.getPropertyValue('--dock-factor')))).toBe(1);
    await target.focus();
    await target.press('Enter');
    await expect(target).toHaveAttribute('aria-current', 'page');
    await toggle.click();
    await expect(nav.getByRole('button', { name: '收起侧边栏' })).toHaveAttribute('aria-expanded', 'true');
    await expect(target.locator('.dock-label')).toBeVisible();
    await expect.poll(async () => (await nav.boundingBox())!.width).toBeGreaterThanOrEqual(before!.width - 1);
    expect(Math.abs((await nav.getByRole('button', { name: '收起侧边栏' }).boundingBox())!.y - toggleTop)).toBeLessThan(1);
    expect(Math.abs((await firstItem.boundingBox())!.y - firstTop)).toBeLessThan(1);
    const expandedCenters = await nav.locator('.workflow-icon').evaluateAll(icons => icons.map(icon => { const rect = icon.getBoundingClientRect(); return rect.left + rect.width / 2; }));
    expandedCenters.forEach((center, index) => expect(Math.abs(center - iconCenters[index])).toBeLessThan(1));
    await page.unrouteAll({ behavior: 'wait' });
  }
});

test('侧栏文字平滑切换且遵循减少动态效果', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 850 });
  await page.route('**/api/v1/auth/me', route => route.fulfill({ json: { user: { id: 'reviewer-test', username: 'reviewer', role: 'reviewer', enterpriseId: null }, csrfToken: '0123456789abcdef', expiresAt: '2030-01-01T00:00:00Z' } }));
  await page.goto('/#enterprise');
  const nav = page.getByRole('navigation', { name: '主导航' });
  const label = nav.locator('.dock-label').first();
  await nav.getByRole('button', { name: '收起侧边栏' }).click();
  await expect.poll(() => label.evaluate(el => getComputedStyle(el).opacity)).toBe('0');
  await expect(label).toHaveCSS('visibility', 'hidden');
  await nav.getByRole('button', { name: '展开侧边栏' }).click();
  await expect.poll(() => label.evaluate(el => getComputedStyle(el).opacity)).toBe('1');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await nav.getByRole('button', { name: '收起侧边栏' }).click();
  await expect(label).toHaveCSS('opacity', '0');
  expect(parseFloat(await label.evaluate(el => getComputedStyle(el).transitionDuration))).toBeLessThan(.001);
});
