import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import DocumentsPage from "./page";

const mocks = vi.hoisted(() => ({ ragDocuments: vi.fn(), ragDocumentPreview: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));
beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.ragDocuments.mockResolvedValue([
    { source: "first", title: "First guide", chunks: 1, type: "note" },
    { source: "second", title: "Second guide", chunks: 1, type: "note" },
  ]);
});

it("ignores an older preview response after selecting another document", async () => {
  const user = userEvent.setup();
  let finish!: (value: unknown) => void;
  mocks.ragDocumentPreview.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce({ text: "Second document content" });
  render(<DocumentsPage />);
  await user.click(await screen.findByRole("button", { name: /First guide/ }));
  await user.click(screen.getByRole("button", { name: /Second guide/ }));
  expect(await screen.findByText("Second document content")).toBeInTheDocument();
  await act(async () => finish({ text: "First document content" }));
  expect(screen.queryByText("First document content")).not.toBeInTheDocument();
  expect(screen.getByText("Second document content")).toBeInTheDocument();
});

it("stops the loading indicator when preview loading fails", async () => {
  const user = userEvent.setup();
  mocks.ragDocumentPreview.mockRejectedValue(new Error("Preview unavailable"));
  render(<DocumentsPage />);
  await user.click(await screen.findByRole("button", { name: /First guide/ }));
  expect(await screen.findByText("Preview unavailable")).toBeInTheDocument();
  expect(document.querySelector(".animate-spin")).toBeNull();
});
