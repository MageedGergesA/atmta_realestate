# `real_estate_checks` 0.2 — PDC Treasury & Cheque Collection Engine

**Implementation report**
**Date:** 2026-08-05
**Version:** `0.1` → `0.2`
**Companion document:** [`PHASE_0_AUDIT_REPORT.md`](PHASE_0_AUDIT_REPORT.md)

---

## 1. Audit findings

The full audit is in the companion document. These are the findings that
actually drove the design, in order of severity.

### 1.1 The headline feature had never worked on Odoo 18

`_try_reconcile` (0.1, `check.py:264`) dereferenced `payment.line_ids`:

```python
receivable_lines = (invoice.line_ids | payment.line_ids).filtered(...)
```

Odoo 17 delegated `line_ids` through `_inherits = {'account.move': 'move_id'}`.
**Odoo 18 removed the delegation.** Verified against the running registry:

```
account.payment  has line_ids: False   _inherits: {}
```

So the call raised `AttributeError` — *after* posting a payment and *before*
assigning `check.payment_id` — and rolled the whole transaction back. Net
effect: **`action_mark_cleared()` failed outright for any cheque whose
obligation had a posted invoice**, and `action_clear_all()` on a deposit slip
failed if a single cheque in the batch was invoiced. Clearing "worked" only for
cheques with no invoice, i.e. precisely the ones where reconciliation was never
going to matter.

Asserted so it cannot regress:
`test_accounting.py::test_account_payment_has_no_line_ids_in_odoo_18`.

### 1.2 `cleared` meant "a user pressed a button"

`action_mark_cleared` set `state = 'cleared'` unconditionally after creating a
payment. No bank statement, no reconciliation, no `is_matched` — nothing about
the bank was consulted anywhere in the module. Between deposit and that button
press there was **no accounting artefact at all**, so a portfolio of cheques at
the bank was invisible to Accounting.

### 1.3 Zero record rules

`security.xml` defined three groups and **no `ir.rule` records**. Every user who
could read a cheque read every cheque in every company — including the drawer's
bank account number. By comparison `real_estate_developer` ships 19 rules.

### 1.4 Every permission was a view attribute

`action_mark_cleared`, `action_clear_all`, `action_issue_penalty_invoice` were
gated by `groups=` on `<button>` elements and by nothing on the server. Worse,
`group_checks_user` — the entry-level group — held **create rights on
`realestate.check.bounce`**, and creating a bounce row is what bounces a cheque.
Any Checks User could bounce any cheque in any company over RPC.

### 1.5 Leading zeros were destroyed on every bulk-generated cheque

```python
start_num = int(self.first_check_number.strip())   # '000012345' -> 12345
...
'check_number': str(num)                            # -> '12345'
```

`000012345` is a different cheque from `12345` and the zeros are printed on the
paper. Any non-numeric format (`AB-0012`) could not be bulk-loaded at all.

### 1.6 Structural gaps

| Gap | Consequence |
|---|---|
| `replacement_check_id` / `replaces_check_id` declared `readonly`, never written; `replaced` state unreachable | The replacement feature was declared and absent |
| A cheque could only be presented once (`deposit_id` is a single M2O) | A bounced cheque could not be re-presented without destroying the first attempt |
| One M2O to one instalment | One cheque ↔ many obligations impossible; many ↔ one unvalidated |
| No `else` in `_compute_project_from_source` | Creating a cheque with no contract left two stored computes unassigned |
| Penalty product declared `product.template`, written into `product.product` | The penalty invoice bound the wrong product or none |
| Bank charge and customer penalty in one `penalty_amount` | Two different parties' money in one field |
| `deposit_date` a stored *related* field | Cancelling a slip erased the record of a presentation that really happened |
| No company plumbing at all | A Company A cheque could be deposited into Company B's journal |
| No tests | All of the above shipped |

---

## 2. Instrument architecture

Four distinct things, and 0.2 keeps them apart by construction.

```
    realestate.sale.installment        the COMMERCIAL OBLIGATION
    realestate.contract.payment         — what is owed, and when
              │                           (Developer / Rental own this)
              ▼
        account.move                    the ACCOUNTING DOCUMENT
                                        — what was billed
                                          (Odoo owns this)
              ▲
              │  reconciliation
              ▼
      account.payment                   the MONEY
    account.bank.statement.line         — what arrived
                                          (Odoo owns this)
              ▲
              │  1 : 1 per presentation
              │
      realestate.check                  the INSTRUMENT
                                        — the piece of paper
                                          (this module owns this)
```

**A cheque is not an invoice** (Rule 1). It carries no account, no tax, no
journal entry. It points at obligations through `realestate.check.allocation`,
and that pointing settles nothing.

**A received PDC is not cash** (Rule 2). Registering one creates no
`account.payment` and touches no residual. `test_a_pdc_does_not_mark_its_
instalment_paid` asserts exactly the example from the brief: cheque received
today, due 2027-06-01, instalment still outstanding.

**Odoo owns payment truth** (Rule 3). The module never writes `payment_state`,
never zeroes a residual, never deletes a payment or edits a posted entry. Every
accounting figure it displays is read back from Odoo.

**Clearance comes from bank reconciliation** (Rule 4). See §6.

---

## 3. State machine

### 3.1 `realestate.check.state`

Every 0.1 value survives with its original meaning — Developer matches on these
strings and production rows carry them. Two were **added**.

```
 draft ──register──► registered ──deposit.confirm──► deposited ──►in_clearing
                          ▲                              │            │
                          │                              └─────┬──────┘
                          │                                    │
                          │                        ┌───────────┴───────────┐
                          │                        │                       │
                          │              (bank reconciled)          (bank returns)
                          │                        │                       │
                          │                        ▼                       ▼
                          │                    cleared                  bounced
                          │                   [terminal]                   │
                          │                                                │
                          ├──── authorise re-presentation ◄────────────────┤
                          │                                                │
      replaced ◄──────────┴──── replacement wizard ◄──────────────────────┤
                                                                           │
      returned ◄─── hand the paper back ◄──────────────────────────────────┘
     cancelled ◄─── cancel (draft/registered only)
```

| State | 0.1? | Meaning |
|---|---|---|
| `draft` | yes | Recorded, not yet accepted |
| `registered` | yes | Accepted and held. **On hand.** |
| `deposited` | yes | Presented to a bank. Payment registered. **At bank.** |
| `in_clearing` | **new** | At the bank, bank has acknowledged, outcome unknown |
| `cleared` | yes | Odoo's reconciliation confirms the money. **Terminal.** |
| `bounced` | yes | Returned by the bank |
| `replaced` | yes | Superseded by another instrument. *Now reachable.* |
| `returned` | **new** | Physically handed back to the drawer |
| `cancelled` | yes | Voided before presentation |

### 3.2 Transitions that are refused, and why

| Attempt | Result |
|---|---|
| Cancel a `cleared` cheque | Refused — the money arrived; use a refund or credit note |
| Cancel a cheque at the bank | Refused — resolve the presentation first |
| Bounce a cheque not at a bank | Refused — 0.1 allowed this from *any* state |
| Mark cleared without a bank match | Refused, with the reason (§6) |
| Replace a cleared cheque | Refused |
| Replace a cheque at the bank | Refused — the obligation would be covered twice |
| Present the same cheque twice concurrently | Refused — one open attempt per cheque |

### 3.3 The second dimension

`state` is physical. `accounting_state` is a **read-only view of Odoo**:
`no_payment` → `payment_registered` → `in_payment` → `reconciled`, plus
`reversed`. Nothing in this module writes an accounting field to make one of
them true; the compute reads `payment.state`, `payment.is_matched` and
`payment.reconciled_invoice_ids` and reports what it finds.

### 3.4 `realestate.check.deposit.state`

0.1's five values kept; `reconciled` added, so "every cheque cleared" and "the
whole batch is bank-matched" are finally distinguishable.

---

## 4. Allocation architecture

`realestate.check.allocation` is the single authoritative answer to *what does
this cheque pay?*

```
realestate.check ──1:N──► realestate.check.allocation ──1:1──► obligation
                                                                ├─ realestate.sale.installment
                                                                └─ realestate.contract.payment
                                        │
                                        └── move_id (read from the obligation,
                                            never authored here)
```

### 4.1 The three shapes, all supported and all tested

| Case | Shape | Test |
|---|---|---|
| A | 1 cheque → 1 instalment | `test_case_a_one_cheque_one_instalment` |
| B | 2 cheques → 1 instalment | `test_case_b_two_cheques_one_instalment` |
| C | 1 cheque → N obligations | `test_case_c_one_cheque_several_instalments` |

### 4.2 Invariants

* **Cheque side:** `SUM(active allocations) ≤ face value`, always. Enforced on
  both models, because an allocation can be created without the cheque being
  written and Odoo only fires the constraints of the model you touch.
* **Obligation side:** two cheques covering one instalment is fine; covering it
  twice over is a data-entry error and is refused at the point of entry.
* **`unapplied_amount` is displayed, never hidden.** An unallocated balance is a
  real operational state.
* **Strict mode:** `res.company.check_require_full_allocation` blocks
  presentation while anything is unapplied. Off by default — a genuine
  consolidated cheque often arrives before the schedule it will settle.

### 4.3 Compatibility with the legacy fields

`sale_installment_id` and `rental_payment_id` survive, and setting one still
means "this cheque pays that". They are now a **two-way mirror** of the engine:

* setting the field creates one allocation for the full face value;
* a cheque with exactly one active allocation has the field populated;
* **a cheque split across several obligations has the field blank** — pointing
  it at the first of three would be a falsehood that Developer's guards would
  then act on.

### 4.4 Coverage, named so it cannot be misread

`realestate.sale.installment.secured_by_checks_amount` and `unsecured_amount`;
`realestate.contract.payment.secured_by_checks_amount`. Deliberately not
`paid`, not `collected`. A cleared cheque **stops** counting as security, so the
same money is never both paper and cash.

---

## 5. Deposit architecture

```
   Deposit Workbench (M22)          eligible = registered + matured + right
        │                                      company + right currency
        ▼
   realestate.check.deposit         the PHYSICAL batch
        │  action_confirm()          — this paper, this bank, this day,
        │                              prepared by X, confirmed by Y
        ├──► realestate.check.presentation   one attempt per cheque
        ├──► realestate.check.custody        paper moves to "At Bank"
        │
        ▼
   account.payment  (one per cheque)         ODOO owns this
        │  reconciled against the invoices behind the cheque's allocations
        │
        ▼
   account.batch.payment  (optional)         ODOO owns this, when present
        │
        ▼
   bank statement line ──reconcile──► payment.is_matched = True
        │
        ▼
   cheque state = cleared                    read back, never asserted
```

### 5.1 What confirmation validates

0.1 checked one thing (the cheque's state). 0.2 checks: same company; same
currency as the slip; state is `registered`; no open presentation elsewhere;
maturity against the company's early-presentation policy; staleness; allocation
completeness under strict mode; journal belongs to the company; maker/checker.
**All problems are collected and reported together** — a treasurer fixing a
50-cheque batch should not discover them one press at a time.

### 5.2 The Enterprise question

`account_batch_payment` is licensed **`OEEL-1`**. This module is **LGPL-3** and
the whole ATMTA suite depends only on Community modules. A hard dependency would
make the suite Enterprise-only and create a licence conflict, so the integration
is **optional and late-bound**:

```python
if 'account.batch.payment' not in self.env:
    return False
```

On Enterprise, the deposit's payments are grouped so one bank line reconciles
the batch. On Community, each payment reconciles individually and **nothing
about the treasury workflow changes**. Both paths are tested.

The division of ownership:

| | Owner |
|---|---|
| Which physical cheques, whose custody, what maturity, who prepared/confirmed, the slip handed to the teller | `realestate.check.deposit` |
| Grouped payments, outstanding-receipts balance, the single bank line, batch accounting reference | `account.batch.payment` (when installed) |

---

## 6. Clearance — the precise definition

> **A cheque is `cleared` when, and only when, `payment.is_matched` is true for
> the payment raised by its live presentation.**

Written in exactly one place, `check._sync_clearance_from_accounting()`. The
manual button, the deposit's "Clear Matched Cheques", and the cron all route
through it, so there is no path by which a cheque clears on a weaker test.

### 6.1 Why `is_matched` and not `payment.state == 'paid'`

This distinction was found by the test suite and is the single most important
correction in the release. Odoo 18's `account_payment.py::_compute_state`:

```python
if (payment.state == 'in_process' and payment.reconciled_invoice_ids and
        all(invoice.payment_state == 'paid' for invoice in payment.reconciled_invoice_ids)):
    payment.state = 'paid'
```

and on Community `_get_invoice_in_payment_state()` returns `'paid'`. So **a
payment is promoted to `paid` the moment the invoices it settles read paid —
with no bank transaction anywhere**. Keying clearance off `state` would have
marked every cheque cleared the instant it was *presented*: the exact error 0.1
made by hand, reintroduced by a different route.

`is_matched` is honest in all three configurations
(`_compute_reconciliation_status`):

| Configuration | `is_matched` |
|---|---|
| Outstanding-receipts account (reconcilable) | true only once the liquidity line has no residual — a real bank match |
| Payment posts into the bank account directly | true immediately — correct, that configuration has no matching step |
| No outstanding account at all | falls back to `state == 'paid'` — the only signal that configuration has |

`test_both_configurations_use_the_same_clearance_test` asserts both supported
configurations, as M7 requires.

### 6.2 What the user sees while it is pending

The cheque form carries a standing notice: *"This cheque is at the bank. A
payment has been registered, but the money is not confirmed until Odoo
reconciles it against a bank transaction."* Pressing "Refresh Clearance" on an
unmatched cheque produces:

> Cheque CHK-000123 cannot be marked cleared. Its payment PBNK/2026/00007 is
> 'in_process' and has not been matched against a bank transaction.
> Reconcile the bank statement in Accounting and the cheque will clear by
> itself. **Marking it cleared here would record cash that has not arrived.**

---

## 7. Bounce accounting

### 7.1 The normal case — before and after

| | Before bounce | After bounce |
|---|---|---|
| Invoice exists | yes, posted | **yes, posted** (never deleted) |
| Invoice residual | 0 | **restored to full** |
| Invoice `payment_state` | paid / in_payment | **not_paid** |
| Payment exists | yes, posted | **yes, cancelled** (never deleted) |
| Payment journal entry | posted | **reversed, still visible** |
| Commercial instalment | settled | **still due**, `paid_amount = 0` |
| Cheque | deposited | **bounced** |
| Presentation attempt 1 | presented | **bounced**, with the bounce linked |

The sequence is **unreconcile, then cancel** — in that order. Unreconciling is
what restores the residual; cancelling reverses the entry through Odoo's own
`action_cancel`. Doing it the other way round, or deleting anything, would leave
an invoice reading as settled by a payment that no longer exists.

Nothing writes `payment_state`. Nothing zeroes a residual. Nothing calls
`unlink()` on a posted record. `test_accounting_history_is_preserved` asserts
that the invoice, the payment and the payment's journal entry all still exist
afterwards.

### 7.2 The already-bank-reconciled case

M10 says this must be handled separately, and it is. If the bank statement had
already been matched and the bank *later* posts a returned-cheque debit, the
original receipt genuinely happened. Unwinding it would leave the bank account
unreconcilable against a statement the accountant has already agreed.

So the module **refuses to automate it**. It marks the cheque bounced, sets
`requires_manual_accounting`, leaves the ledger untouched, and writes
instructions onto the record and into the chatter:

> Payment PBNKO/2026/00001 has already been matched against a bank transaction,
> so the original receipt genuinely happened. This module will not silently
> unwind a matched bank line…
> 1. Record the bank's returned-cheque debit as a new bank transaction.
> 2. Reconcile it against a reversal of payment PBNKO/2026/00001, or against the
>    customer's receivable directly.
> 3. The invoice returns to outstanding through that reversal.

Re-presentation is **blocked** while a bounce is in that state, so the same
money cannot be registered twice. The bounce wizard shows which of the two paths
will be taken *before* anything is written.

### 7.3 Fees — two parties, two fields (M11)

0.1 had one `penalty_amount` doing both jobs. 0.2 separates them:

* `bank_charge_amount` — what the **bank** charged **us**;
* `penalty_amount` — what **we** charge the **drawer**.

The penalty is invoiced by an explicit, separate action. 0.1's wizard carried an
"Issue Penalty Invoice Now" tickbox that raised a customer invoice as a side
effect of recording a bank event; billing a customer is not a side effect. No
account is hard-coded — the configured service product's category drives the
income account, exactly as for any other sale.

---

## 8. Re-presentation

`realestate.check.presentation` makes each trip to the bank a first-class
record. A cheque can therefore read:

```
attempt 1   2026-03-01   DEP-00012   payment PBNK/00007 (cancelled)   bounced
attempt 2   2026-04-15   DEP-00031   payment PBNK/00044               cleared
```

Nothing about attempt 1 is destroyed. The bounce record survives, the cancelled
payment survives, and its journal entry survives.

Constraints: a cheque may have **one open attempt at a time** (it cannot be at
two banks); attempt numbers are derived from what exists rather than passed in,
so a re-presentation cannot reuse a number and overwrite history; an attempt
that reached the bank cannot be deleted.

Authorising a re-presentation returns the cheque to `registered` and clears its
`deposit_id` and `payment_id`, so the next deposit raises a **fresh** payment —
the first one was cancelled when the cheque bounced.

---

## 9. Replacement

`root_check_id` and `replacement_generation` are stored and indexed, so a whole
chain is one query rather than a parent-walk:

```
CHK-A   generation 0   root = A   bounced   → replaced by
CHK-B   generation 1   root = A   bounced   → replaced by
CHK-C   generation 2   root = A   cleared
```

Refused: self-replacement; cycles; replacing a cleared cheque; replacing a
cancelled or returned cheque; replacing a cheque that is at the bank; replacing
one twice; a replacement smaller than the obligations it would carry.

**Allocations move rather than copy, and the old ones are released first.**
Raising the new allocations before cancelling the old ones would momentarily
cover the obligation twice — which the obligation-side invariant correctly
refuses. That ordering bug existed in the first draft and was caught by
`test_allocations_move_rather_than_duplicate`.

---

## 10. Developer M6 interaction

Developer's `_blocking_checks` / `_assert_no_blocking_checks` are **overridden
here**, so its existing call sites get the correct answer without Developer
changing at all. The four workflows it never guarded are guarded by override
too.

### 10.1 The API (M15)

| Method | Returns |
|---|---|
| `contract.get_check_exposure(installments=None)` | plain dict: five buckets (on hand / at bank / bounced / cleared / closed), counts, amounts, per-cheque detail, `can_change_schedule` |
| `contract.get_locked_allocations(installments=None)` | allocations whose cheque has left the building |
| `contract.assert_schedule_can_change(installments, operation)` | raises with the offending cheques named |

Developer no longer inspects Checks internals with scattered searches.

### 10.2 Corrections to 0.1's guard

| 0.1 | 0.2 |
|---|---|
| Searched `sale_installment_id` only — a contract-level cheque was invisible | Contract-level cheques included, **including when the caller narrows to specific instalments**: an unallocated cheque at the bank is unscoped exposure and bears on everything |
| Matched `'collected'`, `'endorsed'` — states that have never existed | Gone |
| `in_clearing` did not exist | Included |
| `bounced` was not blocking | **Blocking** — once a payment exists, a bounce leaves the money position in flux |
| Cancellation and buyer transfer unguarded | Guarded |

### 10.3 The matrix

| Workflow \ cheque | On hand (registered) | At bank (deposited / in clearing) | Bounced, unresolved | Cleared |
|---|---|---|---|---|
| **Restructuring** | allowed — cancel / reallocate / replace | **blocked** | **blocked** | allowed (history) |
| **Early settlement** | allowed; paper listed in the preview and in chatter | **blocked** | **blocked** | allowed (history) |
| **Cancellation** | **blocked** until returned or cancelled | **blocked** | **blocked** | allowed (money arrived) |
| **Unit swap** | allowed; allocations follow the open balance | **blocked** | **blocked** | allowed — stays on the **original** property |
| **Buyer transfer** | allowed; drawer **never** rewritten, Treasury told to collect replacements | **blocked** | **blocked** | allowed — drawer **never** rewritten |

Two rules the matrix encodes:

* **Historical instrument ownership is a fact.** A cheque is drawn on a named
  person's account, and a contract transfer does not retro-actively make the new
  buyer the drawer. `test_a_transfer_never_rewrites_a_drawer` asserts it for
  both cleared and uncashed paper.
* **Cleared cheques stay with their original obligation.** A unit swap does not
  remap them to the new property.

---

## 11. Custody

`realestate.check.custody` is an append-only movement log.
`check.custodian_id` and `check.location_id` are `readonly=True` mirrors of the
last movement — there is no way to change them except by recording one.

* Movements **cannot be edited** (only the free-text note and the receiver may
  be corrected) and **cannot be deleted**.
* Every cheque opens with a movement, so history starts somewhere honest.
* Locations are configurable records (`realestate.check.location`), not a
  hard-coded selection — a developer with four sales offices needs four.
* Presenting a deposit moves custody to the bank; a bounce brings it back; a
  return to the customer ends it. All three go through `Custody.transfer()`, so
  every route leaves the same trail.
* `None` means "unchanged", `False` means "there is no longer one" — conflating
  them produced a movement that moved nothing, which a constraint now refuses.

---

## 12. Security

### 12.1 Roles

The three 0.1 xml ids are **kept** — renaming one would silently un-assign every
production user. `group_checks_treasurer` becomes M28's Treasury Officer, and a
new Approver slots in above it.

```
User ──► Treasury Officer ──► Treasury Approver ──► Manager
```

| Role | Can |
|---|---|
| **User** | Register cheques, allocate them |
| **Treasury Officer** (`group_checks_treasurer`) | + custody, deposits, bounce, re-present, replace, cancel, return, see full bank details |
| **Treasury Approver** | + confirm deposits under maker/checker |
| **Manager** | + configuration, locations, delete |

### 12.2 Server-side enforcement

Every sensitive method calls `_assert_group()` **before** doing anything:
confirm deposit, clear, bounce, re-present, replace, cancel, return to customer,
custody transfer, penalty invoice, manual sync. Tested from purpose-built users
in `test_security_multicompany.py`, not by inspecting view XML.

The over-broad ACL is closed: `group_checks_user` now has **read only** on
bounces, deposits, custody and presentations.

### 12.3 Maker/checker (M28)

Configurable per company, default off (turning it on for a one-person treasury
would lock them out), with a self-approval limit. Enforced on the server.

### 12.4 Privacy (M32)

`account_number`, `branch` and `partner_bank_id` are behind
`group_checks_see_bank_details`, implied by Treasury Officer and **not** by
User. None of them is `tracking=True` — tracking a bank account number would
copy it into a permanent, widely readable chatter message on every edit.
Asserted by `test_the_account_number_is_not_tracked_into_chatter`.

---

## 13. Multi-company

Seven global record rules, one per model, none of which admits
`company_id = False` (a cheque with no company is a data error, and making it
visible to everyone would be the wrong way to surface it). All are
`global`, so they bind administrators too.

`_check_company_auto` on every model, `check_company=True` on
`sale_contract_id`, `deposit_id`, `journal_id`, `payment_id`, `location_id`, and
an explicit constraint with a readable message:

> Cheque CHK-000123 belongs to Alpha Developments but its bank journal belongs
> to Beta Holdings. A cheque cannot cross companies.

Deposit confirmation refuses a foreign cheque; an allocation cannot straddle
companies; sequences are drawn `with_company(...)`, so a deployment that creates
per-company sequence records gets per-company numbering and one that does not
keeps its single shared counter.

**Multi-currency (M30):** one slip, one currency — mixed batches are refused
rather than silently summed. The dashboard detects more than one currency in
scope and says so instead of adding EGP to SAR. FX is left entirely to Odoo.

---

## 14. Reports and dashboard

### 14.1 Treasury dashboard (M20)

Every figure is produced by `_read_group`; no records are loaded. Every KPI
carries the server-side domain that produced it, so **a count and its
drill-down cannot disagree** — asserted for every KPI in
`test_every_kpi_count_equals_its_drilldown`.

Every KPI also carries an explicit `kind`:

| `kind` | Meaning | Sections |
|---|---|---|
| `paper` | Face value of instruments | On hand, At bank, Risk, PDC received |
| `cash` | Money the bank honoured and Odoo reconciled | Cleared |
| `obligation` | What is owed | Coverage |

The page says so in as many words: *"Paper is not cash."* **"PDC amount
received" is never labelled "cash collected"** — asserted by
`test_the_coverage_block_labels_paper_as_paper`.

KPIs: on hand / due today / due 7 / due 30 / matured-not-presented; at bank /
deposits today / awaiting reconciliation; cleared this month / to date; bounced,
bounce rate (denominator = cheques actually **presented**), unresolved bounces,
replacements outstanding; future obligations, PDC received, coverage %,
unsecured.

Charts: maturity forecast (clickable to the bucket), by state, by bank, by
project, bounce reasons.

### 14.2 Maturity forecast (M21)

Nine buckets from `past_due` to `>365`, grouped in SQL on the stored
`maturity_bucket`, optionally split by company / project / customer / bank /
currency. Carries its own disclaimer: *"expected paper, not guaranteed cash — a
cheque can still bounce."*

### 14.3 The eight registers (M25)

Check Register · PDC Maturity · Deposit Register · Cleared Register · Bounce
Register · Replacement Register · PDC Coverage · Customer PDC Statement.

All list/pivot/graph actions. A register is a dataset a treasurer filters and
exports; rendering it to paper would make it less useful.

### 14.4 The two documents that are useful on paper

* **Deposit Slip** (M23) — what the treasurer carries to the teller: company,
  bank, reference, date, count, total, per-cheque number/drawer/bank/due/amount,
  prepared-by, confirmed-by, and a bank acknowledgement block.
* **PDC Acknowledgement** (M24) — deliberately **not** called a payment receipt.
  It says on its face: *"This document acknowledges receipt of the physical
  instruments listed below. It is not a payment receipt: no payment has been
  made."* Asserted by
  `test_the_acknowledgement_is_not_called_a_payment_receipt`.

---

## 15. Migration

Version `0.1` → `0.2`. **This module had no `migrations/` directory at all**, so
this is the first upgrade it has ever run.

### 15.1 Pre-migration — look before touching

Reports, never repairs:

* duplicate cheque identities under the new key, listed by reference;
* company mismatches (cheque vs contract, cheque vs slip, slip vs journal) —
  **never reassigned**, because guessing which company is right is not a
  migration's business;
* cheques with no company, which the new rules will hide;
* how many `cleared` cheques carry no payment, i.e. rest on a 0.1 button press
  rather than a bank match.

Then it adds the new columns with conservative defaults in one pass, seeds
`presented_date` from the old `deposit_date`, and drops 0.1's blanket
uniqueness constraint.

### 15.2 The identity index

The new key adds `account_number` and is **partial** (live states only), so a
cancelled number can be reissued. It is created in `init()` **only after a
duplicate probe passes**. If live rows already collide the index is skipped, the
collisions are logged by cheque reference, the upgrade completes, and the index
appears by itself on the next upgrade once the data is clean. Nothing is ever
deleted or merged.

### 15.3 Post-migration — additive only

* **Allocations** — one per legacy cheque with `sale_installment_id` **or**
  `rental_payment_id`, for the full face value, only where the link is
  unambiguous. A cheque naming both is reported, not guessed. Cancelled and
  replaced cheques get a *cancelled* allocation, so history is recorded without
  securing anything. **No amount is duplicated.**
* **Custody** — one opening movement per live cheque, reason `opening`, dated
  from the cheque's own `create_date`, with a note saying it is a placeholder.
  An honest placeholder, not an invented handover.
* **Presentations** — attempt 1 reconstructed for every cheque that has a
  deposit link, from data already on the cheque.
* **Configuration** — per-company settings pointed at the penalty and
  bank-charge product *variants*, fixing 0.1's `product.template`-into-
  `product.product` bug. Default locations seeded.

### 15.4 Verified against legacy-shaped data

`real_estate_checks` was never committed to git, so 0.1 could not be checked
out. The legacy **shape** was reproduced directly instead — cheques with the old
single-obligation link and no allocation, custody or presentation rows; a
`cleared` cheque with no payment (exactly what 0.1's button produced); and a
colliding identity pair — the module version rolled back to `18.0.0.1`, and the
upgrade run for real. Results in §17.

---

## 16. Tests

**Baseline before any work, captured from the repository as it stood:**

| Module | Tests before |
|---|---|
| `atmta_real_estate` | 299 |
| `real_estate_developer` | 247 |
| **`real_estate_checks`** | **0** |
| `real_estate_handover` | 0 |
| `real_estate_portal` | 0 |
| Accounting integration (in Checks) | 0 |
| **Total ATMTA** | **546** |

The one failure in that baseline run was `TestDashboardRTL.test_dashboard_rtl`,
and it was **environmental**: `rtlcss` was installed under
`/home/mageed/.npm-rtl` but not on `PATH`, so Odoo could not generate the RTL
bundle (`WARNING … You need https://rtlcss.com/ to convert css file to right to
left compatiblity`). Fixed in the test runner, not in the product.

**After:**

| Module | Tests after |
|---|---|
| `atmta_real_estate` | 299 (unchanged — frozen) |
| `real_estate_developer` | 247 (unchanged) |
| **`real_estate_checks`** | **246** |
| **Total ATMTA** | **792** |

### Coverage by area

| File | Area | Tests |
|---|---|---|
| `test_check_master.py` | Numbering, identity key, validation, state machine | 26 |
| `test_developer_integration.py` | M6 workflows × cheque positions | 24 |
| `test_deposit.py` | Batch validation, lifecycle, maker/checker, workbench | 24 |
| `test_bounce.py` | Accounting effect, guards, already-matched case, fees, resolution | 24 |
| `test_accounting.py` | Payment timing, reconciliation, clearance, both configurations, full lifecycle, batch | 24 |
| `test_dashboard_reports.py` | KPIs, drill-down parity, forecast, registers, documents, performance | 23 |
| `test_security_multicompany.py` | Server-side enforcement, isolation, privacy, portal | 22 |
| `test_maturity_custody.py` | Maturity, presentation policy, custody | 21 |
| `test_representation_replacement.py` | Second attempts, replacement chains | 17 |
| `test_allocation.py` | Three shapes, invariants, legacy mirror, coverage labels | 16 |
| `test_compatibility.py` | Rule 5 contract surface, migration shape | 15 |
| `test_rental.py` | Rental parity | 10 |
| | **Total** | **246** |

### Bugs the tests caught in my own implementation

Worth recording, because two of them were the same category of error the audit
found in 0.1:

1. **Clearance keyed off `payment.state == 'paid'`.** Odoo 18 promotes a payment
   to `paid` as soon as its invoices read paid — no bank involved. Every cheque
   would have cleared the instant it was presented. Fixed to `is_matched`; see
   §6.1. *This is the most important correction in the release.*
2. **The bounce's "already matched" guard used the same wrong test**, so every
   ordinary bounce was routed down the manual path and the receivable was never
   restored.
3. **Replacement created the new allocations before releasing the old**,
   momentarily covering the obligation twice.
4. **The allocation "exactly one obligation" constraint never fired** when both
   fields were omitted — Odoo only validates constrained fields present in
   `vals`. Fixed by adding the always-present `check_id` to the trigger list.
5. **A contract-level cheque was invisible** to the guard when the caller
   narrowed to specific instalments — reopening 0.1's hole by another route.
6. **Registering a cheque required Treasury rights**, because it writes a
   custody row. A clerk could not do their own job.
7. **`to_custodian=False` meant "unchanged"**, so returning a cheque produced a
   movement that moved nothing.

---

## 17. Full-suite compatibility

### 17.1 Fresh install of all 14 modules, all ATMTA tests

```
RC=0
0 failed, 0 error(s) of 792 tests
```

| Module | Version | State | Tests |
|---|---|---|---|
| `atmta_real_estate` | 18.0.0.4 | installed | 299 |
| `real_estate_developer` | 18.0.0.3 | installed | 247 |
| `real_estate_checks` | **18.0.0.2** | installed | **246** |
| `real_estate_handover` | 18.0.0.1 | installed | — |
| `real_estate_portal` | 18.0.0.1 | installed | — |
| `real_estate_api` | 18.0.0.1 | installed | — |
| `real_estate_brokerage` | 18.0.0.1 | installed | — |
| `real_estate_construction` | 18.0.0.1 | installed | — |
| `real_estate_contract_template` | 18.0.0.1 | installed | — |
| `real_estate_customer_service` | 18.0.0.1 | installed | — |
| `real_estate_investment` | 18.0.0.1 | installed | — |
| `real_estate_maquette` | 18.0.0.4 | installed | — |
| `real_estate_plan` | 18.0.0.3 | installed | — |
| `real_estate_procurement` | 18.0.0.1 | installed | — |

Module 1's 299 and Developer's 247 are **unchanged** — neither module lost or
gained a test, and neither needed a change.

The only `ERROR` lines in the log are two `odoo.sql_db: bad query` entries from
Module 1 and Developer tests that deliberately provoke constraint violations to
assert them. They are present in the baseline run too.

### 17.2 A defect this work found in Developer, and fixed

**A fresh install of the 14-module suite failed** before any Checks work began.
`real_estate_developer/views/report_actions.xml` hangs a Reports submenu off
`menu_developer_root`, but the manifest loaded it **before** `views/menus.xml`,
which defines that menu:

```
ValueError: External ID not found in the system: real_estate_developer.menu_developer_root
  while parsing .../views/report_actions.xml:233
```

The 0.3 release was verified by **upgrading an existing database** — where the
root menu already exists — and never by a fresh install, so this never surfaced.

Under the frozen-core rule this was reproduced (the baseline could not be
captured at all without it), the smallest possible fix applied — swapping two
lines in the manifest's `data` list — and the full Developer suite re-run: **247
tests, 0 failures.** No other change was made to Developer.

### 17.3 Migration verified against legacy-shaped data

`real_estate_checks` was never committed to git, so 0.1 could not be checked
out. The legacy *shape* was reproduced instead (§15.4) and the upgrade run for
real.

**Planted:** 6 cheques with the 0.1 single-obligation link, no allocations, no
custody, no presentations; one `cleared` with no payment; one colliding identity
pair. Module version rolled back to `18.0.0.1`.

**Result — `RC=0`, the upgrade completed:**

| Check | Result |
|---|---|
| Cheques still there | 6 / 6 |
| Legacy references intact | 6 / 6 |
| Allocations created | 4 (one per instalment link) |
| Allocated total | 400,000 — exactly 4 × 100,000, **no amount duplicated** |
| Opening custody seeded | 6 |
| `cleared` row untouched | 1 |
| Duplicate identity pair | **both rows still present** — nothing merged or deleted |
| Identity index | **not created**, and said so |
| Version after | `18.0.0.2` |

The two `ERROR` lines in that run are the deliberate reports:

> `1 group(s) of live cheques share an identity … NOTHING HAS BEEN CHANGED —
> the uniqueness index will be skipped and retried on the next upgrade.`

> `1 of 1 cleared cheque(s) carry no accounting payment. In 0.1 'cleared' meant
> a treasurer pressed a button; from 0.2 it means Odoo reconciled a payment.
> These rows are left EXACTLY as they are.`

**The retry path was then verified.** Treasury resolved the collision the way a
human would — cancelling the duplicate — and the upgrade was run again:

```
real_estate_checks: cheque identity index created.
RC=0, 0 ERROR/CRITICAL
allocations: still 4  (not re-created)
custody:     still 6  (not re-seeded)
```

The migration is idempotent, and the index appears by itself once the data is
sound.

---

## 18. Remaining gaps

Nothing here is hidden; each item says exactly where it stops.

### Implemented in full

M1 instrument hardening · M2 state machine + accounting dimension · M3
allocation engine · M4 custody · M5 maturity and presentation policy · M6
deposit architecture · M7 payment timing · M8 reconciliation sync · M9
presentation attempts · M10 bounce v2 · M11 separated fees · M12
re-presentation · M13 replacement lineage · M14 cancellation · M15 Developer
integration · M16 Rental parity · M17 payment correspondence · M18 partial
coverage · M19 coverage analytics · M20 dashboard · M21 forecast · M22
workbench · M23 deposit slip · M24 acknowledgement · M25 registers · M27
activities · M28 maker/checker · M29 multi-company · M30 multi-currency · M31
security · M32 privacy · M33 performance · M34 crons · M35 migration · M36
tests · M37 bank-reconciliation integration test · M38 full-suite regression

### Partial

**M6 — Odoo batch payment.** Fully wired, but only active when Enterprise's
`account_batch_payment` is in the registry. On Community the payments are
created and reconciled individually. This is a licensing constraint
(`OEEL-1` vs `LGPL-3`), not an omission, and it is the correct resolution.

**M20 — dashboard front end.** The OWL component, KPIs, charts and drill-downs
are built. It has **not** been verified in a real browser (Module 1's dashboard
was, and that is how its RTL bug was found). The server payload is fully tested;
the rendering is not.

### Intentionally deferred

**M26 — portal.** Phase 0 found the portal exposes no PDC data at all, so this
is greenfield rather than a migration. It is deferred deliberately: publishing
cheque data to customers means deciding what a customer may see of a bank
account number, a treasury note and a bounce reason, and that is a product
decision rather than an engineering one. `test_no_portal_route_exposes_cheques`
asserts that nothing has started exposing it by accident.

**Bank charge accounting.** `bank_charge_amount` is recorded and reported;
`bank_charge_move_id` exists as the link. Raising the vendor bill automatically
is not implemented — how a bank fee is booked differs by bank and by chart of
accounts, and getting it wrong is worse than leaving it to Accounting.

### Localisation-specific — deliberately out of core

Egyptian and Saudi cheque validity periods; legal presentation windows;
country-specific returned-cheque procedures; debt-collection and legal
escalation. All are exposed as **configuration** (`check_stale_days`,
`check_allow_early_deposit`, `check_early_deposit_days`) with conservative
defaults, and none is hard-coded. A localisation module sets them.

### Unresolved accounting limitation — stated plainly

**A bounce on a payment that has already been matched to a bank statement is not
automated.** It cannot be, safely and generically: unwinding a matched bank line
would break a reconciliation the accountant has already agreed, and the correct
correcting entries depend on how the bank posts the return. The module detects
the case, refuses to touch the ledger, marks the instrument, blocks
re-presentation until it is resolved, and writes step-by-step instructions onto
the record. That is a controlled workflow rather than a silent corruption — but
it is manual, and it is the one place where Treasury has to finish the job in
Accounting.

### Not built here, per the brief

Generic bank-reconciliation replacement · accounting ledger · full Collections
case management · CRM · sales pricing · payment-plan engine · developer contract
amendments · property management · country-specific cheque law · debt-collection
workflow · 2D/3D · construction.

---

# 19. RELEASE FREEZE — `real_estate_checks 0.2`

**Frozen:** 2026-08-05
**Scope of this closeout:** verification and documentation only. No Treasury or
PDC features were added. Portal was not implemented. The already-bank-matched
returned-cheque scenario was not automated.

---

## 19.1 Final test totals

| | Baseline (0.1) | Frozen (0.2) |
|---|---|---|
| `atmta_real_estate` | 299 | **299** (frozen, unchanged) |
| `real_estate_developer` | 247 | **247** (unchanged) |
| **`real_estate_checks`** | **0** | **325** |
| `real_estate_handover` | 0 | 0 |
| `real_estate_portal` | 0 | 0 |
| **ATMTA total** | **546** | **871** |

### `real_estate_checks` — 325 tests

| File | Area | Tests |
|---|---|---|
| `test_reports_and_access.py` | 8 registers, 2 QWeb documents, sensitive-data access | 30 |
| `test_check_master.py` | Numbering, identity key, validation, state machine | 26 |
| `test_developer_integration.py` | M6 workflows × cheque positions | 24 |
| `test_deposit.py` | Batch validation, lifecycle, maker/checker, workbench | 24 |
| `test_bounce.py` | Accounting effect, guards, matched case, fees, resolution | 24 |
| `test_accounting.py` | Payment timing, reconciliation, clearance, both journal configurations, full lifecycle, batch | 24 |
| `test_dashboard_reports.py` | KPIs, drill-down parity, forecast, registers, performance | 23 |
| `test_security_multicompany.py` | Server-side enforcement, isolation, privacy, portal | 22 |
| `test_maturity_custody.py` | Maturity, presentation policy, custody | 21 |
| `test_representation_replacement.py` | Second attempts, replacement chains | 17 |
| `test_allocation.py` | Three shapes, invariants, legacy mirror, coverage labels | 16 |
| `test_compatibility.py` | Rule 5 contract surface, migration shape | 15 |
| `test_terminology.py` | Labels, payload, view arch, report wording | 14 |
| `test_dashboard_browser.py` | 10 browser environments | 14 |
| `test_dashboard_assets.py` | Bundle composition, production assets | 10 |
| `test_clearance_semantics.py` | The `is_matched` oracle, pinned | 8 |
| `test_workflow_browser.py` | Deposit / bounce / matched-bounce flows in a browser | 3 |

---

## 19.2 Browser verification

14 tours driven through real headless Chrome. Nothing below is asserted from a
stylesheet or a payload: geometry comes from `getBoundingClientRect()`,
direction from `getComputedStyle()`, and every figure is read out of the
rendered DOM.

### Environments

| Scenario | How | Result |
|---|---|---|
| Desktop | `1920x1080` | pass |
| Tablet landscape | `1024x768`, touch emulation | pass |
| Bootstrap lg/md boundary | `991x768`, touch emulation | pass |
| Tablet portrait | `768x1024`, touch emulation | pass |
| RTL Arabic | `ar_001` session, `direction` + mirrored geometry | pass |
| Empty company | company with no cheques at all | pass |
| Multi-company switch | Odoo's own switcher, 3 + 7 → 10 | pass |
| Restricted Checks User | only `group_checks_user` | pass |
| Treasury Officer | only `group_checks_treasurer` | pass |
| Treasury Approver | only `group_checks_approver` | pass |
| Checks Manager | only `group_checks_manager` | pass |
| Production assets | no `debug=assets`, minified, cold cache | pass |

### What each tour asserts

* **KPI values** — every KPI compared against a fixture whose numbers are
  known (4 on hand, 1 due today, 1 in clearing, 1 cleared, 1 bounced).
* **Drill-down parity** — clicking *Cheques On Hand* opens a list with exactly
  4 rows. Server-side, **every** KPI's and every maturity bucket's domain is
  asserted to return exactly the count it advertises.
* **Charts** — both canvases have real pixels (≥40×40) and neither escapes the
  dashboard box. A collapsed canvas is invisible to a CSS review.
* **Maturity buckets** — all nine present at every viewport, including when
  empty; their counts sum to the on-hand count.
* **PDC coverage** — a real percentage in `[0, 100]`, never a placeholder.
* **Refresh** — clicking it reloads and the dashboard is still intact.
* **Loading / empty / error states** — the spinner, the zeroed company (no
  `NaN`, `undefined`, `Infinity`, `∞`, `[object Object]`; bounce rate reads
  `0.0%` on a zero denominator), and the error banner with its retry button.
* **RTL** — computed `direction: rtl`, and the first KPI card proven to hug the
  **right** edge by geometry rather than by reading a CSS property.
* **No horizontal overflow** — page and container, at every viewport, in both
  directions.
* **No browser console errors** — `HttpCase` fails the tour on any uncaught JS
  error; none occurred.

### Product defects found *only* by the browser

Five, all fixed. Every one would have shipped:

1. **The dashboard failed completely for its own users.** `_coverage_block`
   read `realestate.sale.installment` without elevation, so any Treasury
   Officer without a Developer role got an `AccessError` and an empty
   dashboard. Fixed with a narrow, documented `sudo` on the read.
2. **A Treasury Officer could not present a cheque linked to a sale contract.**
   `_check_company_consistency`, `_compute_project_from_source`,
   `_compute_obligation` and `_invoices_to_settle` all read Developer models as
   the user. Fixed the same way, each with the reason recorded in place.
3. **Confirming the first deposit in a company was refused** — `_bank_location`
   auto-creates the "At Bank" custody location, which only a Manager may
   create.
4. **The bounce wizard predicted the wrong outcome.** Its preview still used
   `is_matched or state == 'paid'`, so it announced the manual-accounting path
   for every ordinary bounce — the opposite of what the bounce then did. A
   wizard that mispredicts is worse than one that predicts nothing.
5. **The dashboard overflowed horizontally by 8px at every viewport.** A
   Bootstrap `.row` carried `p-3`; a row's negative gutter margins sit outside
   its padding box, so the row was wider than its parent and the page scrolled
   sideways.

One usability fix followed from the same work: the Deposit Workbench now
proposes a destination bank (the company's configured cheque method's journal,
else its first bank journal) instead of leaving a required field blank.

---

## 19.3 Terminology verification

Asserted in four places — field and selection labels, the dashboard payload,
view arch and QWeb templates, and the rendered DOM in every browser tour.

The two explicit rules:

* **`PDC received` is never labelled `Collected Revenue`.** The payload label
  is *"PDC Amount Received (Face Value)"*, `kind: 'paper'`, with the note
  *"Paper held against those obligations. NOT cash collected."*
* **`Deposited` is never labelled `Cleared`.** They are distinct selection
  values with distinct labels, and `in_clearing` is a third distinct state
  between them.

The seven concepts stay separate throughout:

| Concept | Where it lives | Marked |
|---|---|---|
| Cheque received | `check_amount_total` — *"Cheques Received (Face Value)"* | paper |
| Cheque amount on hand | `check_amount_on_hand`, KPI *Cheques On Hand* | paper |
| PDC coverage | `pdc_coverage_percent`, `secured_by_checks_amount` | obligation vs paper |
| Deposited | state `deposited` — *"Presented / Deposited"* | paper |
| In clearing | state `in_clearing` — *"In Clearing"* | paper |
| Cleared | state `cleared`, `accounting_state = reconciled` | **cash** |
| Cash collected | **never used as a claim** — only as the disclaimer *"NOT cash collected"* | — |

Only the *Cleared* section carries the `Cash` badge; the browser tours assert
that no other KPI does, by walking every badge in the DOM and checking its
card's label. The dashboard also carries a standing legend: *"Paper is not
cash."*

The PDC Acknowledgement states on its face: *"This document acknowledges
receipt of the physical instruments listed below. It is not a payment receipt:
no payment has been made."* It lists only paper still held — cheques already at
the bank are excluded.

---

## 19.4 Clearance semantics — pinned

> **ATMTA uses Odoo's `account.payment.is_matched` as its accounting clearance
> oracle. It does not use `payment.state == 'paid'`.**

Documented in `models/check.py::_is_cash_confirmed`, in
`tests/test_clearance_semantics.py`, and here.

### Why `state` is not the oracle

Odoo 18's `account_payment.py::_compute_state`:

```python
if (payment.state == 'in_process' and payment.reconciled_invoice_ids and
        all(invoice.payment_state == 'paid'
            for invoice in payment.reconciled_invoice_ids)):
    payment.state = 'paid'
```

and on Community `_get_invoice_in_payment_state()` returns `'paid'`. So a
payment is promoted to `paid` the moment the invoices it settles read paid —
**with no bank transaction anywhere.** Keying clearance off `state` would mark
every cheque cleared the instant it was *presented*: 0.1's error, reintroduced
by a different route. `test_odoo_still_promotes_state_to_paid_without_a_bank`
pins that premise so a future Odoo change is noticed rather than silently
absorbed.

### The nuance, per journal configuration

| Configuration | `is_matched` becomes true | Regression test |
|---|---|---|
| **Outstanding receipts** — inbound method posts to a reconcilable Outstanding Receipts account | only when the liquidity line's residual reaches zero, i.e. **on completion of the bank-side liquidity reconciliation** | `test_outstanding_configuration_waits_for_the_bank` |
| **Direct to bank** — method posts into the journal's default liquidity account | **immediately.** Odoo short-circuits `_compute_reconciliation_status` on `journal.default_account_id in liquidity_lines.account_id`, commented in its own source as *"Allow user managing payments without any statement lines by using the bank account directly"*. There is no separate matching step to wait for, so clearing on presentation is **correct for this configuration** — not a loophole, and not to be "fixed" | `test_direct_configuration_is_matched_immediately_and_that_is_correct` |
| **No outstanding account** — Odoo raises no journal entry | falls back to `state == 'paid'`, the only signal that configuration has | covered by the same helper |

`test_the_two_configurations_diverge_only_on_the_bank_step` asserts both in one
place: same code path, different journals, different timing, by design.

### Structural guarantees

* `state = 'cleared'` is written in **exactly one method**,
  `_sync_clearance_from_accounting`. Asserted by AST parse, attributed to the
  enclosing function, so the invariant tested is *which method may write it*.
* The module **never writes an accounting field.** Asserted by AST parse for
  assignments to, and dict keys named, `payment_state` or `amount_residual`.
* `action_mark_cleared` refuses on an unmatched cheque with a message naming
  the payment, its state, and what to do — *"Marking it cleared here would
  record cash that has not arrived."*

---

## 19.5 Bounce after a bank match — an intentional accounting-control boundary

**This is not automated in Module 3, deliberately, and will not be.**

If a bank statement has already been matched to the payment and the bank later
posts a returned-cheque debit, the original receipt genuinely happened.
Unwinding it automatically would break a reconciliation the accountant has
already agreed, and the correcting entries depend on how the particular bank
posts the return. There is no generic answer that is safe.

What the module does instead, verified in a browser
(`atmta_treasury_matched_bounce_tour`):

1. **Refuses** to modify the ledger. The invoice's residual is untouched and
   the matched payment is not cancelled — asserted server-side.
2. **Explains**, on the record and in the chatter, with a danger ribbon and a
   banner stating *"…the ledger is untouched and awaits the entries above."*
3. **Instructs** — record the bank's returned-cheque debit as a new bank
   transaction; reconcile it against a reversal of the payment, or against the
   customer's receivable directly; the invoice returns to outstanding through
   that reversal.
4. **Blocks re-presentation** until it is resolved, with the reason: doing so
   would register *"the same money twice"*.

The instrument is still marked bounced, so the treasury position is honest even
while the accounting is outstanding.

---

## 19.6 Production assets

| Gate | Result |
|---|---|
| Dashboard JS / XML / SCSS in `web.assets_backend` | pass |
| OWL client action registered exactly once | pass |
| `ir.actions.client.tag` matches the registered key | pass |
| OWL template present in the compiled bundle | pass |
| Chart.js absent from `web.assets_backend` | pass |
| Chart.js loaded lazily from Odoo's `web.chartjs_lib` | pass — the same pattern Module 1 settled on in 0.4 |
| No vendored Chart.js anywhere in the module | pass |
| No import from another ATMTA dashboard's assets | pass — imports restricted to `@web/*` and `@odoo/owl` |
| Production bundle minified, contains the dashboard | pass |
| Source comments stripped from the production bundle | pass |
| Cold cache: assets purged, page loads, bundle rebuilds | pass |
| Dashboard renders with no `debug=assets` | pass — all 12 browser scenarios run without it |

---

## 19.7 Reports verification

All eight registers and both documents.

| Register | Company-scoped | Currency | Content | Filters | Grouping |
|---|---|---|---|---|---|
| Check Register | ✓ | groupable | every instrument | ✓ | by state |
| PDC Maturity | ✓ | groupable | on-hand only — a presented cheque is not a future maturity | ✓ | by bucket |
| Deposit Register | ✓ | own currency | slips | ✓ | by bank |
| Cleared Register | ✓ | groupable | `state = cleared` only | ✓ | — |
| Bounce Register | ✓ | own currency | bounces | ✓ | by reason |
| Replacement Register | ✓ | own currency | both ends of each chain | ✓ | by drawer |
| PDC Coverage | ✓ | own currency | open, uncancelled obligations | ✓ | pivot |
| Customer PDC Statement | ✓ | groupable | per drawer | ✓ | by drawer, state |

* **Company scoping** is asserted by opening every register as a user of one
  company and checking that no record of the other appears.
* **Currency handling** — every analytical register can be grouped by currency,
  monetary columns render in each row's own currency, and the dashboard flags a
  multi-currency scope rather than summing across it.
* **No sensitive bank data reaches unauthorised roles** — see §19.8.

Both QWeb documents render and are asserted for content: the **Deposit Slip**
carries company, bank, reference, date, every cheque number, prepared-by,
confirmed-by and a bank acknowledgement block; the **PDC Acknowledgement**
disclaims being a payment receipt and lists only paper still held.

---

## 19.8 Sensitive-data access

| Data | Sales user (Developer roles, no Treasury) | Checks User | Treasury Officer + |
|---|---|---|---|
| Cheque records | **no access at all** | read/write | full |
| Drawer account number, branch, drawer bank account | — | **stripped from the served arch** | visible |
| Internal treasury notes | — | **stripped from the served arch** | visible |
| Custody movements | **no access** | read only | create via the transfer action |
| Bounce records | **no access** | **read only** — cannot create, and creating one is what bounces a cheque | create/resolve |
| Presentation attempts | **no access** | read only | manage |
| Contract cheque totals | **visible** (aggregate only) | visible | visible |

A Sales user still sees the contract's cheque statistics — that aggregate is
computed with a `sudo` read so a salesperson opening their own contract is never
refused, while gaining no access to any cheque record. Asserted both ways.

Bank account numbers are **not** `tracking=True`, so they are never copied into
permanent chatter messages.

Every Treasury operation is enforced **on the server** before it acts: confirm
deposit, clear, bounce, re-present, replace, cancel, return to customer, custody
transfer, penalty invoice, manual sync. Tested from purpose-built users, not by
reading view XML.

---

## 19.9 Migration result

Verified against legacy-shaped data (§15.4), twice.

**First upgrade — with a deliberate data problem present:**

| Check | Result |
|---|---|
| Upgrade completed | `RC=0` |
| Cheques preserved | 6 / 6, references intact |
| Allocations created | 4 — one per legacy instalment link |
| Allocated total | 400,000 = 4 × 100,000, **no amount duplicated** |
| Opening custody seeded | 6 |
| `cleared` row untouched | 1, and reported as resting on no payment |
| Colliding identity pair | **both rows still present** — nothing merged or deleted |
| Identity index | **not created**, and said so, by cheque reference |
| Version | `18.0.0.2` |

**Retry after Treasury resolved the collision:**

| Check | Result |
|---|---|
| Upgrade completed | `RC=0`, **0 ERROR/CRITICAL** |
| Identity index | **created automatically** |
| Allocations | still 4 — not re-created |
| Opening custody | still 6 — not re-seeded |

The migration is idempotent, reports rather than repairs, and the index appears
by itself once the data is sound.

---

## 19.10 Cross-module compatibility

Fresh install of all 14 modules, every ATMTA test, no pre-existing database.

| Module | Version | State |
|---|---|---|
| `atmta_real_estate` | 18.0.0.4 | installed — **frozen, unchanged** |
| `real_estate_developer` | 18.0.0.3 | installed |
| `real_estate_checks` | **18.0.0.2** | installed — **frozen here** |
| `real_estate_handover` | 18.0.0.1 | installed |
| `real_estate_portal` | 18.0.0.1 | installed |
| `real_estate_api` | 18.0.0.1 | installed |
| `real_estate_brokerage` | 18.0.0.1 | installed |
| `real_estate_construction` | 18.0.0.1 | installed |
| `real_estate_contract_template` | 18.0.0.1 | installed |
| `real_estate_customer_service` | 18.0.0.1 | installed |
| `real_estate_investment` | 18.0.0.1 | installed |
| `real_estate_maquette` | 18.0.0.4 | installed |
| `real_estate_plan` | 18.0.0.3 | installed |
| `real_estate_procurement` | 18.0.0.1 | installed |

Contract-surface compatibility (`test_compatibility.py`, 15 tests): all three
0.1 models, every 0.1 field on cheque / deposit / bounce, the contract
statistics, the three sequences and their prefixes, the module-level constants,
`action_clear_all`, and Developer's late-bound `_blocking_checks` hooks all
still exist and still behave.

---

## 19.11 Remaining deferred items

Unchanged from §18, restated so the freeze is explicit about them.

### Deferred by instruction, not by omission

* **Portal (M26).** Not implemented in this release. The portal exposes no PDC
  data today, and `test_no_portal_route_exposes_cheques` asserts that nothing
  has started exposing it by accident. Publishing cheque data to customers
  requires deciding what a customer may see of a bank account number, a
  treasury note and a bounce reason — a product decision.
* **Bounce after a bank match.** Not automated. Recorded as an intentional
  accounting-control boundary (§19.5).

### Partial, and why

* **Odoo batch payment (M6).** Fully wired but active only when Enterprise's
  `account_batch_payment` is in the registry. `OEEL-1` versus this module's
  `LGPL-3`; on Community every payment is created and reconciled individually
  and no treasury behaviour changes. Both paths tested.
* **Bank charge accounting (M11).** `bank_charge_amount` is recorded and
  reported and `bank_charge_move_id` is the link, but the vendor bill is not
  raised automatically — how a bank fee is booked differs by bank and by chart
  of accounts.

### Localisation-specific, exposed as configuration

Cheque validity periods, early-presentation windows and country-specific
returned-cheque procedure are `check_stale_days`,
`check_allow_early_deposit` and `check_early_deposit_days`, defaulted
conservatively. Nothing is hard-coded; a localisation module sets them.

### Known operational note

Two stored computes can be recomputed during a read: `check.maturity_bucket`
(date-dependent, and stored so the forecast groups in SQL rather than in
Python) and `deposit.payment_method_line_id` (stored and user-editable, so it
back-fills when a journal's methods change). When that happens inside one of
Odoo 18's read-only request transactions, Postgres refuses the `UPDATE`, Odoo
logs a `ReadOnlySqlTransaction` traceback and retries the request in read-write,
and it succeeds. It appears in the full-suite log as two `odoo.sql_db: bad
query` lines with **zero test failures**.

It is log noise, not a defect, and the design is deliberate: both fields are
stored because the alternative — computing maturity in Python across 100,000
cheques on every dashboard load — is the thing M33 forbids. The nightly
`_cron_refresh_maturity` is what keeps the first settled in practice.

---

## 19.12 Freeze

`real_estate_checks 0.2` is **frozen** as of 2026-08-05.

Every gate passed:

- [x] Module 1 frozen baseline — 299, unchanged
- [x] Developer suite — 247, unchanged
- [x] Checks suite — 325
- [x] Handover, Portal, API, Plan/Maquette, Contract Template — install and
      pass with Checks present
- [x] Full ATMTA suite — 871
- [x] Fresh install of all 14 modules
- [x] Upgrade from legacy Checks 0.1 data
- [x] Migration retry and idempotency
- [x] Browser verification — 14 tours, 12 environments
- [x] Production assets — minified, cold cache, no `debug=assets`
- [x] Terminology
- [x] Clearance semantics pinned with regression tests
- [x] Reports and documents
- [x] Sensitive-data access

No further Checks feature work after this point. Portal support is not to be
added before a new release is opened.
