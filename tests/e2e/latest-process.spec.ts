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
  await page.getByLabel("Workspace").selectOption({ label: "Round3 Verify" });
}

test("latest process signs in, shows evidence, and verifies a repair", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The repair is checked once on desktop.");
  test.skip(!process.env.CIR_VERIFY_LATEST, "This check is opt-in so the demo workspace is not reset.");
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await signIn(page, credentials.users.operator, credentials.passwords.operator);
  await page.getByRole("row").filter({ hasText: "Quinn Adler" }).first().click();
  await expect(page.getByRole("heading", { name: "Quinn Adler" })).toBeVisible();
  await page.getByRole("button", { name: "Investigate" }).click();
  await expect(page.getByText("propose_repair")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Plan premium").first()).toBeVisible();
  await expect(page.getByText("Plan basic").first()).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, credentials.users.approver, credentials.passwords.approver);
  await page.getByRole("row").filter({ hasText: "Quinn Adler" }).first().click();
  await page.getByRole("button", { name: "Approve repair" }).click();
  await expect(page.getByText("Incident resolved: yes")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Repair verified: yes")).toBeVisible();
  expect(errors).toEqual([]);
});
