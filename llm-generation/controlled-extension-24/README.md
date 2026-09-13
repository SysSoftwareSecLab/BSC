# Controlled extension: 24 one-attempt generations

This directory retains the actual rendered prompt and first output for every one of the 24 fixed slots. Each slot had a maximum of one attempt; no replacement was allowed or performed. All 24 generation processes completed and all 24 sources entered the fixed-source cohort. Later oracle qualification and correction-aware analysis are separate from generation and are documented under `oracles/` and `results/`.

The model selector was `gpt-5.5`, reasoning effort was `high`, and the recorded client was `codex-cli 0.153.1`. The template and task-card documents retain historical pre-dispatch status text because the executed prompts were derived from those exact bytes; the actual dispatched inputs are the files in `rendered-prompts/`. Full event streams, task/thread identifiers, network tracing values, command lines, temporary/application/workstation paths, and stderr were removed. Compact process and transport counts are retained in `generation-records.jsonl`.

The rendered prompts also retain a real specification mismatch: an inherited
manual line says 1--16 API calls, while the Q4 task cards target 12--24 source
API calls. The frozen Q4 admission worker used a separate 1--24 lexical
admission range. Thus 18/19-call sources were admitted, and the task-card lower
bound was not an exclusion rule. `Q4-SLOT-10` has 10 source actions; it remains
in the fixed 24 as the sole action-count target miss. No prompt text, source, or
slot was repaired or replaced after this was observed.
