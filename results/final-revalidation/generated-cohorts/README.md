# Generated-program cohort results

This directory is a compact, anonymous projection of two generated-program
experiments. It keeps every unique admitted result or fixed controlled slot,
including `unknown`, `invalid`, and failed results. It does not contain private
workspace paths, user/account identifiers, host names, task threads, or the
large raw execution trees.

The projection supports an exact recount of the reported denominators and
method/oracle outcome tables. The released generation records and program files
support a public byte-level join to the projected result rows. Private raw
method, oracle, forensic, seal, and acceptance files are outside this selective
review disclosure and are recorded only as `WITHHELD_NOT_PUBLICLY_BOUND`; no
digest of an undistributed private file is published. This is a count-level
evidence package, not a standalone replay of generation or verifier execution.

## `no-repair/`

The one-shot no-repair experiment made 36 generation calls under a frozen
12-card by three-family design. There was no repair, replacement, or
failed-sample substitution. The funnel was 36 generated, 30 mechanically
admitted including duplicates, 21
unique admitted, and 20 oracle-valid scored programs. The one oracle-invalid
unique program remains in the 21-row file and is explicitly unscored.

The local projection builder checks the 21 retained private method records and
21 retained private oracle records against their private seal manifests, then
checks those seals against the paper-facing acceptance and numeric-lock records.
Those checks are a local release-preparation step, not a public cryptographic
claim. The private records, seals, receipts, and their digests are omitted from
this package; `summary.json` states that boundary directly.

For the 20 scored rows, method outcomes are 4
`verified-within-bounds`, 10 `violated`, and 6 `unknown`. All 16 oracle-unsafe
programs were withheld; none was released. The all-21 raw method count has one
additional `violated` result belonging to the retained oracle-invalid row.

This finite, deliberately balanced cohort is evidence about the frozen task
cards and model families. It is not a defect-prevalence estimate, model ranking,
population reliability estimate, or unbounded safety result.

## `controlled-extension-q4/`

The controlled extension retains the same 24 admitted, sealed programs and the
first method output for every slot. The first method was not rerun and no source
or result was replaced. A post-discovery corrected independent oracle was then
run once on those same sources.

The executed prompt bytes contain an inherited manual line stating 1--16 API
calls and a Q4 task-card target of 12--24 source API calls. The Q4 admission
worker used its separately frozen lexical range of 1--24; the task-card count
was a generation target, not an exclusion rule. Three retained sources contain
18 or 19 calls. `Q4-SLOT-10` contains 10 calls and is the one explicit target
miss: 23/24 sources met the 12--24 target, while all 24 met their structural
target. The conflicting prompt text and the target miss are retained rather
than silently normalized or excluded.

The corrected-oracle outcomes are 13 safe, 8 unsafe, 2 invalid, and one retained
worker failure with a null accepted verdict. The 21 safe/unsafe scored rows pair
with 13 `verified-within-bounds`, 4 `violated`, and 4 `unknown` method outputs.
All eight corrected-oracle-unsafe programs were withheld; none was released.

Two correction records are intentionally prominent:

- `Q4-SLOT-17`: the prior oracle label was safe, but the corrected-oracle run
  had an RSS-monitoring failure. It remains in the fixed denominator with a null
  accepted verdict and is not scored. A complete raw worker output retained in
  the authors' private source record reported unsafe, but that forensic output
  was not promoted into the scored result. The public projection retains its
  disposition and private-evidence status, not the raw worker file or a digest
  of that undistributed file.
- `Q4-SLOT-18`: the prior top label was invalid. The corrected oracle reports
  unsafe because an earlier valid-prefix handover violation precedes a later
  invalid trajectory-binding suffix. The frozen first method remains
  `violated`.

This is explicitly a post-discovery oracle correction on the same 24 programs,
not an untouched holdout or a new cohort. It is software-model evidence, not a
physical-clearance certificate or a population-level guarantee.

## Recount and integrity check

From the artifact root, run:

```bash
python3 results/final-revalidation/tools/recount_generated_cohorts.py
```

The script uses only the Python standard library. It verifies the internal
checksums; joins the released generation records, output JSON, and program bytes
to both result projections; recounts fixed denominators and Boolean target
flags; and checks the release rule, outcome counts, process exits, retained
failure, correction records, and disclosure boundary. Public verification is
limited to files included in the package; it does not claim to byte-verify
omitted private records.

`SHA256SUMS` covers this README and every projected result file. The checksum
manifest itself is intentionally not self-listed.
