import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CareerPage from "./page";

const apiMocks = vi.hoisted(() => ({
  modelSettings: vi.fn(),
  careerPreferences: vi.fn(),
  careerProfile: vi.fn(),
  careerJobs: vi.fn(),
  currentCareerScoreBatch: vi.fn(),
  updateCareerProfile: vi.fn(),
  deleteCareerProfile: vi.fn(),
  importCareerJobUrl: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/career/CvProfileImport", () => ({
  default: () => <div data-testid="cv-profile-import" />,
}));

const defaultPreferences = {
  roles: "",
  locations: "",
  remote: "any",
  industries: "",
  must_have: "",
  avoid: "",
  match_mode: "both",
};

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    title: "Platform Engineer",
    company: "Example Ltd",
    location: "London",
    url: "https://example.com/jobs/1",
    description: "Build reliable software platforms.",
    source: "adzuna",
    status: "scored",
    fit_score: 82,
    decision: "apply",
    analysis: { summary: "Strong fit." },
    created_at: 1,
    updated_at: 2,
    ...overrides,
  };
}

describe("CareerPage tracker behavior", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    window.history.replaceState({}, "", "/career");
    apiMocks.modelSettings.mockResolvedValue({ task_models: { career: "test-model" } });
    apiMocks.careerPreferences.mockResolvedValue(defaultPreferences);
    apiMocks.careerProfile.mockResolvedValue({ cv_text: "Cloud CV", updated_at: 1 });
    apiMocks.careerJobs.mockResolvedValue([]);
    apiMocks.currentCareerScoreBatch.mockResolvedValue(null);
    apiMocks.updateCareerProfile.mockResolvedValue({ cv_text: "Cloud CV", updated_at: 1 });
    apiMocks.deleteCareerProfile.mockResolvedValue({ cv_text: "", updated_at: null });
    apiMocks.importCareerJobUrl.mockResolvedValue(job());
  });

  it("keeps a low-scoring manually saved job visible in Saved", async () => {
    const user = userEvent.setup();
    apiMocks.careerJobs.mockResolvedValue([
      job({
        title: "Manual analyst role",
        source: "manual",
        fit_score: 48,
        decision: "maybe",
      }),
    ]);

    render(<CareerPage />);

    await user.click(await screen.findByRole("button", { name: "Saved (1)" }));
    expect(screen.getByText("Manual analyst role")).toBeInTheDocument();
    expect(screen.getByText("48/100")).toBeInTheDocument();
    expect(screen.getByText("Saved")).toBeInTheDocument();
  });

  it("opens an imported low-scoring job in Saved instead of an empty Matches view", async () => {
    const user = userEvent.setup();
    apiMocks.importCareerJobUrl.mockResolvedValue(
      job({
        title: "Imported junior role",
        source: "lever",
        fit_score: 52,
        decision: "maybe",
      }),
    );

    render(<CareerPage />);

    await screen.findByText("test-model");
    await user.type(screen.getByPlaceholderText("https://jobs.lever.co/..."), "https://jobs.lever.co/example/1");
    await user.click(screen.getByRole("button", { name: "Import & Score" }));

    expect(await screen.findByText("Imported junior role")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Saved (1)" })).toHaveClass("text-brand-ink");
  });

  it("marks degraded scores on the card and explains them in Details", async () => {
    const user = userEvent.setup();
    apiMocks.careerJobs.mockResolvedValue([
      job({
        analysis: {
          summary: "Basic local comparison found matching keywords.",
          warning: "The AI provider was unavailable. Review this estimate manually.",
          degraded: true,
          risks: ["Keyword overlap only."],
        },
      }),
    ]);

    render(<CareerPage />);

    expect(await screen.findByText("Local fallback")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Details" }));
    expect(screen.getByText(/The AI provider was unavailable/)).toBeInTheDocument();
    expect(screen.getByText("Keyword overlap only.")).toBeInTheDocument();
  });

  it("keeps successful profile and job responses when model settings fail", async () => {
    window.localStorage.setItem("career-profile", "Stale local CV");
    apiMocks.modelSettings.mockRejectedValue(new Error("settings unavailable"));
    apiMocks.careerProfile.mockResolvedValue({ cv_text: "Current cloud CV", updated_at: 2 });
    apiMocks.careerJobs.mockResolvedValue([job({ title: "Still visible" })]);

    render(<CareerPage />);

    expect(await screen.findByText("Still visible")).toBeInTheDocument();
    expect(screen.getByLabelText("CV or profile text")).toHaveValue("Current cloud CV");
    expect(screen.getByText(/Could not load model settings/)).toBeInTheDocument();
    expect(apiMocks.updateCareerProfile).not.toHaveBeenCalled();
  });

  it("does not restore or autosave a stale local CV after the profile read fails", async () => {
    window.localStorage.setItem("career-profile", "Stale local CV");
    apiMocks.careerProfile.mockRejectedValue(new Error("profile unavailable"));

    render(<CareerPage />);

    expect(await screen.findByText(/Could not load CV\/profile/)).toBeInTheDocument();
    expect(screen.getByLabelText("CV or profile text")).toHaveValue("");
    await waitFor(() => expect(window.localStorage.getItem("career-profile")).toBeNull());
    expect(apiMocks.updateCareerProfile).not.toHaveBeenCalled();
    expect(apiMocks.deleteCareerProfile).not.toHaveBeenCalled();
  });

  it("hides an empty completed scoring batch", async () => {
    apiMocks.currentCareerScoreBatch.mockResolvedValue({
      id: "batch-empty",
      status: "completed",
      total: 0,
      completed: 0,
      failed: 0,
      cancelled: 0,
      remaining: 0,
      processed: 0,
      progress: 100,
      current_job: null,
    });

    render(<CareerPage />);

    await screen.findByText("test-model");
    expect(screen.queryByText("Background scoring complete")).not.toBeInTheDocument();
  });
});
