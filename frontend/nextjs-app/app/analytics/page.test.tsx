import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import AnalyticsPage from "./page";

const mocks = vi.hoisted(() => ({ analyticsSummary: vi.fn(), analyticsRecent: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));
beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.analyticsSummary.mockResolvedValue({ total_queries: 5, success_rate: 0, avg_latency_ms: 0, p95_latency_ms: 0 });
  mocks.analyticsRecent.mockResolvedValue([]);
});

it("renders zero success as 0% instead of an unknown value", async () => {
  render(<AnalyticsPage />);
  expect(await screen.findByText("0%")).toBeInTheDocument();
  expect(screen.getAllByText("0ms")).toHaveLength(2);
});

it("retains the summary and distinguishes failed requests from an empty history", async () => {
  mocks.analyticsRecent.mockRejectedValue(new Error("Analytics unavailable"));
  render(<AnalyticsPage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Refresh to retry");
  expect(screen.getByText("5")).toBeInTheDocument();
  expect(screen.queryByText("No recent queries.")).not.toBeInTheDocument();
});
