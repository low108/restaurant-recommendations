const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

const password = 'local-e2e-only-709';
const email = number => `${test.info().project.name}-e2e${number}@example.test`;
const role = (page, kind, name) => page.getByRole(kind, { name, exact: true });

async function login(page, number) {
  await page.goto('/');
  await role(page, 'button', 'Sign in').click();
  await page.getByLabel('Email address', { exact: true }).fill(email(number));
  await page.getByLabel('Password', { exact: true }).fill(password);
  await role(page, 'button', 'Sign in').click();
  await role(page, 'button', 'Sign out').waitFor();
}

async function checkA11yAndReflow(page) {
  const scan = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(scan.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test('axe A/AA checks and narrow reflow pass on auth and logged-out views', async ({ page }) => {
  await page.goto('/');
  await checkA11yAndReflow(page);

  // Sign in dialog
  await role(page, 'button', 'Sign in').click();
  await checkA11yAndReflow(page);
});

test('axe A/AA checks pass on main navigation views and dialog focus returns', async ({ page }, info) => {
  await login(page, 1);
  const nav = page.getByRole('navigation', { name: 'Main navigation' });

  // Home view
  await checkA11yAndReflow(page);

  // Tables view
  await nav.getByRole('link', { name: 'Tables', exact: true }).click();
  await checkA11yAndReflow(page);

  // Profile view
  await nav.getByRole('link', { name: 'Profile', exact: true }).click();
  await checkA11yAndReflow(page);

  // Inbox view
  await nav.getByRole('link', { name: /^Inbox/ }).click();
  await checkA11yAndReflow(page);

  // Room view
  await nav.getByRole('link', { name: 'Tables', exact: true }).click();
  await role(page, 'link', 'Open Nine-person E2E table').click();
  await checkA11yAndReflow(page);

  // Start meal dialog and focus return
  const startBtn = role(page, 'button', 'Start a meal');
  await startBtn.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await checkA11yAndReflow(page);

  // Close modal
  await role(page, 'button', 'Close dialog').click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  // Focus returns on desktop browsers
  if (info.project.name === 'chromium-desktop') {
    await expect(startBtn).toBeFocused();
  }
});

test('meal checkin and dialogs meet accessibility requirements', async ({ page }) => {
  await login(page, 1);
  await role(page, 'link', 'Open Nine-person E2E table').click();
  await role(page, 'button', 'Start a meal').click();
  await role(page, 'checkbox', 'E2E Diner 2').check();
  await role(page, 'button', 'Invite my table to check in').click();
  await role(page, 'heading', 'What sounds good today?').waitFor();

  // Checkin view axe and reflow
  await checkA11yAndReflow(page);

  // Announcements exist in DOM with accessible roles
  const announcement = page.locator('#page-announcement');
  await expect(announcement).toHaveAttribute('role', 'status');
  await expect(announcement).toHaveAttribute('aria-live', 'polite');

  const toast = page.locator('#toast');
  await expect(toast).toHaveAttribute('role', 'status');
  await expect(toast).toHaveAttribute('aria-live', 'polite');
});
