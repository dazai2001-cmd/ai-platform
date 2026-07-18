import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "./page";

const apiMocks = vi.hoisted(() => ({
  biAsk: vi.fn(),
  biUpload: vi.fn(),
  biDatasets: vi.fn(),
  biDeleteDataset: vi.fn(),
}));

const chatHarness = vi.hoisted(() => ({
  props: null as any,
  message: null as any,
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

vi.mock("@/components/chat/ChatWindow", () => ({
  default: (props: any) => {
    chatHarness.props = props;
    return (
      <div data-testid="chat-window">
        {chatHarness.message ? props.renderExtra?.(chatHarness.message) : null}
      </div>
    );
  },
}));

vi.mock("@/components/charts/ChartRenderer", () => ({
  default: () => <div data-testid="chart-renderer">Chart</div>,
}));

const sales = { name: "sales", rows: 12, columns: ["region", "revenue"] };
const inventory = { name: "inventory", rows: 4, columns: ["sku", "stock"] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("BI dashboard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    chatHarness.props = null;
    chatHarness.message = null;
    apiMocks.biDatasets.mockResolvedValue([sales, inventory]);
    apiMocks.biAsk.mockResolvedValue({ answer: "Done", rows: [] });
    apiMocks.biDeleteDataset.mockResolvedValue({ deleted: "sales" });
  });

  it("auto-selects the first loaded dataset and always sends its name and abort signal", async () => {
    render(<DashboardPage />);

    expect(await screen.findByText("Active dataset: sales")).toBeInTheDocument();
    const controller = new AbortController();
    await act(async () => {
      await chatHarness.props.onSend("Show revenue", controller.signal);
    });

    expect(apiMocks.biAsk).toHaveBeenCalledWith(
      "Show revenue",
      expect.any(String),
      "sales",
      controller.signal,
    );
  });

  it("does not call BI when no dataset is selected", async () => {
    apiMocks.biDatasets.mockResolvedValue([]);
    render(<DashboardPage />);

    expect(await screen.findByText("No datasets loaded")).toBeInTheDocument();
    await expect(chatHarness.props.onSend("Show revenue")).rejects.toThrow(
      "Select or upload a dataset before asking a question.",
    );
    expect(apiMocks.biAsk).not.toHaveBeenCalled();
    expect(chatHarness.props.disabled).toBe(true);
  });

  it("retries one transient dataset-list failure before showing the loaded data", async () => {
    apiMocks.biDatasets
      .mockRejectedValueOnce(new Error("Temporary network failure"))
      .mockResolvedValueOnce([sales]);
    render(<DashboardPage />);

    expect(await screen.findByText("Active dataset: sales", {}, { timeout: 2_000 })).toBeInTheDocument();
    expect(apiMocks.biDatasets).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows a retry action after the bounded dataset-list retry fails", async () => {
    apiMocks.biDatasets.mockRejectedValue(new Error("Dataset service unavailable"));
    render(<DashboardPage />);

    expect(await screen.findByRole("alert", {}, { timeout: 2_000 })).toHaveTextContent(
      "Dataset service unavailable",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(apiMocks.biDatasets).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("No datasets loaded")).not.toBeInTheDocument();
  });

  it("deletes a dataset, selects the next one, and resets the chat", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DashboardPage />);

    expect(await screen.findByText("Active dataset: sales")).toBeInTheDocument();
    const firstResetKey = chatHarness.props.resetKey;
    await user.click(screen.getByRole("button", { name: "Delete sales" }));

    expect(apiMocks.biDeleteDataset).toHaveBeenCalledWith("sales");
    expect(await screen.findByText("Active dataset: inventory")).toBeInTheDocument();
    expect(screen.queryByText("12 rows / 2 columns")).not.toBeInTheDocument();
    expect(chatHarness.props.resetKey).not.toBe(firstResetKey);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^inventory 4 rows/i })).toHaveFocus();
    });
  });

  it("disables dataset controls while deleting so selection cannot race the response", async () => {
    const user = userEvent.setup();
    const deletion = deferred<{ deleted: string }>();
    apiMocks.biDeleteDataset.mockReturnValue(deletion.promise);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<DashboardPage />);

    expect(await screen.findByText("Active dataset: sales")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete inventory" }));

    expect(screen.getByRole("button", { name: "Deleting inventory" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByRole("button", { name: /^inventory 4 rows/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Upload CSV / Excel" })).toBeDisabled();

    await act(async () => {
      deletion.resolve({ deleted: "inventory" });
      await deletion.promise;
    });
    expect(await screen.findByText("inventory deleted")).toBeInTheDocument();
    expect(screen.getByText("Active dataset: sales")).toBeInTheDocument();
  });

  it("offers a real retry after a delete failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    apiMocks.biDeleteDataset
      .mockRejectedValueOnce(new Error("Delete service unavailable"))
      .mockResolvedValueOnce({ deleted: "sales" });
    render(<DashboardPage />);

    expect(await screen.findByText("Active dataset: sales")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete sales" }));
    expect(await screen.findByText("Delete service unavailable")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry deleting sales" }));
    expect(apiMocks.biDeleteDataset).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("sales deleted")).toBeInTheDocument();
  });

  it("does not restore an old dataset's chart after the user switches datasets", async () => {
    const user = userEvent.setup();
    const answer = deferred<any>();
    apiMocks.biAsk.mockReturnValue(answer.promise);
    render(<DashboardPage />);

    expect(await screen.findByText("Active dataset: sales")).toBeInTheDocument();
    const pending = chatHarness.props.onSend("Show revenue");
    await user.click(screen.getByRole("button", { name: /^inventory 4 rows/i }));
    expect(await screen.findByText("Active dataset: inventory")).toBeInTheDocument();

    await act(async () => {
      answer.resolve({ answer: "Old chart", chart: { chart_type: "bar" }, rows: [] });
      await pending;
    });
    expect(screen.queryByText("Last Chart")).not.toBeInTheDocument();
  });

  it("renders chart answers with their SQL, rows, and readable number formatting", async () => {
    chatHarness.message = {
      role: "assistant",
      content: "Monthly revenue",
      chart: { chart_type: "bar", data: { labels: [], values: [] } },
      sql: "SELECT month, revenue FROM dataset",
      rows: [{
        month: "January",
        revenue: 2550,
        percentage_returned: 16.6666667,
        percentage_tiny: 0.004,
        precise_ratio: 0.004,
        tiny_metric: 0.0000004,
      }],
    };
    render(<DashboardPage />);

    expect(screen.getByTestId("chart-renderer")).toBeInTheDocument();
    expect(screen.getByText("SELECT month, revenue FROM dataset")).toBeInTheDocument();
    expect(screen.getByText("2,550")).toBeInTheDocument();
    expect(screen.getByText("16.67%")).toBeInTheDocument();
    expect(screen.getByText("0.004%")).toBeInTheDocument();
    expect(screen.getByText("0.004")).toBeInTheDocument();
    expect(screen.getByText("4.0000e-7")).toBeInTheDocument();
  });

  it("normalizes numeric-leading filenames to the backend dataset convention", async () => {
    const user = userEvent.setup();
    const uploaded = { name: "dataset_2026_sales", rows: 1, columns: ["revenue"] };
    apiMocks.biUpload.mockResolvedValue(uploaded);
    apiMocks.biDatasets
      .mockResolvedValueOnce([sales, inventory])
      .mockResolvedValueOnce([uploaded, sales, inventory]);
    render(<DashboardPage />);
    await screen.findByText("Active dataset: sales");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["revenue\n10"], "2026 Sales.csv", { type: "text/csv" });
    await user.upload(input, file);

    await waitFor(() => expect(apiMocks.biUpload).toHaveBeenCalledWith(file, "dataset_2026_sales"));
    expect(await screen.findByText("dataset_2026_sales loaded - 1 rows")).toBeInTheDocument();
  });

  it("makes normalized-name collisions visible and uploads under a confirmed unique name", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    apiMocks.biUpload.mockResolvedValue({ name: "sales_2", rows: 1, columns: ["revenue"] });
    render(<DashboardPage />);
    await screen.findByText("Active dataset: sales");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["revenue\n10"], "Sales!!.csv", { type: "text/csv" });
    await user.upload(input, file);
    expect(apiMocks.biUpload).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledWith(
      'A dataset named "sales" already exists. Upload this file as "sales_2" instead?',
    );

    await user.upload(input, file);
    await waitFor(() => expect(apiMocks.biUpload).toHaveBeenCalledWith(file, "sales_2"));
    expect(await screen.findByText("sales_2 loaded - 1 rows")).toBeInTheDocument();
  });

  it("resets the session and last chart even if an upload response reuses the active name", async () => {
    const user = userEvent.setup();
    apiMocks.biAsk.mockResolvedValue({ answer: "Chart", chart: { chart_type: "bar" }, rows: [] });
    apiMocks.biUpload.mockResolvedValue({ name: "sales", rows: 2, columns: ["revenue"] });
    render(<DashboardPage />);

    expect(await screen.findByText("Active dataset: sales")).toBeInTheDocument();
    await act(async () => {
      await chatHarness.props.onSend("Show revenue");
    });
    expect(screen.getByText("Last Chart")).toBeInTheDocument();
    const firstResetKey = chatHarness.props.resetKey;

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["revenue\n20"], "replacement.csv", { type: "text/csv" }));

    expect(await screen.findByText("sales loaded - 2 rows")).toBeInTheDocument();
    await waitFor(() => expect(chatHarness.props.resetKey).not.toBe(firstResetKey));
    expect(screen.queryByText("Last Chart")).not.toBeInTheDocument();
  });

  it("styles upload failures as errors instead of successes", async () => {
    const user = userEvent.setup();
    apiMocks.biUpload.mockResolvedValue({ error: "The spreadsheet could not be read" });
    render(<DashboardPage />);
    await screen.findByText("Active dataset: sales");

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["region,revenue"], "broken.csv", { type: "text/csv" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("The spreadsheet could not be read");
    });
    expect(screen.getByRole("alert")).toHaveClass("text-danger-ink");
  });
});
