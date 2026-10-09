# LangChain / LangGraph local experiment

This optional prototype uses the installed `qwen3:8b` through Ollama. LangChain
binds typed tools and returns native tool calls; LangGraph executes a bounded
loop and persists clarification pauses in SQLite. Run it from the repository
root. The application does not import this experiment.

See [RESULTS.md](RESULTS.md) for the recorded local test results from October 8, 2026.

The tools are a calculator, `ask_user`, and a **simulated** image/video model
catalogue. Its `demo-image-worker` and `demo-video-worker` are test fixtures.
They do not describe installed generation models or execute GPU generation.

## Setup and offline tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r experiments/agent_workflow/requirements.txt
.\.venv\Scripts\python.exe -m pytest experiments/agent_workflow/test_workflow.py -q
```

The shared frameworks now appear in the application's requirements for
[local agentic RAG](../../docs/agentic-rag.md). This standalone experiment also
uses an optional SQLite checkpointer. Its tests skip during a repository test run if the
optional SQLite adapter is absent. The offline tests use scripted model responses to verify graph
mechanics: native tool results, multiple steps, clarification before sibling
tools, SQLite reopening, thread isolation, invalid arguments, and call/input
budgets. Actual Qwen behavior needs a live test with Ollama running locally.

## Live Qwen tests

Arithmetic:

```powershell
.\.venv\Scripts\python.exe -m experiments.agent_workflow.run --prompt "For a 5-second video at 24 frames per second, use the calculator tool to work out how many frames are needed."
```

Expect a native `multiply` call with 5 and 24, its result of 120, and a final
answer based on that result.

Clarification:

```powershell
.\.venv\Scripts\python.exe -m experiments.agent_workflow.run --prompt "I want an image of a cat. Help me plan it."
```

Expect `status: paused`, an `ask_user` call, and an interruption asking for
style and size. Copy the generated `thread` value into this command. Starting
a new Python process demonstrates recovery from the saved SQLite checkpoint:

```powershell
.\.venv\Scripts\python.exe -m experiments.agent_workflow.run --thread "COPY-THREAD-ID-HERE" --resume "Watercolor style, square 512 by 512. Keep the cat as the subject."
```

Expect the earlier cat request and new requirements to remain in the task,
followed by a catalogue lookup and a plan retaining those requirements. The
tested final answer did not select the fixture's model ID; see the results.
Resume the same database and thread; a completed task cannot be resumed. Reusing a thread
for a new prompt is rejected to avoid accidentally mixing tasks.

Multiple tools and an unavailable worker:

```powershell
.\.venv\Scripts\python.exe -m experiments.agent_workflow.run --prompt "Plan a watercolor mountain video, 5 seconds, 16:9, at 24 frames per second. Check available video models and calculate the number of frames."
```

Expect catalogue and calculator calls, 120 frames, and an explanation that the
simulated video worker is unavailable.

## Defaults and limits

- Ollama endpoint: `http://127.0.0.1:11434`; override with `--base-url`.
- Qwen context: 4,096 tokens, thinking disabled, temperature 0, output up to
  512 tokens per model call. Use `--model` / `--context` to experiment.
- Task budgets: six model calls, eight tool calls, and a coarse 10,000-character
  input guard including message metadata and tool schemas. This guard estimates
  size; it does not tokenize, summarize history, or provide long-term memory.
- Checkpoints: `data/cache/agent_workflow/checkpoints.sqlite`, ignored by Git.
  Pauses survive process exit, and call counters survive resume. SQLite here
  supports a local experiment; it has no application user-authentication layer.
- Add `--output data/cache/agent_workflow/result.json` to save a full transcript.
  LangSmith tracing is disabled by the runner.

Before connecting generation tools, validate required task fields in the
application and reject an incomplete generation request. Prompt instructions
alone can miss a clarification. Validate a structured selected model ID against
the registry as well. Keep expensive or side-effecting operations
after the clarification checkpoint, since a paused node restarts on resume.

API references: [Ollama integration](https://docs.langchain.com/oss/python/integrations/chat/ollama),
[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts),
[checkpoint persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
