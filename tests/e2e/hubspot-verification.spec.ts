import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('real HubSpot read and local AI produce a separately approved local repair', async ({ page }, info) => {
  test.skip(!process.env.CIR_HUBSPOT_VERIFY || info.project.name !== 'desktop',
    'Opt-in only: uses one explicitly configured fictional contact and local support fixture.');
  test.setTimeout(120000);
  const credentials = JSON.parse(readFileSync(resolve(__dirname, '../../var/local-credentials.json'), 'utf8'));
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  async function login(role: 'operator' | 'approver') {
    await page.goto('/');
    await page.getByLabel('Username').fill(credentials.users[role]);
    await page.getByLabel('Password').fill(credentials.passwords[role]);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.getByLabel('Workspace').selectOption({label: 'HubSpot + Local AI'});
    await page.getByRole('row').filter({hasText: 'Alex Recovery Demo'}).click();
    await expect(page.getByRole('heading', {name: 'Alex Recovery Demo', exact: true})).toBeVisible();
  }
  await login('operator');
  await expect(page.getByText('Source: external_crm', {exact: true})).toBeVisible();
  await expect(page.getByText('Investigator: live', {exact: true})).toBeVisible();
  await expect(page.getByText('Provider: openai_compatible / llama3:latest', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Investigate', exact: true}).click();
  await expect(page.getByText('propose_repair', {exact: true})).toBeVisible({timeout: 60000});
  await expect(page.getByText('Plan premium', {exact: true})).toBeVisible();
  await expect(page.getByText('Plan basic', {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Approve repair', exact: true})).toHaveCount(0);
  await page.screenshot({path: resolve(__dirname, '../../var/hubspot-before-approval.png'), fullPage: true});
  await page.getByRole('button', {name: 'Sign out', exact: true}).click();
  await login('approver');
  await page.getByRole('button', {name: 'Approve repair', exact: true}).click();
  await expect(page.getByText('Incident resolved: yes.')).toBeVisible({timeout: 45000});
  await expect(page.getByText('Repair verified: yes, a fresh read matched the approved plan.')).toBeVisible();
  await page.screenshot({path: resolve(__dirname, '../../var/hubspot-verified.png'), fullPage: true});
  expect(errors).toEqual([]);
});
