# External-40 LLM authoring slice

This directory retains every one of the 18 natural-LLM authoring attempts used while constructing External-40. `generation-records.jsonl` joins each attempt to its mechanical-admission outcome. Two completed outputs were not correctness-eligible; they are retained rather than hidden. The remaining benchmark sources were independently authored by humans and are documented under `datasets/`.

The model selector was `gpt-5.6-sol`, reasoning effort was `high`, and the recorded client was `codex-cli 0.148.0-alpha.9`. The generator received only the frozen prompt, public manual, and one task card; no method or oracle was run during generation. Transport transcripts, task/thread identifiers, network tracing values, complete stderr, private repository references, and workstation paths were deliberately removed because they add no scientific input or outcome. Program outputs and status/admission evidence remain complete.
