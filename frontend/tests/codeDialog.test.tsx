import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { CodeDialog } from "../src/app/CodeDialog";
import { useUI } from "../src/state/store";

type Call = { url: string; init?: RequestInit };

function backend(post: { status: number; body: unknown }) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (init?.method === "POST") return new Response(JSON.stringify(post.body), { status: post.status });
      return new Response(
        JSON.stringify({
          candidates: [
            { id: "gh:me/toy", url: "https://github.com/me/toy", owner: "me", name: "toy", official: true,
              official_evidence: null, status: "ok", stars: 3 },
          ],
          status: { kind: "official_found", message: "Official code found" },
        }),
        { status: 200 },
      );
    }),
  );
  return calls;
}

function mount() {
  useUI.setState({ paperId: "upload:abc", dialog: "code" });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <CodeDialog />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("lists found code and links the chosen candidate", async () => {
  const calls = backend({ status: 200, body: { repo: {}, status: "indexing" } });
  mount();
  expect(await screen.findByText("Official code found")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Use me/toy" }));
  await waitFor(() => expect(useUI.getState().dialog).toBeNull());
  const post = calls.find((c) => c.init?.method === "POST");
  expect(post?.url).toBe("/api/papers/upload%3Aabc/repo");
  expect(JSON.parse(String(post?.init?.body))).toEqual({ candidate_id: "gh:me/toy" });
});

test("a folder path that isn't usable shows the server's reason and keeps the dialog open", async () => {
  backend({ status: 400, body: { error: { code: "bad_folder", message: "C:\\nope isn't a folder Portolan can read." } } });
  mount();
  fireEvent.change(screen.getByLabelText("Or a folder on this computer"), { target: { value: "C:\\nope" } });
  fireEvent.click(screen.getByRole("button", { name: "Use folder" }));
  expect(await screen.findByText("C:\\nope isn't a folder Portolan can read.")).toBeInTheDocument();
  expect(useUI.getState().dialog).toBe("code");
});
