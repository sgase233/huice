import { expect, test } from '@playwright/test';

for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test(`卡片滚动动画遵循动态效果设置：${reducedMotion}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion });
    await page.addInitScript(() => {
      const original = Element.prototype.animate;
      Element.prototype.animate = function (...args) {
        if (this.classList.contains('fact-card')) {
          this.setAttribute('data-reveal-count', String(Number(this.getAttribute('data-reveal-count') || 0) + 1));
          this.setAttribute('data-reveal-duration', String(typeof args[1] === 'number' ? args[1] : args[1]?.duration));
        }
        return original.apply(this, args);
      };
    });
    await page.goto('/?mode=demo#enterprise');
    const card = page.locator('.fact-card').last();
    await card.scrollIntoViewIfNeeded();
    if (reducedMotion === 'reduce') {
      await expect(card).not.toHaveAttribute('data-reveal-count');
    } else {
      await expect(card).toHaveAttribute('data-reveal-count', '1');
      await expect(card).toHaveAttribute('data-reveal-duration', '1200');
      await page.evaluate(() => window.scrollTo(0, 0));
      await card.scrollIntoViewIfNeeded();
      await expect(card).toHaveAttribute('data-reveal-count', '1');
    }
  });
}
