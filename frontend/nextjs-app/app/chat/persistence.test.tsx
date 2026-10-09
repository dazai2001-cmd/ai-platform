import { useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatPage from "./page";
import BrainPage from "@/app/brain/page";
import DashboardPage from "@/app/dashboard/page";
import { ChatStateProvider } from "@/lib/chat-state";

const auth = vi.hoisted(() => ({ owner: "owner-a", loading: false }));
const apiMocks = vi.hoisted(() => ({
  chatConversations: vi.fn(),
  createChatConversation: vi.fn(),
  getChatConversation: vi.fn(),
  deleteChatConversation: vi.fn(),
  saveChatConversation: vi.fn(),
  workspaceChat: vi.fn(),
  generalChat: vi.fn(),
  generalChatStream: vi.fn(),
  localCapabilities: vi.fn(),
  localRuns: vi.fn(),
  resumeLocalTask: vi.fn(),
  ragAsk: vi.fn(),
  ragAskStream: vi.fn(),
  biDatasets: vi.fn(),
  biAsk: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ user: { id: auth.owner }, loading: auth.loading, authRequired: true }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));
vi.mock("@/components/charts/ChartRenderer", () => ({ default: () => <div>Chart</div> }));

function Pages() {
  const [page, setPage] = useState("chat");
  return (
    <>
      <nav>
        {["chat", "brain", "bi", "away"].map((name) => (
          <button key={name} onClick={() => setPage(name)}>Show {name}</button>
        ))}
      </nav>
      {page === "chat" && <ChatPage />}
      {page === "brain" && <BrainPage />}
      {page === "bi" && <DashboardPage />}
      {page === "away" && <div>Other page</div>}
    </>
  );
}

function App() {
  return <ChatStateProvider><Pages /></ChatStateProvider>;
}

const sales = { name: "sales", rows: 12, columns: ["revenue"] };
const inventory = { name: "inventory", rows: 4, columns: ["stock"] };

describe("Chat persistence across pages", () => {
  beforeEach(() => {
    auth.owner = "owner-a";
    auth.loading = false;
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.localCapabilities.mockResolvedValue({ enabled: false });
    apiMocks.localRuns.mockResolvedValue([]);
    apiMocks.chatConversations.mockResolvedValue([]);
    apiMocks.createChatConversation.mockImplementation(async (id, title) => ({
      id, title, messages: [], createdAt: Date.now(), updatedAt: Date.now(),
    }));
    apiMocks.getChatConversation.mockResolvedValue({ id: "saved-chat", messages: [] });
    apiMocks.saveChatConversation.mockResolvedValue({});
    apiMocks.deleteChatConversation.mockResolvedValue({});
    apiMocks.workspaceChat.mockImplementation(async (question) => ({ answer: `Answer to ${question}`, route: "general" }));
    apiMocks.generalChatStream.mockImplementation(async () => new Response("General answer"));
    apiMocks.ragAskStream.mockImplementation(async () => new Response("Brain answer"));
    apiMocks.biDatasets.mockResolvedValue([sales, inventory]);
    apiMocks.biAsk.mockImplementation(async (_question, _session, dataset) => ({ answer: `${dataset} analysis` }));
  });

  it("displays server history after loading and keeps conversations separate when switching", async () => {
    const user = userEvent.setup();
    const now = Date.now() / 1000;
    apiMocks.chatConversations.mockResolvedValue([
      { id: "first", title: "First saved chat", messages: 1, createdAt: now, updatedAt: now },
      { id: "second", title: "Second saved chat", messages: 1, createdAt: now, updatedAt: now },
    ]);
    apiMocks.getChatConversation.mockImplementation(async (id) => ({
      id, messages: [{ role: "assistant", content: `${id} saved answer` }],
    }));
    render(<App />);

    expect(await screen.findByText("first saved answer")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Second saved chat/ }));
    expect(await screen.findByText("second saved answer")).toBeInTheDocument();
    expect(screen.queryByText("first saved answer")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /First saved chat/ }));
    expect(screen.getByText("first saved answer")).toBeInTheDocument();
    expect(screen.queryByText("second saved answer")).not.toBeInTheDocument();
    expect(apiMocks.saveChatConversation).not.toHaveBeenCalled();
  });

  it("displays delayed history when the selected conversation is clicked without toggling modes", async () => {
    const user = userEvent.setup();
    const now = Date.now() / 1000;
    const messages = [
      { role: "user", content: "Hey, how are you?" },
      { role: "assistant", content: "Your saved welcome answer" },
    ];
    let finishHistory!: (value: any) => void;
    apiMocks.chatConversations.mockResolvedValue([
      { id: "welcome", title: "Saved welcome", messages: 2, createdAt: now, updatedAt: now },
    ]);
    apiMocks.getChatConversation.mockReturnValue(new Promise((resolve) => { finishHistory = resolve; }));
    render(<App />);

    const conversation = await screen.findByRole("button", { name: /Saved welcome 2 messages/ });
    expect(screen.queryByText("Your saved welcome answer")).not.toBeInTheDocument();
    await user.click(conversation);
    await act(async () => { finishHistory({ id: "welcome", messages }); });

    expect(screen.getByText("Hey, how are you?")).toBeInTheDocument();
    expect(screen.getByText("Your saved welcome answer")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Workspace" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /Saved welcome 2 messages/ })).toBeInTheDocument();
    expect(apiMocks.saveChatConversation).not.toHaveBeenCalled();
  });

  it("shows delayed history in General and keeps it visible when returning to Workspace", async () => {
    const user = userEvent.setup();
    const now = Date.now() / 1000;
    let finishHistory!: (value: any) => void;
    apiMocks.chatConversations.mockResolvedValue([
      { id: "welcome", title: "Saved welcome", messages: 2, createdAt: now, updatedAt: now },
    ]);
    apiMocks.getChatConversation.mockReturnValue(new Promise((resolve) => { finishHistory = resolve; }));
    render(<App />);
    await screen.findByRole("button", { name: /Saved welcome 2 messages/ });

    await user.click(screen.getByRole("button", { name: "General" }));
    await act(async () => {
      finishHistory({ id: "welcome", messages: [
        { role: "user", content: "Saved question" },
        { role: "assistant", content: "Saved reply" },
      ] });
    });
    expect(screen.getByRole("heading", { name: "General Chat" })).toBeInTheDocument();
    expect(screen.getByText("Saved question")).toBeInTheDocument();
    expect(screen.getByText("Saved reply")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Workspace" }));
    expect(screen.getByRole("heading", { name: "AI Workspace" })).toBeInTheDocument();
    expect(screen.getByText("Saved question")).toBeInTheDocument();
    expect(screen.getByText("Saved reply")).toBeInTheDocument();
    expect(apiMocks.saveChatConversation).not.toHaveBeenCalled();
  });

  it("restores General mode and the selected chat after a full app remount", async () => {
    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByText("Conversations synced");
    await user.click(screen.getByRole("button", { name: "General" }));
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Remember my general chat{Enter}");
    await screen.findByText("General answer");
    const sessionId = apiMocks.generalChatStream.mock.calls[0][1];
    view.unmount();
    apiMocks.chatConversations.mockRejectedValue(new Error("Server unavailable"));
    render(<App />);

    expect(await screen.findByRole("button", { name: "General" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Workspace" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("heading", { name: "General Chat" })).toBeInTheDocument();
    expect(screen.getByText("Remember my general chat", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("General answer")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Continue in General{Enter}");
    await waitFor(() => expect(apiMocks.generalChatStream).toHaveBeenCalledTimes(2));
    expect(apiMocks.generalChatStream.mock.calls[1][1]).toBe(sessionId);
    expect(apiMocks.workspaceChat).not.toHaveBeenCalled();
  });

  it("moves a saved General conversation into local Workspace without losing history", async () => {
    const user = userEvent.setup();
    const view = render(<App />);
    await screen.findByText("Conversations synced");
    await user.click(screen.getByRole("button", { name: "General" }));
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Keep this conversation{Enter}");
    await screen.findByText("General answer");
    const sessionId = apiMocks.generalChatStream.mock.calls[0][1];
    view.unmount();
    apiMocks.localCapabilities.mockResolvedValue({ enabled: true });
    apiMocks.chatConversations.mockRejectedValue(new Error("Server unavailable"));
    render(<App />);
    await screen.findByText(/Qwen brings the right tools/);
    expect(screen.queryByRole("button", { name: "General" })).not.toBeInTheDocument();
    expect(screen.getByText("General answer")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Continue here{Enter}");
    await screen.findByText("Answer to Continue here");
    expect(apiMocks.workspaceChat).toHaveBeenCalledWith("Continue here", sessionId, expect.any(AbortSignal));
    expect(apiMocks.generalChatStream).toHaveBeenCalledTimes(1);
  });

  it("retains Workspace and General messages, the active conversation, and mode after leaving", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations synced");
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Hello workspace{Enter}");
    await screen.findByText("Answer to Hello workspace");
    const sessionId = apiMocks.workspaceChat.mock.calls[0][1];
    await user.click(screen.getByRole("button", { name: "General" }));
    expect(screen.getByText("Answer to Hello workspace")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Hello general{Enter}");
    await screen.findByText("General answer");
    await user.click(screen.getByRole("button", { name: "Show away" }));
    await user.click(screen.getByRole("button", { name: "Show chat" }));

    expect(screen.getByText("Answer to Hello workspace")).toBeInTheDocument();
    expect(screen.getByText("General answer")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Follow up{Enter}");
    await waitFor(() => expect(apiMocks.generalChatStream).toHaveBeenCalledTimes(2));
    expect(apiMocks.generalChatStream.mock.calls[1][1]).toBe(sessionId);
  });

  it("restores cached Workspace messages after a full remount while the server is unavailable", async () => {
    const user = userEvent.setup();
    apiMocks.saveChatConversation.mockRejectedValue(new Error("Offline"));
    const view = render(<App />);
    await screen.findByText("Conversations synced");
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Keep this message{Enter}");
    await screen.findByText("Answer to Keep this message");
    const sessionId = apiMocks.workspaceChat.mock.calls[0][1];
    view.unmount();
    apiMocks.chatConversations.mockRejectedValue(new Error("Offline"));
    render(<App />);

    expect(screen.getByText("Answer to Keep this message")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Continue{Enter}");
    await screen.findByText("Answer to Continue");
    expect(apiMocks.workspaceChat.mock.calls[1][1]).toBe(sessionId);
  });

  it("keeps Brain history and its session when navigating away and returning", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Show brain" }));
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Explain my notes{Enter}");
    await screen.findByText("Brain answer");
    const sessionId = apiMocks.ragAskStream.mock.calls[0][1];
    await user.click(screen.getByRole("button", { name: "Show away" }));
    await user.click(screen.getByRole("button", { name: "Show brain" }));

    expect(screen.getByText("Explain my notes")).toBeInTheDocument();
    expect(screen.getByText("Brain answer")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Explain more{Enter}");
    await waitFor(() => expect(apiMocks.ragAskStream).toHaveBeenCalledTimes(2));
    expect(apiMocks.ragAskStream.mock.calls[1][1]).toBe(sessionId);
  });

  it("retains separate BI histories and sessions for each dataset across navigation", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Show bi" }));
    await screen.findByText("Active dataset: sales");
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Show revenue{Enter}");
    await screen.findByText("sales analysis");
    const salesSession = apiMocks.biAsk.mock.calls[0][1];
    await user.click(screen.getByRole("button", { name: /^inventory 4 rows/ }));
    expect(screen.queryByText("sales analysis")).not.toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Show stock{Enter}");
    await screen.findByText("inventory analysis");
    const inventorySession = apiMocks.biAsk.mock.calls[1][1];
    expect(inventorySession).not.toBe(salesSession);
    await user.click(screen.getByRole("button", { name: "Show away" }));
    await user.click(screen.getByRole("button", { name: "Show bi" }));
    await screen.findByText("Active dataset: inventory");
    expect(screen.getByText("inventory analysis")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^sales 12 rows/ }));
    expect(screen.getByText("sales analysis")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "More revenue{Enter}");
    await waitFor(() => expect(apiMocks.biAsk).toHaveBeenCalledTimes(3));
    expect(apiMocks.biAsk.mock.calls[2][1]).toBe(salesSession);
    expect(apiMocks.biDatasets).toHaveBeenCalledTimes(2);
  });

  it("restores Brain and BI histories after remounting the whole app", async () => {
    const user = userEvent.setup();
    const view = render(<App />);
    await user.click(screen.getByRole("button", { name: "Show brain" }));
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Saved notes{Enter}");
    await screen.findByText("Brain answer");
    await user.click(screen.getByRole("button", { name: "Show bi" }));
    await screen.findByText("Active dataset: sales");
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Saved revenue{Enter}");
    await screen.findByText("sales analysis");
    const sessionId = apiMocks.biAsk.mock.calls[0][1];
    view.unmount();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Show brain" }));
    expect(screen.getByText("Brain answer")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show bi" }));
    await screen.findByText("Active dataset: sales");
    expect(screen.getByText("sales analysis")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Continue analysis{Enter}");
    await waitFor(() => expect(apiMocks.biAsk).toHaveBeenCalledTimes(2));
    expect(apiMocks.biAsk.mock.calls[1][1]).toBe(sessionId);
  });

  it("keeps cached chats isolated when the signed-in account changes", async () => {
    const user = userEvent.setup();
    const view = render(<ChatStateProvider><BrainPage /></ChatStateProvider>);
    await user.type(screen.getByRole("textbox", { name: "Message" }), "Private notes{Enter}");
    await screen.findByText("Brain answer");
    auth.owner = "owner-b";
    view.rerender(<ChatStateProvider><BrainPage /></ChatStateProvider>);
    expect(screen.queryByText("Private notes")).not.toBeInTheDocument();
    expect(screen.queryByText("Brain answer")).not.toBeInTheDocument();
    auth.owner = "owner-a";
    view.rerender(<ChatStateProvider><BrainPage /></ChatStateProvider>);
    expect(screen.getByText("Private notes")).toBeInTheDocument();
    expect(screen.getByText("Brain answer")).toBeInTheDocument();
  });

  it("keeps pages visible while authentication resolves and waits before enabling chat", () => {
    auth.loading = true;
    const view = render(<ChatStateProvider><BrainPage /><div>Sign in form</div></ChatStateProvider>);
    expect(screen.getByText("Sign in form")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled();
    auth.loading = false;
    view.rerender(<ChatStateProvider><BrainPage /><div>Sign in form</div></ChatStateProvider>);
    expect(screen.getByRole("textbox", { name: "Message" })).toBeEnabled();
  });

  it("serializes delayed saves so a completed answer cannot be replaced by an older snapshot", async () => {
    vi.useFakeTimers();
    try {
      let finishFirstSave!: (value: {}) => void;
      apiMocks.saveChatConversation.mockImplementationOnce(() => new Promise((resolve) => { finishFirstSave = resolve; }));
      render(<App />);
      await act(async () => {});
      const send = async (text: string) => {
        await act(async () => {
          fireEvent.change(screen.getByRole("textbox", { name: "Message" }), { target: { value: text } });
          fireEvent.keyDown(screen.getByRole("textbox", { name: "Message" }), { key: "Enter" });
        });
      };
      await send("First prompt");
      await act(async () => { vi.advanceTimersByTime(400); });
      expect(apiMocks.saveChatConversation).toHaveBeenCalledTimes(1);
      await send("Second prompt");
      await act(async () => { vi.advanceTimersByTime(400); });
      expect(apiMocks.saveChatConversation).toHaveBeenCalledTimes(1);
      await act(async () => { finishFirstSave({}); });

      expect(apiMocks.saveChatConversation).toHaveBeenCalledTimes(2);
      expect(apiMocks.saveChatConversation.mock.calls[0][2]).toHaveLength(2);
      expect(apiMocks.saveChatConversation.mock.calls[1][2]).toHaveLength(4);
      expect(apiMocks.saveChatConversation.mock.calls[1][2][3].content).toBe("Answer to Second prompt");
    } finally {
      vi.useRealTimers();
    }
  });
});
