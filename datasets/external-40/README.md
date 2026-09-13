# External-40

External-40 is a development/regression cohort, not an untouched generalization test. Of 43 attempted human or natural-LLM sources, 41 passed mechanical admission. One mechanically admitted human source was then excluded because the frozen prior-comparison record marked it as an exact canonical-AST duplicate. The remaining 40 correctness-eligible sources comprise 24 human and 16 LLM sources; their scored oracle split is 32 safe and eight unsafe.

Two initial false `violated` calls exposed a pose-binding defect. The paper-facing outcomes and the final revalidation use the corrected implementation. The same 40 frozen sources were retained; the correction did not create a new population.

`admission_funnel.json` separates the two mechanical parser rejections from the one post-admission prior-duplicate exclusion. `programs/` contains all 40 scored sources.
