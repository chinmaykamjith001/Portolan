import { api } from "../src/api/client";

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
    async () => new Response(JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

test("a queued paper (202) still returns its paper id", async () => {
  mockFetch(202, { paper_id: "arxiv:2101.00001v1", status: "queued" });
  const res = await api.createPaper("2101.00001");
  expect(res).toEqual({ pending: false, data: { paper_id: "arxiv:2101.00001v1", status: "queued" } });
});

test("202 on a detail means still generating", async () => {
  mockFetch(202, { status: "generating" });
  expect(await api.explanation("p", "c")).toEqual({ pending: true });
});

test("uploadPdf posts multipart form data without a JSON content type", async () => {
  const fetch = mockFetch(202, { paper_id: "upload:abc", status: "queued" });
  const file = new File(["%PDF-1.4"], "paper.pdf", { type: "application/pdf" });
  const res = await api.uploadPdf(file);
  expect(res).toEqual({ pending: false, data: { paper_id: "upload:abc", status: "queued" } });
  const init = fetch.mock.calls[0]?.[1];
  expect(init?.body).toBeInstanceOf(FormData);
  expect((init?.body as FormData).get("file")).toBeInstanceOf(File);
  expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
});

test("JSON calls keep their content type", async () => {
  const fetch = mockFetch(200, { paper_id: "x", status: "ready" });
  await api.createPaper("x");
  expect(new Headers(fetch.mock.calls[0]?.[1]?.headers).get("Content-Type")).toBe("application/json");
});

test("errors carry the server's plain message", async () => {
  mockFetch(413, { error: { code: "too_large", message: "That PDF is larger than 50 MB." } });
  const file = new File(["%PDF"], "big.pdf", { type: "application/pdf" });
  await expect(api.uploadPdf(file)).rejects.toMatchObject({ status: 413, code: "too_large" });
});
