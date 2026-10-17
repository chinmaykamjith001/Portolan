import { render, screen } from "@testing-library/react";

import { App } from "../src/app/App";

test("empty state shows the input and example papers", () => {
  render(<App />);
  expect(screen.getByLabelText("Paper")).toHaveAttribute(
    "placeholder",
    "Paste an arXiv link, DOI, PDF or title",
  );
  expect(screen.getByText("Attention Is All You Need")).toBeInTheDocument();
});
