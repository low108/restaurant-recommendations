const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;
const password = 'local-e2e-only-709';
const email = number => `${test.info().project.name}-e2e${number}@example.test`;
const role = (page, kind, name) => page.getByRole(kind, { name, exact: true });
const option = page => page.getByRole('article').filter({ has: role(page, 'heading', 'Demo Noodle House — Fictional PJ branch') });

async function login(page, number, path = '/') {
  await page.goto(path);
  await role(page, 'button', 'Sign in').click();
  await page.getByLabel('Email address', { exact: true }).fill(email(number));
  await page.getByLabel('Password', { exact: true }).fill(password);
  await role(page, 'button', 'Sign in').click();
  await role(page, 'button', 'Sign out').waitFor();
}
async function startMeal(page) {
  await role(page, 'link', 'Open Nine-person E2E table').click();
  await role(page, 'button', 'Start a meal').click();
  await role(page, 'checkbox', 'E2E Diner 2').check();
  await role(page, 'button', 'Invite my table to check in').click();
  await role(page, 'heading', 'What sounds good today?').waitFor();
  return new URL(page.url()).hash;
}
async function checkin(page, craving = 'light soup') {
  if (!await role(page, 'radio', 'Join').isChecked()) await role(page, 'radio', 'Join').press('Space');
  await expect(role(page, 'radio', 'Join')).toBeChecked();
  await role(page, 'textbox', 'Describe your craving').fill(craving);
  await role(page, 'checkbox', 'My private food requirements are up to date for this meal.').check();
  await role(page, 'button', 'Send my check-in').click();
  await role(page, 'button', 'Update my check-in').waitFor();
}
async function vote(page, choice, reason = '') {
  await option(page).getByRole('button', { name: choice, exact: true }).click();
  await role(page, 'textbox', 'Private reason (optional)').fill(reason);
  await role(page, 'button', 'Save my response').click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
}
async function a11y(page) {
  const scan = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(scan.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test('two private diners reconnect to a durable shortlist and unanimously choose', async ({ page, browser }, info) => {
  const friendContext = await browser.newContext({ ...info.project.use, baseURL: info.project.use.baseURL });
  const friend = await friendContext.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  friend.on('pageerror', error => errors.push(error.message));
  try {
    await login(page, 1);
    await expect(page.locator('#inference-status')).toHaveAttribute('data-inference-status', 'disabled');
    await expect(page.locator('#inference-heading')).toHaveText('Rule-based recommendations');
    const mealHash = await startMeal(page);
    await checkin(page);
    await login(friend, 2, `/${mealHash}`);
    await checkin(friend, 'anything');
    await page.reload();
    const queued = page.waitForResponse(response => response.url().endsWith('/generate') && response.request().method() === 'POST');
    await role(page, 'button', 'Find a meal together').click();
    expect((await queued).status()).toBe(202);
    await page.reload(); // The HTTP request/browser is not the owner of the job.
    await role(page, 'heading', 'Your table’s shortlist').waitFor();
    const persistedResponse = await page.request.get(`/api/meals/${encodeURIComponent(mealHash.slice('#meal/'.length))}`);
    expect(persistedResponse.ok()).toBe(true);
    const persisted = await persistedResponse.json();
    expect(persisted.result.agent).toMatchObject({ model_calls: 0, model_status: 'disabled', inference_status: 'disabled' });
    await expect(page.locator('#inference-status')).toHaveAttribute('data-inference-status', 'not_called');
    await expect(page.locator('#inference-heading')).toHaveText('No LLM used');
    await a11y(page);
    await vote(page, 'Works for me');
    await friend.reload();
    await vote(friend, 'Cannot eat here', 'PRIVATE_BROWSER_VETO');
    await page.reload();
    await role(page, 'heading', 'Your table’s shortlist').waitFor();
    await expect(page.locator('body')).not.toContainText('PRIVATE_BROWSER_VETO');
    await expect(option(page).getByRole('button', { name: 'Choose for this meal', exact: true })).toBeDisabled();
    await vote(friend, 'Works for me');
    await page.reload();
    await option(page).getByRole('button', { name: 'Choose for this meal', exact: true }).click();
    await role(page, 'button', 'Confirm this choice').click();
    await expect(page.getByText('Selected for your table.', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText('Selected for your table.', { exact: true })).toBeVisible();
    await expect(page.locator('#inference-heading')).toHaveText('No LLM used');
    expect(errors).toEqual([]);
    await page.screenshot({ path: info.outputPath('selected-meal.png'), fullPage: true });
  } finally {
    await friendContext.close();
  }
});

test('private drafts survive navigation and a failed send; stale tabs cannot overwrite', async ({ page, context }) => {
  await login(page, 1);
  const mealHash = await startMeal(page);
  await a11y(page);
  if (!await role(page, 'radio', 'Join').isChecked()) await role(page, 'radio', 'Join').press('Space');
  await role(page, 'textbox', 'Describe your craving').fill('PRIVATE_UNSENT_DRAFT');
  await role(page, 'link', 'Home').click();
  await role(page, 'link', 'Open Nine-person E2E table').click();
  await page.locator(`a[href="${mealHash}"]`).click();
  await expect(role(page, 'textbox', 'Describe your craving')).toHaveValue('PRIVATE_UNSENT_DRAFT');
  if (!await role(page, 'radio', 'Join').isChecked()) await role(page, 'radio', 'Join').press('Space');
  await role(page, 'checkbox', 'My private food requirements are up to date for this meal.').check();
  await context.setOffline(true);
  await role(page, 'button', 'Send my check-in').click();
  await expect(page.locator('form[data-form="checkin"] .form-error')).not.toBeEmpty();
  await expect(role(page, 'textbox', 'Describe your craving')).toHaveValue('PRIVATE_UNSENT_DRAFT');
  await context.setOffline(false);
  const stale = await context.newPage();
  await stale.goto(`/${mealHash}`);
  await role(stale, 'heading', 'What sounds good today?').waitFor();
  await role(page, 'button', 'Send my check-in').click();
  await role(page, 'button', 'Update my check-in').waitFor();
  if (!await role(stale, 'radio', 'Join').isChecked()) await role(stale, 'radio', 'Join').press('Space');
  await role(stale, 'textbox', 'Describe your craving').fill('PRIVATE_STALE_DRAFT');
  await role(stale, 'checkbox', 'My private food requirements are up to date for this meal.').check();
  await role(stale, 'button', 'Send my check-in').click();
  await expect(role(stale, 'button', 'Review saved answer')).toBeVisible();
  await expect(role(stale, 'textbox', 'Describe your craving')).toHaveValue('PRIVATE_STALE_DRAFT');
  await page.reload();
  await expect(role(page, 'textbox', 'Describe your craving')).toHaveValue('PRIVATE_UNSENT_DRAFT');
  await stale.close();
});

test('keyboard dialogs and account switching keep private drafts scoped', async ({ page }) => {
  await page.goto('/');
  await a11y(page);
  await login(page, 1);
  await role(page, 'link', 'Profile').click();
  await role(page, 'heading', 'A little about your appetite.').waitFor();
  await a11y(page);
  await page.getByLabel('Allergens to avoid', { exact: true }).fill('PRIVATE_ACCOUNT_DRAFT');
  await role(page, 'button', 'Sign out').click();
  await role(page, 'button', 'Sign in').click();
  await page.getByLabel('Email address', { exact: true }).fill(email(2));
  await page.getByLabel('Password', { exact: true }).fill(password);
  await role(page, 'button', 'Sign in').click();
  await role(page, 'button', 'Sign out').waitFor();
  await role(page, 'link', 'Profile').click();
  await expect(page.getByLabel('Allergens to avoid', { exact: true })).toHaveValue('');
  await expect(page.locator('body')).not.toContainText('PRIVATE_ACCOUNT_DRAFT');
  await role(page, 'link', 'Home').click();
  const create = role(page, 'button', 'Create a table');
  await create.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await a11y(page);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(create).toBeFocused();
});

test('a delayed poll from a signed-out account cannot replace the next account inbox', async ({ page }) => {
  await login(page, 1);
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: /^Inbox/ }).click();
  await role(page, 'heading', 'Your inbox').waitFor();
  let release;
  let captured;
  const held = new Promise(resolve => { captured = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  let intercept = true;
  await page.route('**/api/notifications', async route => {
    if (!intercept) return route.continue();
    intercept = false;
    const response = await route.fetch();
    captured(await response.json());
    await gate;
    await route.fulfill({ response }).catch(() => {}); // An aborted old request is also correct.
  });
  await page.clock.install();
  await page.clock.fastForward(13000);
  const oldInbox = await held;
  await role(page, 'button', 'Sign out').click();
  await page.getByLabel('Email address', { exact: true }).fill(email(2));
  await page.getByLabel('Password', { exact: true }).fill(password);
  await role(page, 'button', 'Sign in').click();
  await role(page, 'button', 'Sign out').waitFor();
  const ownResponse = page.waitForResponse(r => r.url().endsWith('/api/notifications'));
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: /^Inbox/ }).click();
  const ownInbox = await (await ownResponse).json();
  await role(page, 'heading', 'Your inbox').waitFor();
  expect(ownInbox.notifications.map(n => n.id)).not.toEqual(oldInbox.notifications.map(n => n.id));
  release();
  await page.unrouteAll({ behavior: 'wait' });
  await expect(page.locator('article.notification')).toHaveCount(ownInbox.notifications.length);
  const oldOnly = oldInbox.notifications.filter(n => !ownInbox.notifications.some(other => other.id === n.id));
  expect(oldOnly.length).toBeGreaterThan(0);
  for (const notification of oldOnly) {
    await expect(page.locator(`[data-id="${notification.id}"], [data-read="${notification.id}"]`)).toHaveCount(0);
  }
});

test('interface switches to Bahasa Melayu with translated navigation and workflow labels', async ({ page }) => {
  await login(page, 1);
  // Click language switch button to switch to Malay
  await role(page, 'button', 'Change interface language').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'ms');

  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(nav.getByRole('link', { name: 'Utama', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Kumpulan', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: /^Peti masuk/ })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Profil', exact: true })).toBeVisible();

  // Switch back to English
  await role(page, 'button', 'Change interface language').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(nav.getByRole('link', { name: 'Home', exact: true })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Tables', exact: true })).toBeVisible();
});
