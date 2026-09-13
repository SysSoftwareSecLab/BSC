# Provider controls not recorded

The following provider-resolved generation metadata could not be recovered and must not be inferred from client-side files:

- immutable provider snapshot/build identifier;
- temperature;
- sampling seed;
- top-p or penalty settings;
- model output-token limit;
- hidden provider-side service or system context.

The JSON-schema character limits in this directory are validation constraints, not model token limits. Model selector, client version, reasoning effort, prompts, outputs, dates, hashes, and inclusion/exclusion decisions are retained where recorded.
