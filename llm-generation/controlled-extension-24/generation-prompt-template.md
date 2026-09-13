# Q4 generation prompt draft

Status: `DRAFT / DO NOT DISPATCH BEFORE FINAL FREEZE`

The formal collector will replace the two delimited placeholders byte-for-byte and hash the resulting prompt before dispatch.

---

You are writing one robot coordination program in the restricted BiSafeCode authoring language.

Follow the public authoring manual except for the stricter Q4 rule below. Use only identifiers, finite inputs, trajectory hashes, literal durations, bounded loops, and branching permitted by that manual. The program will be analyzed offline and will never control a robot.

For this controlled Q4 route, `parallel(...)`, `barrier(...)`, parallel branch functions, and every other parallel construct are prohibited even if the public manual describes them. Use only the requested straight-line, single-if/else, single-bounded-loop, or one-if/else-plus-one-bounded-loop structure. The independent Q4 oracle enumerates the four `mode` × `ready` input valuations; the no-parallel restriction makes those four traces the complete path universe for this prospective evaluation.

Do not import modules, access files or the network, use dynamic Python features, invent API calls or identifiers, or include executable top-level statements. Do not discuss, predict, or optimize for verifier results. Do not intentionally create or avoid any known safety defect. Implement the assigned task naturally and directly.

Return the answer directly. Do not call or use tools, apps, skills, plugins, web or browser search, computer control, image generation, terminals, workspace files, project memory, or other tasks/conversations. The manual and task card below are the complete research inputs for this generation slot.

The requested 12–24 source actions and structural family are generation targets. Do not pad the program with no-op behavior merely to hit a count. If the task cannot be represented exactly, produce your best manual-compliant program and explain the limitation briefly; do not change the task into a different one.

Return exactly one JSON object matching the supplied output schema. `program_source` must contain the complete Python source with one parameterless `task()` entry point and no helper or parallel branch functions. `brief_author_note` must be concise and must not state an expected safety verdict.

<PUBLIC_AUTHORING_MANUAL>
{{PUBLIC_AUTHORING_MANUAL}}
</PUBLIC_AUTHORING_MANUAL>

<TASK_CARD>
{{TASK_CARD_JSON}}
</TASK_CARD>

---

Formal freeze requirements outside the model prompt:

- hash the manual, task-card file, template, rendered prompt, and JSON schema;
- pass the schema through the client's native structured-output option;
- use one frozen model selector and reasoning effort for all 24 cards;
- do not expose oracle labels, method outputs, prior failure labels, or defect templates to the generator;
- do not regenerate malformed, duplicate, unsupported, or structure-miss outputs.
