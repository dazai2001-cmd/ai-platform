import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatPage from "./page";

const apiMocks = vi.hoisted(() => ({
  chatConversations: vi.fn(),
  createChatConversation: vi.fn(),
  getChatConversation: vi.fn(),
  deleteChatConversation: vi.fn(),
  saveChatConversation: vi.fn(),
  workspaceChat: vi.fn(),
  generalChat: vi.fn(),
  generalChatStream: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/chat/ChatWindow", () => ({
  default: ({ emptyTitle }: { emptyTitle: string }) => <div data-testid="chat-window">{emptyTitle}</div>,
}));

describe("ChatPage conversation loading", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.createChatConversation.mockImplementation((id: string, title: string) =>
      Promise.resolve({
        id,
        title,
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      }),
    );
    apiMocks.getChatConversation.mockResolvedValue({ id: "remote-chat", messages: [] });
    apiMocks.deleteChatConversation.mockResolvedValue({});
    apiMocks.saveChatConversation.mockResolvedValue({});
  });

  it("renders a usable local conversation while remote loading is pending", async () => {
    let resolveRemote: (value: any[]) => void = () => {};
    apiMocks.chatConversations.mockReturnValue(
      new Promise<any[]>((resolve) => {
        resolveRemote = resolve;
      }),
    );

    render(<ChatPage />);

    expect(screen.getByTestId("chat-window")).toHaveTextContent("Command your AI workspace");
    expect(screen.getByText("Syncing conversations")).toBeInTheDocument();
    expect(screen.getAllByText("New chat").length).toBeGreaterThan(0);

    resolveRemote([]);
    expect(await screen.findByText("Conversations synced")).toBeInTheDocument();
  });

  it("keeps the local chat available and retries with the same remote conversation id", async () => {
    const user = userEvent.setup();
    apiMocks.chatConversations.mockResolvedValue([]);
    apiMocks.createChatConversation
      .mockRejectedValueOnce(new Error("Conversation service unavailable"))
      .mockImplementationOnce((id: string, title: string) =>
        Promise.resolve({ id, title, messages: [], createdAt: Date.now(), updatedAt: Date.now() }),
      );

    render(<ChatPage />);

    expect(await screen.findByText("Working locally")).toBeInTheDocument();
    expect(screen.getByTestId("chat-window")).toBeInTheDocument();
    expect(screen.getByText("Conversation service unavailable")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Conversations synced")).toBeInTheDocument();

    await waitFor(() => expect(apiMocks.createChatConversation).toHaveBeenCalledTimes(2));
    expect(apiMocks.createChatConversation.mock.calls[1][0]).toBe(apiMocks.createChatConversation.mock.calls[0][0]);
  });
});
