import { expect, test } from "@playwright/test";
import { PRIYA, RAHUL, customerLogin, staffLogin, startReturn } from "./helpers";

test("approval: high-value refund waits, an approver approves, the customer sees the offer", async ({ browser }) => {
  const customer = await browser.newPage();
  await customerLogin(customer, PRIYA);
  await startReturn(customer, "DEMO-1005", "The smartwatch looks nothing like the pictures.", "not_as_described", "refund");
  await expect(customer.getByTestId("chat")).toContainText("being reviewed by our team");

  const staff = await browser.newPage();
  await staffLogin(staff, "approver@saferetuns.dev");
  await staff.getByRole("button", { name: "approval queue" }).click();
  await staff.getByTestId("queue-item").first().getByRole("link", { name: "Open case" }).click();
  await expect(staff.getByTestId("summary")).toContainText("Route approval");
  await staff.getByLabel("Reason code").selectOption("evidence_sufficient");
  await staff.getByRole("button", { name: "Submit decision" }).click();
  await expect(staff.getByRole("status")).toHaveText("Decision recorded.");

  await expect(customer.getByTestId("offer")).toBeVisible({ timeout: 15_000 });
  await expect(customer.getByTestId("offer")).toContainText("₹5,999.00");
});

test("fraud review: a heavy returner goes to a person, who resolves with a reason", async ({ browser }) => {
  const customer = await browser.newPage();
  await customerLogin(customer, RAHUL);
  await startReturn(customer, "DEMO-2001", "The jeans do not fit.", "size_fit", "refund");
  await expect(customer.getByTestId("chat")).toContainText("A specialist will look at your case");
  await expect(customer.getByTestId("chat")).not.toContainText(/fraud|suspicious/i);

  const staff = await browser.newPage();
  await staffLogin(staff, "agent@saferetuns.dev");
  await staff.getByRole("button", { name: "fraud review queue" }).click();
  await staff.getByTestId("queue-item").first().getByRole("link", { name: "Open case" }).click();
  await expect(staff.getByText("serial returner")).toBeVisible();
  await staff.getByLabel("Outcome").selectOption("cancelled");
  await staff.getByLabel("Reason code").selectOption("customer_withdrew");
  await staff.getByRole("button", { name: "Resolve case" }).click();
  await expect(staff.getByRole("status")).toHaveText("Case resolved.");
});
