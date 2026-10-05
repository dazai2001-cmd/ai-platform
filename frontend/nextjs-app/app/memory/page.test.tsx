import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import MemoryPage from "./page";

const mocks = vi.hoisted(() => ({ memoryFacts: vi.fn(), memorySessions: vi.fn(), getHistory: vi.fn(), addMemoryFact: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));
beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.memoryFacts.mockResolvedValue([]);
  mocks.memorySessions.mockResolvedValue([{ session_id: "first", messages: 1 }, { session_id: "second", messages: 1 }]);
});

it("ignores a slower history response for a previously selected session", async () => {
  const user = userEvent.setup();
  let finish!: (value: unknown) => void;
  mocks.getHistory.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValueOnce([{ role: "user", content: "Second conversation" }]);
  render(<MemoryPage />);
  await user.click(await screen.findByRole("button", { name: /first/ }));
  await user.click(screen.getByRole("button", { name: /second/ }));
  expect(await screen.findByText("Second conversation")).toBeInTheDocument();
  await act(async () => finish([{ role: "user", content: "First conversation" }]));
  expect(screen.queryByText("First conversation")).not.toBeInTheDocument();
  expect(screen.getByText("Second conversation")).toBeInTheDocument();
});

it("reports a failed memory write and retains the draft", async () => {
  const user = userEvent.setup();
  mocks.addMemoryFact.mockRejectedValue(new Error("Memory store unavailable"));
  render(<MemoryPage />);
  const input = screen.getByPlaceholderText(/Example:/);
  await user.type(input, "Keep this draft");
  await user.click(screen.getByRole("button", { name: "Add memory" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Memory store unavailable");
  expect(input).toHaveValue("Keep this draft");
});
