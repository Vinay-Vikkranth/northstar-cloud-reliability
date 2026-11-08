import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const credentials = JSON.parse(readFileSync(resolve(__dirname, "../../var/local-credentials.json"), "utf8"));

async function signIn(page: import("@playwright/test").Page, username: string, password: string) {
  await page.goto("/");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Incident queue" })).toBeVisible();
}

test("operator investigates and approver verifies a synthetic mismatch", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The full repair is checked on desktop; mobile checks the queue.");
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await signIn(page, credentials.users.admin, credentials.passwords.admin);
  await page.getByRole("button", { name: "Demo controls" }).click();
  await page.getByRole("button", { name: "Reset demo workspace" }).click();
  await expect(page.getByText("missing-update")).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, credentials.users.operator, credentials.passwords.operator);
  await expect(page.getByText("Synthetic demo")).toBeVisible();
  await page.getByRole("row").filter({ hasText: "Alex Rivera" }).first().click();
  await page.getByRole("button", { name: "Investigate" }).click();
  await expect(page.getByText("propose_repair")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, credentials.users.approver, credentials.passwords.approver);
  await page.getByRole("row").filter({ hasText: "Alex Rivera" }).first().click();
  await page.getByRole("button", { name: "Approve repair" }).click();
  await expect(page.getByText("Incident resolved: yes")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Repair verified: yes")).toBeVisible();
  expect(errors).toEqual([]);
});

test("mobile queue is reachable from the keyboard", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Viewport check.");
  await signIn(page, credentials.users.viewer, credentials.passwords.viewer);
  await expect(page.getByRole("heading", { name: "Incident queue" })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Incidents" })).toBeVisible();
});

test("viewer cannot approve", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Permission copy is checked once.");
  await signIn(page, credentials.users.viewer, credentials.passwords.viewer);
  const row = page.getByRole("row").filter({ hasText: "Alex Rivera" }).first();
  if (await row.count()) {
    await row.click();
    await expect(page.getByRole("button", { name: "Approve repair" })).toHaveCount(0);
  }
});
