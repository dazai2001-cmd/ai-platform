# Local test results — October 8, 2026

Tested with Python 3.11.5, LangChain 1.2.10, LangChain Core 1.4.0,
LangGraph 1.0.10, Ollama adapter 1.1.0, and SQLite checkpointer 3.1.1.
The optional adapters were installed into the project's existing `.venv`.

The real model was the already installed `qwen3:8b`, model ID `500a1f067a9f`,
through local Ollama 0.32.14. Its context was explicitly set to 4,096 tokens;
`ollama ps` confirmed that size and a 61% / 39% CPU / GPU split on this laptop's
4 GB RTX 3050. Thinking was disabled. No model weights were downloaded.

## Offline checks

`python -m pytest experiments/agent_workflow/test_workflow.py -q`: **11 passed**.
These use scripted responses to check graph behavior, argument validation,
bounded loops, checkpoint reopening, and thread isolation.

## Real Qwen checks

Live transcripts were saved under `data/cache/agent_workflow/` and checked for
native tool-call names, matching tool results, expected answers, saved task
identity, and the absence of malformed tool calls.

| Check | Observed result |
| --- | --- |
| Arithmetic | Called `multiply(5, 24)`, received 120, answered 120 frames. |
| Clarification | Called `ask_user` for the cat image's style and size. LangGraph paused before other tools ran. |
| Recovery in a new process | Reopened SQLite with the same thread, accepted the answer, retained the original cat request, and called the image catalogue. The final answer retained watercolor and 512 × 512. |
| Two tools in one task | Called the video catalogue and calculator, returned 120 frames, and reported the simulated video worker's unavailability. |

The initial broad system instructions missed the clarification tool: Qwen
looked up the catalogue and asked a vague question in its final text. Explicit
required fields and an instruction to call `ask_user` before the catalogue
produced the observed durable pause on retest.

The resumed image answer also did **not** choose `demo-image-worker`, despite
the fixture marking it available. It explained that actual generation was
unavailable. Native tool execution and state recovery worked; reliable final
model selection still needs a typed plan validated against the registry.

Reported Ollama durations were approximately 19.6 seconds for arithmetic
(including 12.8 seconds of initial model loading), 5.5 seconds for the
clarification question, 11.7 seconds for the resumed stage, and 15.8 seconds
for the two-tool task. These are sums of model-call durations for individual
samples, excluding Python startup and user wait time. They are not a load
benchmark. The largest observed prompt was 671 tokens, so long-context
performance and summarization remain untested.

## Integration decision

LangChain's Ollama tool adapter and LangGraph's local checkpoint/resume loop
are usable foundations on this machine. Before adding them to application
chat, define a typed task brief, validate required fields and the selected
model ID in application code, and map authenticated users to task IDs.

The catalogue contains simulated workers. Image/video generation, frontend
chat integration, production persistence, and long-term memory are outside
this experiment.
