# ATMTA suite architecture

How the procurement and construction domains are laid out, why each boundary
falls where it does, and what a maintainer needs to know before moving
anything.

This describes the state after the V2 extraction programme (commits `84caf41`
through `c20fe57`). It is a map, not a plan: everything here is measured from
the tree it documents.

---

## 1. The shape

Two monoliths were decomposed into capability modules, each owning the models
behind one idea. A module may depend on the modules below it and never on the
ones above.

### Construction

| Depth | Module | Models | Owns |
|---:|---|---:|---|
| 3 | `atmta_construction_core` | 4 | WBS, cost codes, analytic wiring, company accounts |
| 4 | `atmta_construction_contract` | 2 | contractors, contract packages |
| 5 | `atmta_construction_change` | 6 | change events, change orders |
| 6 | `atmta_construction_documents` | 10 | drawings, submittals, transmittals, RFIs |
| 7 | `atmta_construction_quality` | 10 | ITPs, inspections, observations, NCRs |
| 8 | `atmta_construction_claims` | 8 | delay events, notices, EOTs, claims |
| 9 | `atmta_construction_site` | 14 | BOQ, milestones, tasks, daily reports, labour, cost lines |
| 10 | `atmta_construction_certification` | 8 | payment certificates, retention, advances, owner billing |
| 11 | `atmta_construction_cost` | 21 | budget, commitment, forecast, cost reporting, risk |
| 12 | `real_estate_construction` | 5 | the application layer (see §4) |
| 13 | `atmta_construction_app` | 0 | navigation |

`real_estate_construction` began with **88 owned models**. It now owns 5.

### Procurement

| Depth | Module | Models |
|---:|---|---:|
| 1 | `atmta_procurement_core` | 0 |
| 2 | `atmta_procurement_request` | 7 |
| 2 | `atmta_procurement_vendor` | 16 |
| 3 | `atmta_procurement_control` | 9 |
| 3 | `atmta_procurement_sourcing` | 10 |
| 4 | `atmta_procurement_evaluation` | 14 |
| 5 | `atmta_procurement_award` | 3 |
| 5 | `atmta_procurement_receipt` | 2 |
| 6 | `atmta_procurement_purchase` | 0 |
| 7 | `real_estate_procurement` | 0 |
| 8 | `atmta_procurement_app` | 0 |

**Dependency cycles across the whole suite: 0.**

---

## 2. What decided each boundary

The ordering was not chosen; it was measured. Two rules did nearly all the
work.

**A Many2one cannot point upward.** A field's comodel must be defined at or
below the module declaring it. This is why the commercial spine had to move
before anything else in construction: documents, quality and site operations
all point at contractors and contract packages with Many2one fields, and no
seam can substitute for a comodel. It is also why the boundaries could be
computed in advance, by taking the strongly-connected components of the
Many2one graph.

**Behaviour can cross a boundary; data cannot.** Where a lower module needed
an answer only a higher one could give, the lower module declares a seam and
the higher one overrides it. Sixteen of these exist (§3).

The remaining core stopped being one mass surprisingly early. By Wave 18 it
was a near-DAG: 25 strongly-connected components across 26 files, with exactly
one mutual pair.

**The one irreducible pair** is payment certificates and retention. A
certificate withholds retention; a retention release reads the certificates it
came from. They live together in `atmta_construction_certification` rather than
being pretended apart.

---

## 3. The seam catalogue

A seam is a method a lower module declares and answers neutrally, so it is
truthful on its own, and a higher module overrides with the real arithmetic.
The arithmetic never changed during extraction; only the module stating it.

| Seam | Declared in | Answered by |
|---|---|---|
| `_variation_amount` | contract | `contract_package_construction.py` |
| `_eot_day_totals` | contract | `contract_package_construction.py` |
| `_package_commitment` | contract | `contract_package_construction.py`, delegating to `atmta_construction_cost/commitment.py` |
| `_consumed_value` | contract | `contract_package_construction.py` |
| `_authority_rule` | change | `change_implementation.py` |
| `_check_authority` | change | `change_implementation.py` |
| `_apply_budget_impact` | change | `change_implementation.py` |
| `_apply_commitment_impact` | change | `change_implementation.py` |
| `_apply_revenue_impact` | change | `change_implementation.py` |
| `_convert_forecast_anticipations` | change | `change_implementation.py` |
| `_already_implemented` | change | `change_implementation.py` |
| `_reason_dispatch` | quality | `quality_reason_dispatch.py` |
| `_record_ref_selection` | claims | `delay_daily_link.py` |
| `_daily_record_count` | claims | `delay_daily_link.py` |
| `_daily_delay_chronology` | claims | `delay_daily_link.py` |
| `_certified_totals` | site | `atmta_construction_certification/boq_certification.py` |

`_already_implemented` is the subtle one. Implementing a change order is
idempotent *because* its linked budget and commitment records exist, and those
records are invisible to the module that runs the workflow. The guard had to
become a seam of its own or the guarantee would have been lost silently.

---

## 4. Why `real_estate_construction` still exists

It is the application layer, not a remnant. It holds four things, and each is
there for a reason that survives scrutiny.

**Five read-only models.** The control tower, the dashboard, the data-control
exceptions and the integrity audit consume every module beneath them and add
no figure of their own. The tower has zero writes and zero numeric fields; its
own header says it never adds a sixth opinion. Plus one subcontract PO wizard.

**Ten cross-layer extension files.** Each declares a relation whose two ends
sit in modules that cannot depend on each other. `contract_package_construction.py`
adds variations and EOTs to a package at depth 4 from models at depths 8 and 11.
`delay_daily_link.py` joins delay events (claims, 8) to daily records (site, 9)
— and site depends on claims, so claims can never declare it. Only a module
above both can. That is this one. `quality_reason_dispatch.py` is the same
shape: quality owns the reason prompt, but amending a daily report is a site
concern, and site sits above quality.

**Eight view files.** Three render fields this module declares (`change`,
`contractor`, `project`); three span two capabilities (`budget`, `claims`,
`procurement coding`); plus the menu tree and the dashboard. Eleven view files
moved to their owners, nine in Wave 21 and two in Wave 23.

**The test suite: 33 files, 563 methods.** Measured: `common.py` is 617 lines
with 45 helpers touching 30 models, 31 of 33 files import it, and 249 of 563
methods (44%) live in files touching three or more capabilities. This is an
integration gate over the whole domain, and this module is the only one
depending on all ten capabilities. Splitting it would fragment the gate that
protected every wave. It stays whole deliberately.

---

## 5. Rules learned the hard way

Each of these cost a failed run or a near miss.

**A hook fires once, at install; only a migration runs on an upgrade.** Wave 21
moved screens with a `pre_init_hook`. Every total matched and all 563 tests
passed — and the views had been deleted and recreated with new database ids,
which would have orphaned any inherited view or saved filter layered on them.
Caught by comparing a database id across the upgrade, not a count. If a module
may already be installed, it needs a migration.

Wave 23 is the counter-example that shows the rule works. All three modules it
touched were already installable, so migrations were written from the start and
verified by id: three views kept 1653, 1670 and 1672 across the upgrade while
changing owner. The rule cost one wave to learn and nothing afterwards.

**`_process_end` skips `noupdate` records.** Sequences are `noupdate`, so
nothing hands them over automatically. Every wave that moved a sequence named
it explicitly. Wave 6 lost four sequences to this.

**Odoo does not upgrade a dependency when a dependent upgrades.** Hand-over
belongs in the receiving module, which loads first, not in the one being
emptied.

**Exclude the fields that stay.** A blanket field sweep takes the seam fields
too, and the losing module's next reload drops the columns behind them. Waves
13 to 18 each excluded a field or two by name. Wave 20 was the first with no
exclusions, because by then every other end was below.

**Never write a file before reading it.** `open(p, 'w')` truncates. Twice a
one-liner with the read inlined in the comprehension emptied a security file.
Both were caught by the standing zero-byte check before any test ran.

**A test that asserts an identifier is asserting the wrong thing.** Wave 17
found a security test asserting a literal group XML ID. The rebinding was
correct; the test was rewritten to prove the restriction still bites — that a
legacy group holder can read the field and a site engineer cannot — rather
than to match a string.

---

## 6. Verification standard

Every wave was held to the same gate, and none of it was assumed:

- the frozen suite: **563 tests, 0 failed, 0 errors**, before and after;
- record totals identical across baseline, fresh install and `-u all` upgrade;
- fresh install and upgrade converging on identical ownership;
- a seeded database carrying real records upgraded, and the records checked by
  value afterwards — a 4.8M contract package, a 240,000 change order, an 875,000
  claim, a 270,000 certificate, a 600,000 BOQ line;
- the committed tree hash compared against the tested tree hash before each
  commit.

---

## 7. If you continue

Wave 23 did the first item on the original list: documents and quality now
depend on change, `change_event_id` sits beside the models it belongs to, and
two more view files followed it down.

What is left:

1. **Split the three mixed view files** — `budget`, `claims` and
   `procurement coding` each render two capabilities' models. Splitting lets
   each half sit with its owner, at the cost of inherited-view indirection
   where there is none today.
2. **Leave the rest.** The five models, the ten extension files and the
   integration suite are where they belong. Moving them would trade a correct
   architecture for a tidier file listing.
