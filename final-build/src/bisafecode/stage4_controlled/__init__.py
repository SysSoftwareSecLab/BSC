"""Freeze-only infrastructure for EXP-S4-002 controlled unseen programs.

FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Importing this package cannot generate a locked set or execute a verifier,
oracle, baseline, ROS component, solver, or robot interface.
"""

EXPERIMENT_ID = "EXP-S4-002_CONTROLLED_UNSEEN_PROGRAM_CORRECTNESS"
FREEZE_STATUS = "FREEZE_ONLY_NOT_FORMAL_STAGE4_EVIDENCE"
LOCKED_RAW_STATUS = "LOCKED_RAW_PENDING_BLIND_EVALUATION"
FORMAL_RAW_STATUS = "FORMAL_RAW_PENDING_MAC_REVIEW"
DERIVED_STATUS = "DERIVED_PENDING_MAC_REVIEW"
# Compatibility name for generation/admission records.  Later phases must use
# their phase-specific status and may not reuse this alias.
FORMAL_DATA_STATUS = LOCKED_RAW_STATUS
GENERATOR_VERSION = "bisafecode.stage4.controlled.generator/v4-stratified-matched-collision"
GRAMMAR_VERSION = "bisafecode.stage4.controlled.grammar/v1"
PROGRAM_SCHEMA_VERSION = "bisafecode.stage4.controlled.program-record/v1"
SPLIT = "controlled_unseen_test"
STOP_MARKER = "READY_FOR_STAGE4_CODEX_CLI_BASELINE_PREFLIGHT_REVIEW"
