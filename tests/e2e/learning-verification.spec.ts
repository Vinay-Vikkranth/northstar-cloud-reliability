import { expect, test } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

const credentials = JSON.parse(readFileSync(resolve(__dirname, '../../var/local-credentials.json'), 'utf8'));

test('independent learning walkthrough repairs one account and refuses unnecessary or unmapped changes', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop' || !process.env.CIR_LEARNING_VERIFY,
    'Explicit opt-in; operates only on Independent Learning Check fictional records.');
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  const screenshots = resolve(__dirname, '../../var/learning-verification');
  mkdirSync(screenshots, { recursive: true });
  async function login(role: 'operator' | 'approver') {
    await page.goto('/');
    await page.getByLabel('Username').fill(credentials.users[role]);
    await page.getByLabel('Password').fill(credentials.passwords[role]);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('heading', { name: 'Incident queue' })).toBeVisible();
    await page.getByLabel('Workspace').selectOption({ label: process.env.CIR_LEARNING_WORKSPACE || 'Independent Learning Check' });
  }
  async function openAccount(name: string) {
    await page.getByRole('button', { name: 'Incidents', exact: true }).click();
    await page.getByRole('row').filter({ hasText: name }).first().click();
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  }
  await login('operator');
  await openAccount('Alex Practice');
  await page.getByRole('button', { name: 'Investigate', exact: true }).click();
  await expect(page.getByText('propose_repair', { exact: true })).toBeVisible({ timeout: 30000 });
  await expect(page.getByText('Plan premium', { exact: true })).toBeVisible();
  await expect(page.getByText('Plan basic', { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(screenshots, '01-evidence.png'), fullPage: true });
  await page.getByRole('button', { name: 'Sign out' }).click();
  await login('approver');
  await openAccount('Alex Practice');
  await page.getByRole('button', { name: 'Approve repair', exact: true }).click();
  await expect(page.getByText('Incident resolved: yes.')).toBeVisible({ timeout: 30000 });
  await expect(page.getByText('Repair verified: yes, a fresh read matched the approved plan.')).toBeVisible();
  await page.screenshot({ path: resolve(screenshots, '02-verified.png'), fullPage: true });
  await page.getByRole('button', { name: 'Sign out' }).click();
  await login('operator');
  await openAccount('Jordan Healthy');
  await page.getByRole('button', { name: 'Investigate', exact: true }).click();
  await expect(page.getByText('no_mismatch', { exact: true })).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole('button', { name: 'Approve repair', exact: true })).toHaveCount(0);
  await openAccount('Casey Unknown Plan');
  await page.getByRole('button', { name: 'Investigate', exact: true }).click();
  await expect(page.getByText('mapping_mismatch', { exact: true })).toBeVisible({ timeout: 30000 });
  await expect(page.getByRole('button', { name: 'Approve repair', exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});
