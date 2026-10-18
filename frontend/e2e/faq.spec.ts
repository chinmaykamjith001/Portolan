// M9 FAQ flow against the real backend (mock provider) and the seeded demo paper.
// Seed: backend/portolan/dev/seed_faq.py — at least six items over at least three source kinds,
// one of them unsupported.
//
// The Ask box is the one stubbed call: answering needs an LLM response, and the mock provider has
// no recorded fixture for an arbitrary question, so POST /api/papers/*/ask is intercepted with
// page.route and answered with a FAQItem. Everything else (list, filters, cross-links) is real.
//
//   PW_API_PORT=8773 PW_WEB_PORT=5183 pnpm e2e e2e/faq.spec.ts

import { expect, test, type Page } from "@playwright/test";

const PAPER = "demo:toyformer";

const faq = (page: Page) => page.getByRole("region", { name: "Questions about this paper" });
const items = (page: Page) => faq(page).getByRole("article");
const filters = (page: Page) => faq(page).getByRole("group", { name: "Filter by source" });

async function openFaq(page: Page) {
  await page.goto(`/?paper=${encodeURIComponent(PAPER)}`);
  await expect(page.getByRole("heading", { level: 1, name: /Toyformer/ })).toBeVisible();
  await page.getByRole("tab", { name: "FAQ" }).click();
  await expect(faq(page)).toBeVisible();
  await expect(items(page).first()).toBeVisible();
}

test("questions from at least two kinds of source, each labeled, filterable by kind", async ({ page }) => {
  await openFaq(page);

  expect(await items(page).count()).toBeGreaterThanOrEqual(6);

  // Source labels: plain text under every answer.
  const labels = await items(page).locator("p").filter({ hasText: /^(From |Finding|Anticipated|You asked|Your question)/ }).allTextContents();
  expect(labels.length).toBeGreaterThanOrEqual(6);

  // At least two kinds besides "All", each with a count.
  const kindButtons = filters(page).getByRole("button");
  const names = await kindButtons.allTextContents();
  expect(names[0]).toMatch(/^All \d+$/);
  expect(names.length - 1).toBeGreaterThanOrEqual(2);
  for (const n of names) expect(n).toMatch(/\d+$/);

  // Narrow to one kind: fewer items, and the pressed button says which.
  const total = await items(page).count();
  const first = kindButtons.nth(1);
  const count = Number((await first.textContent())?.match(/(\d+)$/)?.[1]);
  await first.click();
  await expect(first).toHaveAttribute("aria-pressed", "true");
  await expect(items(page)).toHaveCount(count);
  expect(count).toBeLessThan(total);

  await kindButtons.first().click();
  await expect(items(page)).toHaveCount(total);
});

test("an unsupported answer says so and links to nothing", async ({ page }) => {
  await openFaq(page);
  const unsupported = items(page).filter({ hasText: "Not supported by the sources" });
  await expect(unsupported.first()).toBeVisible();
  await expect(unsupported.first().getByRole("button", { name: /Show on Chart/ })).toHaveCount(0);
});

test("Show on Chart opens the Chart on that concept", async ({ page }) => {
  await openFaq(page);
  const link = faq(page).getByRole("button", { name: /^Show on Chart/ }).first();
  await expect(link).toBeVisible();
  const title = (await link.textContent())?.replace(/^Show on Chart for /, "").trim() ?? "";
  expect(title).not.toBe("Show on Chart"); // the concept is named from the cached map
  await link.click();

  await expect(page.getByRole("tab", { name: "Chart" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".react-flow")).toBeVisible();
  // The Chart reveals and selects the concept (pendingFocus, consumed by the Chart view).
  await expect(
    page.getByRole("complementary", { name: "Concept details" }).getByRole("heading", { level: 2, name: title }),
  ).toBeVisible();
});

test("asking a question puts the answer first", async ({ page }) => {
  const question = "Why divide by the square root of d_k?";
  await page.route("**/api/papers/*/ask", async (route) => {
    const body = route.request().postDataJSON() as { question: string };
    expect(body.question).toBe(question);
    await route.fulfill({
      json: {
        id: "asked-e2e",
        question,
        answer_markdown: "Large dot products push the softmax into a flat region, so Eq. 1 scales them down.",
        source_kind: "user_asked",
        source_url: null,
        source_label: "You asked",
        concept_ids: [],
        anchors_cited: ["eq:1"],
        supported: true,
      },
    });
  });
  await openFaq(page);
  const before = await items(page).count();

  await page.keyboard.press("/");
  const box = faq(page).getByRole("textbox", { name: "Ask about this paper" });
  await expect(box).toBeFocused();
  await box.fill(question);
  await box.press("Enter");

  const top = items(page).first();
  await expect(top).toContainText(question);
  await expect(top).toContainText("Large dot products push the softmax");
  await expect(top).toContainText("Just asked");
  await expect(top).toContainText("Eq. 1");
  await expect(items(page)).toHaveCount(before + 1);
  await expect(box).toHaveValue("");
});
