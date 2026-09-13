"""EXP-S4-009 rare-schedule challenge support.

The challenge is deliberately mechanistic rather than population-estimating:
it measures how a fixed random schedule budget behaves as the exact exposure
probability of a reachable resource race decreases.
"""

EXPERIMENT_ID = "EXP-S4-009_RQ1_RARE_SCHEDULE_CHALLENGE"
FREEZE_STATUS = "FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE"
RAW_STATUS = "FORMAL_RAW_PENDING_MAC_REVIEW"
DERIVED_STATUS = "DERIVED_PENDING_MAC_REVIEW"

METHOD_FULL = "bisafecode_full"
METHOD_RANDOM = "independent_random_schedule_dynamic_monitor_v4"

BUDGET_PREFIXES = (1, 5, 10, 25, 50, 100)

CONTRACT_RELATIVE = (
    "03_experiments/contracts/"
    "EXP-S4-009_RQ1_RARE_SCHEDULE_CHALLENGE_FREEZE_ONLY"
)
RAW_RELATIVE = "03_experiments/raw/EXP-S4-009_RQ1_RARE_SCHEDULE_CHALLENGE"
DERIVED_RELATIVE = "03_experiments/derived/EXP-S4-009_RQ1_RARE_SCHEDULE_CHALLENGE"
ASSET_RELATIVE = (
    "03_experiments/contracts/"
    "EXP-S4-002_CONTROLLED_UNSEEN_FORMAL_FREEZE/assets"
)
