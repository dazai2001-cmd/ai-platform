import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CareerProfileImportResult } from "@/lib/api";
import CvProfileImport from "./CvProfileImport";

const { careerImportProfile } = vi.hoisted(() => ({
  careerImportProfile: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { careerImportProfile },
}));

const pdfResult: CareerProfileImportResult = {
  cv_text: "Platform engineer with production AI experience.",
  updated_at: 1234,
  filename: "rahul-cv.pdf",
  file_type: "pdf",
  characters: 4213,
  pages: 2,
  used_ocr: true,
};

describe("CvProfileImport", () => {
  beforeEach(() => {
    careerImportProfile.mockReset();
  });

  it("shows extraction progress and reports the imported PDF metadata", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    const onBusyChange = vi.fn();
    let finishImport!: (result: CareerProfileImportResult) => void;
    careerImportProfile.mockReturnValue(new Promise((resolve) => {
      finishImport = resolve;
    }));

    render(
      <CvProfileImport
        hasProfile={false}
        onBusyChange={onBusyChange}
        onImported={onImported}
      />,
    );

    const file = new File(["%PDF-1.4"], "rahul-cv.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("Choose CV file"), file);

    expect(careerImportProfile).toHaveBeenCalledWith(file);
    expect(screen.getByText("Reading rahul-cv.pdf and extracting editable text...")).toBeInTheDocument();
    expect(onBusyChange).toHaveBeenCalledWith(true);

    await act(async () => finishImport(pdfResult));

    expect(onImported).toHaveBeenCalledWith(pdfResult);
    expect(await screen.findByText("rahul-cv.pdf")).toBeInTheDocument();
    expect(screen.getByText("PDF / 4,213 characters / 2 pages / OCR used")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replace file" })).toBeInTheDocument();
    expect(onBusyChange).toHaveBeenLastCalledWith(false);
  });

  it("waits for an older profile write before starting the file import", async () => {
    const user = userEvent.setup();
    let releasePendingWrite!: () => void;
    const beforeImport = vi.fn(() => new Promise<void>((resolve) => {
      releasePendingWrite = resolve;
    }));
    careerImportProfile.mockResolvedValue(pdfResult);

    render(
      <CvProfileImport
        hasProfile
        beforeImport={beforeImport}
        onImported={vi.fn()}
      />,
    );

    await user.upload(
      screen.getByLabelText("Choose CV file"),
      new File(["%PDF-1.4"], "new-cv.pdf", { type: "application/pdf" }),
    );

    expect(beforeImport).toHaveBeenCalledOnce();
    expect(careerImportProfile).not.toHaveBeenCalled();

    await act(async () => releasePendingWrite());

    await waitFor(() => expect(careerImportProfile).toHaveBeenCalledOnce());
  });

  it("supports dropping a file and retries an API failure without replacing prior text", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    careerImportProfile
      .mockRejectedValueOnce(new Error("No readable text was found in this CV."))
      .mockResolvedValueOnce({ ...pdfResult, used_ocr: false });
    const file = new File(["%PDF-1.4"], "scan.pdf", { type: "application/pdf" });

    render(<CvProfileImport hasProfile onImported={onImported} />);
    fireEvent.drop(screen.getByRole("group", { name: "CV file drop zone" }), {
      dataTransfer: { files: [file] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("No readable text was found in this CV.");
    expect(onImported).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(onImported).toHaveBeenCalledOnce());
    expect(careerImportProfile).toHaveBeenCalledTimes(2);
    expect(screen.getByText("PDF / 4,213 characters / 2 pages / OCR not needed")).toBeInTheDocument();
  });

  it("shows Word metadata without a PDF-only OCR label", async () => {
    const user = userEvent.setup();
    const onImported = vi.fn();
    careerImportProfile.mockResolvedValue({
      ...pdfResult,
      filename: "rahul-cv.docx",
      file_type: "docx",
      pages: null,
      used_ocr: false,
    });
    const file = new File(["word document"], "rahul-cv.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });

    render(<CvProfileImport hasProfile={false} onImported={onImported} />);
    await user.upload(screen.getByLabelText("Choose CV file"), file);

    expect(await screen.findByText("DOCX / 4,213 characters")).toBeInTheDocument();
    expect(screen.queryByText(/OCR/)).not.toBeInTheDocument();
  });

  it("explains how to convert a legacy Word .doc file", async () => {
    const onImported = vi.fn();
    const file = new File(["legacy word"], "old-cv.doc", { type: "application/msword" });

    render(<CvProfileImport hasProfile={false} onImported={onImported} />);
    fireEvent.drop(screen.getByRole("group", { name: "CV file drop zone" }), {
      dataTransfer: { files: [file] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Legacy .doc files are not supported. Save the CV as .docx or export it as PDF",
    );
    expect(careerImportProfile).not.toHaveBeenCalled();
    expect(onImported).not.toHaveBeenCalled();
  });

  it("rejects files larger than 10 MB before upload", async () => {
    const onImported = vi.fn();
    const file = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "large-cv.pdf", {
      type: "application/pdf",
    });

    render(<CvProfileImport hasProfile={false} onImported={onImported} />);
    fireEvent.drop(screen.getByRole("group", { name: "CV file drop zone" }), {
      dataTransfer: { files: [file] },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("larger than the 10 MB limit");
    expect(careerImportProfile).not.toHaveBeenCalled();
    expect(onImported).not.toHaveBeenCalled();
  });
});
