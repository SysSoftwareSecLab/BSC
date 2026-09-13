# Release Selection Policy

This artifact is designed to make the paper's reported work auditable while
remaining compact, double-anonymous, and legally reviewable. Selection is based
on evidentiary value, not on whether a record is favorable to the method.

## Included when relevant to a paper claim

- Frozen programs, contracts, trajectories, labels, oracle outputs, and hashes.
- Per-case or per-slot final results, including failures, `UNK`, `INV`, and
  blocked decisions in a reported denominator.
- Corrections that change the interpretation of an experiment, together with
  the retained pre-correction boundary and the final recomputation result.
- The two External-25 unsafe-to-`UNK` outcomes, the C04/C05 post-run telemetry
  gaps, and the incomplete historical collision dispatch chain.
- Experiment-relevant prompts, task cards, raw generator outputs or failures,
  admission and deduplication decisions, dates or times where actually recorded,
  client versions, and content hashes, after anonymity review.

## Excluded unless separately approved

- Duplicate giant traces, warm-up payloads, and full internal engineering
  attempts when compact per-slot records and hashes preserve the same audit.
- Mentor correspondence, private chats, internal planning logs, and unrelated
  working notes.
- Git history containing identities, account details, credentials, personal
  paths, captured system libraries, and virtual environments.
- Unrelated obsolete versions and records that do not support or delimit a
  paper claim.
- Third-party models, meshes, binaries, music, voices, or media without confirmed
  redistribution rights.
- Hardware photos or video until scientific wording, anonymity, audio, and media
  rights have all passed a separate release review.

## Non-negotiable rule

No reported failure, unknown result, blocked release, correction, denominator,
or claim-changing limitation may be hidden merely because it is unfavorable.
Omitting redundant bytes is allowed; changing the scientific story is not.
