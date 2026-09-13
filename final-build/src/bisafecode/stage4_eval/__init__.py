"""Prepare-only RQ2/RQ3 and Raw-to-Derived evaluation tools.

PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE

Importing this package does not read a locked dataset, call an external API,
run an oracle, or create Raw/Derived/Paper outputs.
"""

PREP_STATUS = "PREP_ONLY_NOT_FORMAL_STAGE4_EVIDENCE"

RQ2_VARIANTS = (
    "bisafecode_full",
    "ablation_non_timed",
    "ablation_separated_state_property",
    "ablation_no_attached_object_geometry",
)
