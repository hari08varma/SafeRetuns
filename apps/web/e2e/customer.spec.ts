import { expect, test } from "@playwright/test";
import { PRIYA, customerLogin, startReturn } from "./helpers";

test("size problem: offer with the exact amount, accepted, pickup booked", async ({ page }) => {
  await customerLogin(page, PRIYA);
  await startReturn(page, "DEMO-1001", "The kurta is too tight on the shoulders.", "size_fit", "refund");
  const offer = page.getByTestId("offer");
  await expect(offer).toContainText("₹1,299.00");
  await offer.getByLabel("Refund (we collect the item)").check();
  await offer.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("chat")).toContainText("Your pickup is booked", { timeout: 30_000 });
  await expect(page.getByText("You are chatting with an AI assistant").first()).toBeVisible();
});

test("cheap cosmetic: refund without returning the item", async ({ page }) => {
  await customerLogin(page, PRIYA);
  await startReturn(page, "DEMO-1002", "The serum is nothing like the description.", "not_as_described", "refund");
  const offer = page.getByTestId("offer");
  await offer.getByLabel("Refund, keep the item").check();
  await offer.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByTestId("status")).toHaveText("Closed", { timeout: 30_000 });
  await expect(page.getByTestId("chat")).toContainText("Your return is complete");
});

test("final sale: declined with the clause, then a human review is requested", async ({ page }) => {
  await customerLogin(page, PRIYA);
  await startReturn(page, "DEMO-1003", "It is too big.", "size_fit", "refund");
  await expect(page.getByTestId("chat")).toContainText("Final-sale items cannot be returned");
  await page.getByRole("button", { name: "Ask for a human review" }).click();
  await expect(page.getByRole("status")).toContainText("will review this decision");
});

test("damaged item: photo upload, then replacement offered", async ({ page }) => {
  await customerLogin(page, PRIYA);
  await startReturn(page, "DEMO-1004", "The earbuds case arrived cracked.", "damaged", "replacement");
  await page.getByLabel(/Photos of the item/).setInputFiles("e2e/fixtures/cracked-case.jpg");
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByTestId("offer")).toContainText("Replacement");
});
