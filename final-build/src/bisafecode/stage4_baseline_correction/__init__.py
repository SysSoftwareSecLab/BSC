"""Post-result RQ1 baseline fairness correction.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

The package reuses the sealed EXP-S4-002 sources but writes only to the
separate EXP-S4-006 Raw/Derived roots after exact-commit authorization.
"""

EXPERIMENT_ID = "EXP-S4-006_RQ1_BASELINE_CORRECTION"
CONTRACT_RELATIVE = f"03_experiments/contracts/{EXPERIMENT_ID}"
SOURCE_EXPERIMENT = "EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS"
SOURCE_RAW_RELATIVE = f"03_experiments/raw/{SOURCE_EXPERIMENT}"
RAW_RELATIVE = f"03_experiments/raw/{EXPERIMENT_ID}"
DERIVED_RELATIVE = f"03_experiments/derived/{EXPERIMENT_ID}"

METHOD_RANDOM = "random_timed_schedule_testing_v2"
METHOD_LLM = "gpt55_full_context_judge_v2"
METHOD_IDS = (METHOD_RANDOM, METHOD_LLM)

