import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import SettingsPage from "./page";

const mocks = vi.hoisted(() => ({
  health: vi.fn(), analyticsSummary: vi.fn(), modelSettings: vi.fn(),
  updateModelSettings: vi.fn(), resetModelSettings: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: mocks }));
const models = { task_models: { general: "first", rag: "first" }, available_models: ["first", "second"] };

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.health.mockResolvedValue({ status: "ok", runtime: "local", checks: { model_provider: true } });
  mocks.analyticsSummary.mockResolvedValue({ total_queries: 12, success_rate: 0 });
  mocks.modelSettings.mockResolvedValue(models);
  mocks.updateModelSettings.mockImplementation(async (task_models) => ({ ...models, task_models }));
});

it("keeps model settings and analytics when the health request fails", async () => {
  mocks.health.mockRejectedValue(new Error("Provider offline"));
  render(<SettingsPage />);
  expect(await screen.findByRole("combobox", { name: "general" })).toHaveValue("first");
  expect(screen.getByText("12")).toBeInTheDocument();
  expect(screen.getByText("0%")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("runtime health");
});

it("shows models from the settings endpoint and the provider readiness check", async () => {
  mocks.health.mockResolvedValue({ status: "degraded", runtime: "local", checks: { database: false, model_provider: true } });
  render(<SettingsPage />);
  await screen.findByRole("combobox", { name: "general" });
  expect(screen.getByText("Connected")).toBeInTheDocument();
  expect(screen.queryByText("No model list available.")).not.toBeInTheDocument();
});

it("serializes rapid model edits and reports a saved write even if health is unavailable", async () => {
  const user = userEvent.setup();
  let finish!: (value: unknown) => void;
  mocks.updateModelSettings.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<SettingsPage />);
  await user.selectOptions(await screen.findByRole("combobox", { name: "general" }), "second");
  await waitFor(() => expect(mocks.updateModelSettings).toHaveBeenCalledTimes(1));
  await user.selectOptions(screen.getByRole("combobox", { name: "rag" }), "second");
  expect(mocks.updateModelSettings).toHaveBeenCalledTimes(1);
  mocks.health.mockRejectedValue(new Error("Provider offline"));
  await act(async () => finish({ ...models, task_models: { general: "second", rag: "first" } }));
  await waitFor(() => expect(mocks.updateModelSettings).toHaveBeenCalledTimes(2));
  expect(mocks.updateModelSettings).toHaveBeenLastCalledWith({ general: "second", rag: "second" });
  expect(await screen.findByText("Saved")).toBeInTheDocument();
});

it("shows a reset failure and retains the selected models", async () => {
  const user = userEvent.setup();
  mocks.resetModelSettings.mockRejectedValue(new Error("Reset unavailable"));
  render(<SettingsPage />);
  await screen.findByRole("combobox", { name: "general" });
  await user.click(screen.getByRole("button", { name: "Reset" }));
  expect(await screen.findByText("Could not reset")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "general" })).toHaveValue("first");
});
