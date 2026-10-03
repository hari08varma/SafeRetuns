import { expect, type Page } from "@playwright/test";

export const PRIYA = "9000000001";
export const RAHUL = "9000000002";
export const STAFF_PASSWORD = process.env.E2E_STAFF_PASSWORD ?? "e2e-staff-password-123";

export async function customerLogin(page: Page, phone: string) {
  await page.goto("/login");
  await page.getByLabel("Mobile number").fill(phone);
  await page.getByRole("button", { name: "Send code" }).click();
  const code = (await page.getByTestId("dev-code").locator("strong").textContent())!.trim();
  await page.getByLabel("6-digit code").fill(code);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "My orders" })).toBeVisible();
}

export async function startReturn(
  page: Page,
  orderId: string,
  message: string,
  reason: string,
  wish: string,
) {
  await page.getByRole("button", { name: new RegExp(`from ${orderId}$`) }).click();
  await page.getByLabel("What happened?").fill(message);
  await page.getByLabel("Reason (optional)").selectOption(reason);
  await page.getByLabel("What would you like? (optional)").selectOption(wish);
  await page.getByRole("button", { name: "Start return" }).click();
  await expect(page.getByRole("heading", { name: "Your return" })).toBeVisible();
}

export async function staffLogin(page: Page, email: string) {
  await page.goto("/console/login");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(STAFF_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Support console" })).toBeVisible();
}
