# `real_estate_checks` — Phase 0 Audit

**Date:** 2026-08-05
**Module version audited:** `0.1`
**Scope:** every file in `real_estate_checks/`, every consumer of its models across
the 14-module ATMTA suite, and the Odoo 18 accounting primitives the module
relies on (`account.payment`, `account.move`, `account.batch.payment`).

Everything below is read out of the code as it exists today, not out of the
module description. Where the manifest and the code disagree, the code wins and
the disagreement is recorded.

---

## 0. Size of the thing being upgraded

| | |
|---|---|
| Python files | 8 (822 lines total, including `__init__`/manifest) |
| Models | 3 (`realestate.check`, `realestate.check.deposit`, `realestate.check.bounce`) + 1 inherit (`realestate.sale.contract`) |
| Wizards | 3 |
| Views | 5 XML files |
| Reports (QWeb/PDF) | **0** |
| Cron jobs | **0** |
| Record rules (`ir.rule`) | **0** |
| Tests | **0** |
| Server-side permission checks | **0** |
| Indexes beyond the M2O defaults | **0** |
| Migration scripts | **0** |

The manifest advertises a "Dashboard: due today, due this week, bounced,
on-deposit, cleared". **There is no dashboard.** Those are five saved filters on
the check search view. The manifest also advertises "Auto-generated
account.payment on cash-in" and "reconciliation with account.payment" — both
exist but not at the moment or with the semantics the words imply (§2, §3).

---

## 1. Current lifecycle

### 1.1 `realestate.check.state`

```
draft ──action_register──► registered ──deposit.action_confirm──► deposited
                                                                     │
                                    ┌────────────────────────────────┤
                                    ▼                                ▼
                          action_mark_cleared              bounce.create()
                                    │                                │
                                    ▼                                ▼
                                 cleared                          bounced
```

Declared but **unreachable** states:

* **`replaced`** — no code path ever writes it. `action_cancel` refuses to act on
  it, the list view decorates it, and nothing produces it.
* **`cancelled`** — reachable via `action_cancel` from `draft` / `registered` /
  `deposited` (a deposited cheque can be cancelled as long as its slip is not
  `confirmed` — but a deposited cheque always *has* a confirmed slip, because
  `action_confirm` is the only thing that sets `deposited`. So in practice
  `cancelled` is only reachable from `draft` and `registered`).

Transitions that are missing entirely:

* `bounced → re-presented` (M12). A bounced cheque is terminal. `action_back_to_draft`
  refuses it, `action_cancel` refuses it, and there is no way to deposit it again:
  `deposit.action_confirm` only accepts `('registered', 'draft')`.
* `bounced → replaced` (M13). The two lineage fields exist and are `readonly=True`
  with no action writing them. **Dead fields.**
* `cleared → bounced`. A bank return after clearance cannot be recorded at all
  (`action_open_bounce_wizard` and `BounceWizard.action_register_bounce` both
  require `state == 'deposited'`).
* Anything resembling "in clearing".

### 1.2 `realestate.check.deposit.state`

```
draft ──action_confirm──► confirmed ──action_clear_all──► cleared
                              │                              (if all cleared)
                              └──(a check bounces)──► partial
```

`_recompute_final_state` sets `partial` when *any* check bounced, including when
*every* check bounced. A fully-bounced slip therefore ends its life labelled
"Partially Bounced" and has no terminal state. `action_cancel` is blocked once
any cheque has cleared, and when it does run it only detaches cheques in state
`deposited` — a `bounced` cheque keeps pointing at a cancelled slip.

### 1.3 `realestate.check.bounce`

Not a state machine. A record with a `resolved` boolean. Creating one is what
bounces the cheque — the side effect lives in `create()` (`bounce.py:63-72`),
so writing a bounce row by any route (import, API, a user with the ACL) flips
the cheque's state with no permission check.

---

## 2. Accounting behaviour — when is `account.payment` created?

**Answer: at `action_mark_cleared()`, and only there.** `check.py:207-258`.

```python
def action_mark_cleared(self):
    for rec in self:
        if rec.state != 'deposited': raise UserError(...)
        rec._create_payment_on_clear()
        rec.state = 'cleared'
```

Consequences:

1. **Between deposit and clearance there is no accounting trace whatsoever.** A
   developer with 4,000 cheques at the bank has zero of them visible to
   Accounting. The receivable is untouched, no outstanding-receipts balance
   exists, nothing appears on the bank reconciliation screen.
2. `action_mark_cleared` is a **manual button** (`check_views.xml:68`, gated only
   by `groups="…group_checks_treasurer"` in the XML). Nothing about the bank is
   consulted.
3. The deposit-level `action_clear_all` (`deposit.py:98`) clears **every**
   deposited cheque on the slip in one click. This is the button a treasurer will
   actually press, and it asserts that every cheque in the batch cleared.

The payment is created with `create()` + `action_post()` directly rather than
through `account.payment.register`, so it bypasses the wizard that Module 1's own
`realestate.account.tools.register_payment` uses everywhere else in the suite.
That is why reconciliation has to be attempted by hand afterwards — see §4.

### 2.1 What `_create_payment_on_clear` gets wrong

```python
payment = self.env['account.payment'].create({
    'partner_id': ..., 'partner_type': 'customer', 'payment_type': 'inbound',
    'amount': self.amount, 'currency_id': ..., 'journal_id': ...,
    'date': self.deposit_date or fields.Date.today(),
    'memo': ..., 'company_id': self.company_id.id,
})
payment.action_post()
```

* No `payment_method_line_id`. Odoo will default it, but the module never
  expresses which method a cheque deposit uses — so the "Checks" payment method,
  which is exactly what Odoo ships for this, is never selected.
* `self.journal_id` is silently back-filled from the deposit if unset
  (`check.py:227`) — a **write during a supposedly read-only clear**, and it
  writes even when the deposit belongs to a different company.
* `company_id` is passed but never validated against `journal_id.company_id`.
* No handling for `currency_id != company.currency_id` beyond passing it through.

---

## 3. Payment timing vs. Rule 2 and Rule 4

The user's Rule 2 says a received PDC is not cash. **V1 satisfies Rule 2 by
accident**, because it creates no accounting artefact until someone presses
"Mark Cleared". Nothing marks an installment paid on receipt.

But it violates Rule 4 in the other direction: `deposit created` is not treated
as cash — *nothing at all* is treated as cash until a human asserts it. The
system has no representation of "presented to the bank, awaiting clearance",
which is where a real PDC portfolio spends most of its life.

**Can a check become cleared without any Odoo bank reconciliation?**
**Yes — it always does.** `state = 'cleared'` is set unconditionally on the line
after `_create_payment_on_clear()`, regardless of whether that method managed to
reconcile anything, and regardless of whether any bank statement exists. There
is no code anywhere in the module that reads a bank statement, a
`account.bank.statement.line`, `payment.is_matched`, or `payment.state`.

---

## 4. Reconciliation behaviour

`check.py:260-276`:

```python
receivable_lines = (invoice.line_ids | payment.line_ids).filtered(
    lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled)
if len(receivable_lines) >= 2:
    try: receivable_lines.reconcile()
    except Exception: self.message_post(...)
```

**This code cannot run at all on Odoo 18.**

`payment.line_ids` does not exist. Odoo 17 and earlier declared
`account.payment` with `_inherits = {'account.move': 'move_id'}`, which
delegated `line_ids` to the journal entry. **Odoo 18 removed the delegation**:

```python
class AccountPayment(models.Model):
    _name = "account.payment"
    _inherit = ['mail.thread.main.attachment', 'mail.activity.mixin']
```

Verified against the running registry:

```
has line_ids: False
has move_id:  True
_inherits:    {}
```

So `payment.line_ids` raises `AttributeError`. And because `_try_reconcile` is
called from `_create_payment_on_clear` *after* `payment.action_post()` and
*before* `self.payment_id = payment`, the consequence is:

> **`action_mark_cleared()` raises and the whole transaction rolls back for any
> cheque whose obligation has a posted invoice.** Clearing works *only* for
> cheques that are not linked to an invoiced instalment — i.e. exactly the
> cheques for which reconciliation was never going to matter.

`action_clear_all` on a deposit slip inherits this: one invoiced cheque in the
batch and the entire slip fails to clear.

Three further defects, latent behind the crash:

1. **No partner, company or currency filter.** Once the attribute error is
   fixed, the filter reconciles whatever receivable lines happen to be on those
   two moves.
2. **`except Exception` swallows genuine errors**, including `UserError` from
   Odoo's own reconciliation guards — and, as written, would have swallowed
   nothing useful anyway since the crash happens before the `try`.
3. The invoice is located by `self.sale_installment_id.move_id` or
   `self.rental_payment_id.move_id`. A cheque linked only at contract level
   reconciles against nothing (§6.3).

This is the single most consequential finding in the audit: the module's
headline feature — "Auto-generated account.payment on cash-in… reconciliation
with account.payment" — **has never worked on Odoo 18** for the invoiced case.

---

## 5. Deposit architecture

`realestate.check.deposit` is a flat batch: date, journal, slip ref, one2many of
cheques, three stored monetary totals, a five-value state.

What it does **not** do:

* **No company consistency.** `deposit.company_id` and `check.company_id` are
  independent. There is no `check_company=True`, no `_check_company_auto`, no
  constraint. **A Company A cheque can be attached to a Company B deposit slip
  and posted into Company B's bank journal.** (`action_confirm` performs no
  company comparison at all.)
* **No currency consistency.** `total_amount = sum(checks.mapped('amount'))`
  sums cheques of different currencies into one number displayed in the
  deposit's own currency, which itself defaults to the *user's* company currency
  and is never reconciled with the journal's.
* **No maturity rule.** A cheque due in 2028 can be deposited today. There is no
  early-presentation policy, no configuration, nothing.
* **No payment-method compatibility check** against the journal.
* **No duplicate-deposit protection.** `check.deposit_id` is a plain M2O with no
  constraint; `deposit.check_ids` has a `domain` on the *view*, which is not
  enforcement. Writing `deposit_id` directly (which `deposit_wizard.py:46` does)
  bypasses every check in `action_confirm` except the state test.
* **State test contradicts itself.** `action_confirm` accepts cheques in
  `('registered', 'draft')` while the `check_ids` field domain says
  `('registered', 'deposited')` and the deposit wizard insists on `registered`
  only. Three different answers to the same question.

### 5.1 Does it duplicate `account.batch.payment`?

Partly — and the answer matters for the design.

Odoo 18's `account.batch.payment` exists precisely to group customer cheques into
one deposit and reconcile the batch against a single bank line. It owns:
`journal_id`, `date`, `payment_ids`, `batch_type`, `payment_method_id`,
`amount` / `amount_residual`, a `draft → sent → reconciled` state driven by the
payments' own reconciliation, and a printable batch deposit report.

**But `account_batch_payment` is licensed `OEEL-1` — Odoo Enterprise.** ATMTA is
LGPL-3 and the whole suite currently depends only on Community modules. A hard
`depends` on it would make the suite Enterprise-only and create a licence
conflict.

So the overlap is real but the resolution is not "delete our model":

| Concern | Owner |
|---|---|
| The physical batch of paper: which cheques, whose custody, what maturity, who prepared and who approved, the deposit slip handed to the teller | `realestate.check.deposit` (ATMTA) |
| The accounting batch: grouped `account.payment` records, the outstanding-receipts balance, the single bank line to reconcile against | `account.batch.payment` (Odoo, **when installed**) |

`realestate.check.deposit` keeps existing (Rule 5 requires it — production slips
have IDs). It gains an **optional, late-bound** `batch_payment_id` that is
populated only when `account.batch.payment` is present in the registry. On
Community the deposit still creates individual `account.payment` records and
still reconciles; it just does not get the batch wrapper.

---

## 6. Downstream dependencies

A suite-wide search for `realestate.check`, `realestate.check.deposit`,
`realestate.check.bounce`, `check_id`, `check_ids`, `sale_installment_id`,
`rental_payment_id`, `check_amount*`, `action_mark_cleared`, `action_clear_all`,
`replacement_check_id`, `replaces_check_id` found consumers in exactly three
places outside the module.

### 6.1 `real_estate_developer` — the M6 contract-change guards (P0)

`real_estate_developer/models/contract_amendment.py:409-440`:

```python
def _blocking_checks(self, installments=None):
    if 'realestate.check' not in self.env:            # late-bound, no dependency
        return self.env['realestate.sale.installment'].browse()
    checks = self.env['realestate.check'].search(
        [('sale_installment_id', 'in', target.ids)])
    settled_states = ('deposited', 'cleared', 'collected', 'endorsed')
    return checks.filtered(lambda c: c.state in settled_states ...)
```

Called from three places (`contract_changes.py:64` restructure,
`:150` early settlement, `:301` unit swap) and from
`wizard/contract_changes.py:89,102`.

Four problems, all of which Checks must fix from its own side:

1. **It searches `sale_installment_id` only.** A cheque linked to the contract
   but not to a specific instalment — which the model fully permits, and which
   the *manifest* describes as a feature — is invisible to every guard. So a
   restructuring can silently run underneath a deposited cheque.
2. **`'collected'` and `'endorsed'` are not values of `CHECK_STATES`.** Dead
   strings; harmless today, but they show the guard was written against a
   different state vocabulary than the one that shipped.
3. **`bounced` is not blocking.** Correct in V1 only because a bounce can never
   have produced accounting. Once payment creation moves to deposit
   confirmation (M7), an unresolved bounce *does* carry accounting exposure, and
   this guard would wave it through.
4. **Cancellation and buyer transfer are not guarded at all.**
   `_apply_cancellation` (`contract_changes.py:200`) and `_apply_buyer_change`
   (`:353`) never call `_assert_no_blocking_checks`. A contract can be cancelled
   with 30 live PDCs in the safe, and a buyer can be swapped out while cheques
   drawn on the old buyer's account sit at the bank.

This is why M15 asks for one authoritative Checks-owned API. Developer should
call `get_check_exposure(contract)` and be told the answer, not run its own
`search` against Checks' internals.

### 6.2 `atmta_real_estate` — one compatibility test

`atmta_real_estate/tests/test_compatibility.py:104-106` asserts that if
`realestate.check` exists it carries a `rental_payment_id` field. That field must
survive verbatim.

### 6.3 `real_estate_checks` itself — the contract statistics

`models/sale_contract.py` adds `check_count`, `check_amount_total`,
`check_amount_cleared`, `check_amount_bounced` to `realestate.sale.contract`,
all non-stored, all computed by Python iteration over `check_ids`. Displayed in
`views/sale_contract_views.xml`. These names are part of the public surface and
must keep working.

Note the label problem the user flags in M19/M20: `check_amount_total` is the
face value of paper received. It is **not** money. Nothing in the UI says so.

### 6.4 Not consumers

* **`real_estate_portal` exposes no cheque data at all** (verified by search).
  M26 is greenfield, not a migration.
* `real_estate_handover`, `real_estate_contract_template`, `real_estate_api`,
  `real_estate_brokerage`, `real_estate_construction`, `real_estate_maquette`,
  `real_estate_plan`, `real_estate_investment`, `real_estate_procurement`,
  `real_estate_customer_service` — **no references**.

---

## 7. Concurrency risks

| Risk | Present today? |
|---|---|
| Two users depositing the same cheque into two slips | **Yes.** `check.deposit_id` is a bare M2O; the last write wins, and `action_confirm` only looks at `state`. Two concurrent transactions can both read `registered` and both confirm. |
| Two users clearing the same cheque | Partly guarded: `_create_payment_on_clear` returns early if `payment_id` is set, but that read is not locked, so two transactions can both create a payment. |
| Duplicate cheque numbers | The SQL constraint `unique(company_id, bank_id, check_number, partner_id)` does guard this at the DB level (§9.1). |
| Bulk-create wizard racing itself | **Yes.** `skip_existing` does a `search` and then creates, with no lock. Two users generating cheques for the same contract produce two full sets. |

No advisory locks, no `SELECT … FOR UPDATE`, no partial unique indexes anywhere
in the module.

---

## 8. Company and security gaps

### 8.1 Record rules: none

`security/security.xml` defines three groups and **zero `ir.rule` records**. Every
user who can read `realestate.check` reads **every cheque in every company**,
including the drawer's bank account number. Same for deposits and bounces.

By contrast `real_estate_developer` ships 19 record rules. Checks — which holds
strictly more sensitive data — ships none.

### 8.2 ACLs are too flat

```csv
access_check_user,...,model_realestate_check,group_checks_user,1,1,1,0
access_bounce_user,...,model_realestate_check_bounce,group_checks_user,1,1,1,0
```

`group_checks_user` — the entry-level group — has **create and write on
`realestate.check.bounce`**. Since `CheckBounce.create()` is what bounces a
cheque, any Checks User can bounce any cheque in any company by creating a
record, entirely bypassing the wizard that is gated to Treasurer.

### 8.3 Every gate is a view attribute

`action_mark_cleared`, `action_clear_all`, `action_issue_penalty_invoice` are
protected by `groups="…"` on `<button>` elements. **The methods themselves check
nothing.** Any user with write access — i.e. `group_checks_user` — can call them
over RPC. The user's M31 phrasing is exactly the finding: *buttons disappearing
is not security*.

### 8.4 No company plumbing

* `_check_company_auto` is not set on any model.
* `check_company=True` on no field.
* `journal_id`'s company filter is a **view domain string**, not a constraint.
* The three `ir.sequence` records have no `company_id`, so all companies share
  one `CHK-` counter.

---

## 9. Data-integrity gaps

### 9.1 The uniqueness key

```python
('check_number_bank_uniq', 'unique(company_id, bank_id, check_number, partner_id)', ...)
```

Better than a bare `unique(check_number)`, but:

* **Postgres treats `NULL` as distinct in a unique constraint.** `bank_id` is
  `required=True` so it cannot be null, and so is `partner_id` — so the key does
  hold. But it omits `account_number`, which is the field that actually
  distinguishes two accounts at the same bank held by the same drawer.
* It is a **hard, unconditional** constraint: once a cheque number is used it can
  never be reused for that drawer/bank, even after the original was cancelled or
  returned to the customer. Real developers do reissue.
* Adding `account_number` to the key on a live database will fail if legacy rows
  collide. Migration must **report** duplicates, not `ALTER TABLE` blind (M35).

### 9.2 `check_number` is a `Char` — good, but the wizard destroys it

`check.check_number` is correctly `fields.Char`. But `bulk_check_wizard.py:52-58`
does `int(self.first_check_number.strip())` and then `str(num)`.

**`'000012345'` becomes `'12345'`.** Leading zeros — which the user's M1 calls
out explicitly as meaningful — are destroyed for every cheque the bulk wizard
generates. The wizard also *refuses* any non-numeric first number, so a bank
using `AB-0012` cannot be bulk-loaded at all.

### 9.3 `_compute_project_from_source` can fail to assign

```python
@api.depends('sale_contract_id', 'rental_contract_id', 'sale_installment_id')
def _compute_project_from_source(self):
    for rec in self:
        if rec.sale_contract_id and ...: rec.project_id = ...
        if rec.sale_contract_id and ...: rec.property_id = ...
        if rec.rental_contract_id and ...: rec.property_id = ...
```

There is no `else`. Both fields are **stored computed**. A cheque created with
neither a sale contract nor a rental contract leaves `project_id` and
`property_id` unassigned, which Odoo 18 treats as a compute error. To be
confirmed by test in M1 — but the shape of the bug is unambiguous, and the fix
(initialise both to `False` at the top of the loop) is one line.

The `'project_id' in rec.sale_contract_id._fields` guards are also pointless:
`realestate.sale.contract` has had `project_id` and `property_id` since Module 2
M1. They are noise that hides the missing `else`.

### 9.4 The penalty invoice points at a `product.template`

```python
product = self.env.ref('real_estate_checks.product_check_bounce_penalty', ...)
...
'invoice_line_ids': [(0, 0, {'product_id': product.id, ...})]
```

`data/sequences.xml:22` declares that XML id as **`model="product.template"`**.
`account.move.line.product_id` is a **`product.product`**. Writing a template's
database id into a variant field silently binds the wrong product — or none —
depending on id collision. This is a live bug, not a style issue.

Also: the invoice is created with **no `company_id`**, no `invoice_date_due`, no
link back to the cheque's contract, and is **never posted**. And the penalty
product record lives in `data/sequences.xml`, a file named for something else.

### 9.5 Amount and currency

* `@api.constrains('amount')` rejects `<= 0`. Fine.
* Nothing constrains the cheque's currency against the contract's, the
  instalment's, the deposit's, or the journal's.
* Nothing constrains the cheque's amount against the instalment it claims to
  pay. One cheque of 10 can be attached to an instalment of 1,000,000 and no
  figure anywhere reveals the shortfall.

### 9.6 No allocation model at all

`sale_installment_id` and `rental_payment_id` are single M2Os. Therefore:

* **Can one cheque currently cover multiple obligations?** **No.**
* **Can multiple cheques cover one instalment?** Technically yes — nothing stops
  two cheques naming the same `sale_installment_id` — but nothing sums them,
  nothing validates the total against the obligation, and the bulk wizard's
  `skip_existing` actively assumes one-to-one. There is no `allocated_amount`,
  no `unapplied_amount`, no over-allocation guard.

Both cases in the user's M3 (Case B, Case C) and M18 are unsupported.

---

## 10. State inconsistencies

1. `deposit.action_confirm` accepts `draft` cheques; `deposit_wizard` accepts only
   `registered`; `check_ids`'s view domain says `registered` **or** `deposited`.
2. `check.action_cancel` blocks on `deposit_id.state == 'confirmed'`, but a
   cheque only reaches `deposited` *through* `action_confirm`, so the guard's
   real effect is "you may cancel a cheque sitting on a draft slip" — which then
   leaves a `cancelled` cheque attached to a slip that will happily try to
   confirm it later (`action_confirm` raises, blocking the whole slip).
3. `deposit.action_cancel` returns `deposited` cheques to `registered` but leaves
   `bounced` ones pointing at the cancelled slip.
4. `_recompute_final_state` never produces a terminal state for an all-bounced
   slip.
5. `check.deposit_date` is `related='deposit_id.deposit_date', store=True`, so
   cancelling a deposit (which clears `deposit_id`) also erases the historical
   record of when the cheque was presented.
6. `bounce.create()` writes `check.state = 'bounced'` unconditionally — including
   for a cheque that is `cleared`, `cancelled` or `draft`, if the row is created
   outside the wizard.

---

## 11. Obsolete / dead code

| Item | Status |
|---|---|
| `check.replacement_check_id`, `check.replaces_check_id` | Declared, `readonly`, never written. Dead. |
| `check.state = 'replaced'` | Declared, never reachable. Dead. |
| `bounce.action_mark_resolved` | Sets a boolean; nothing reads `resolved` except a list decoration. |
| `settled_states = (..., 'collected', 'endorsed')` in Developer | Two values that do not exist in `CHECK_STATES`. |
| `'project_id' in rec.sale_contract_id._fields` guards | Always true since Module 2 M1. Noise. |
| `bulk_check_wizard.skip_existing` search | Racy and one-to-one-assuming. |
| Manifest claim: "Dashboard" | Does not exist. |
| `from odoo import api` in `deposit_wizard.py`, `bounce_wizard.py` | `api` unused in `bounce_wizard.py`. |

---

## 12. Migration risks

Production databases contain live cheques, slips and bounces. The upgrade must
preserve every id, reference, link, attachment and chatter thread.

| Change planned | Risk | Mitigation |
|---|---|---|
| Adding `company_id` consistency + record rules | Rows whose `company_id` disagrees with their contract/journal become invisible or raise | Pre-migration script **reports** every inconsistent row by reference before anything is enforced; never auto-reassigns a company |
| Tightening the uniqueness key with `account_number` | Legacy collisions ⇒ `ALTER TABLE` fails, upgrade aborts mid-way | Pre-migration probes for collisions with a `GROUP BY … HAVING count(*) > 1` and reports them; the index is created `WHERE` a validity predicate holds, and only if the probe is clean |
| Introducing `realestate.check.allocation` | Double-counting if legacy `sale_installment_id` is migrated *and* kept as truth | Migrate exactly one allocation per cheque that has `sale_installment_id` **or** `rental_payment_id` set, for the full cheque amount, only when the link is unambiguous; leave the legacy field in place as a compatibility mirror, never as a second source of truth |
| Splitting `state` into instrument + accounting dimensions | Downstream `c.state in ('deposited','cleared')` breaks | **Keep `state` and every current value.** Add states; add a separate computed accounting dimension. No value is renamed or removed. |
| Moving payment creation from clear-time to deposit-time | Historical cheques already `cleared` with a `payment_id` must not be re-processed | The new flow keys off `payment_id` being empty; existing cleared cheques are left exactly as they are and are marked as legacy-cleared in the migration log |
| Adding `custody` history | Existing cheques have no history | Migration writes one opening custody row per live cheque, dated from the cheque's `create_date`, reason "opening balance" — an honest placeholder, not an invented handover |
| `product_check_bounce_penalty` is a `product.template` | Changing the XML id's model orphans existing references | Keep the template record; add a properly-modelled configuration pointing at its `product_variant_id`, and read the variant everywhere |

Note also: **there are no `migrations/` directories in this module at all**, so
`0.1` has never been upgraded in place. The version bump to `0.2` will be the
first migration this module has ever run.

---

## Answers to the eight questions asked in the brief

> **When is `account.payment` currently created?**

Only inside `action_mark_cleared()` → `_create_payment_on_clear()`. Never at
receipt, never at deposit. Triggered exclusively by a human pressing a button.

> **When is the invoice reconciled?**

**Never, on Odoo 18.** `_try_reconcile` dereferences `payment.line_ids`, which
Odoo 18 removed along with `account.payment`'s `_inherits` delegation to
`account.move`. It raises `AttributeError` and rolls back the clear. See §4.

> **What exactly does `cleared` mean today?**

"A user with the Treasurer group pressed a button." Nothing more. It carries no
guarantee that a payment reconciled, that a bank statement exists, or that money
moved.

> **Can a check become cleared without actual Odoo bank reconciliation?**

Yes — and it is the only way it ever happens. A cheque with no posted invoice
clears on a button press, with a posted payment and no bank involvement at all.
A cheque *with* a posted invoice cannot clear at all, because the reconciliation
attempt crashes first (§4). So the module today has exactly two outcomes:
clearance asserted without evidence, or clearance impossible.

> **Can a bounced check restore the original receivable safely?**

The question does not arise in V1, because a cheque can only bounce from
`deposited`, and no accounting exists before `cleared`. So a bounce writes a
state and a chatter message and touches no ledger — safe, but only because the
accounting was never there. The moment payment creation moves to deposit
confirmation (M7), this becomes the module's single most dangerous path and
must be built properly (M10).

Separately: **a bank return after clearance cannot be recorded at all today.**

> **Can one check currently cover multiple obligations?**

No. `sale_installment_id` is a single M2O.

> **Can multiple checks cover one installment?**

Only by accident. Nothing sums them, nothing validates them, and the bulk wizard
assumes one-to-one.

> **Does `realestate.check.deposit` duplicate something `account.batch.payment` should own?**

It duplicates the *accounting* half — grouping payments for a single bank line.
It does **not** duplicate the physical half (custody, maturity, prepared-by /
approved-by, the paper slip). And `account_batch_payment` is Enterprise
(`OEEL-1`) while ATMTA is LGPL-3, so it cannot become a hard dependency. The
resolution is the split in §5.1: keep `realestate.check.deposit` as the physical
batch, integrate with `account.batch.payment` **optionally and late-bound** when
the registry has it.

---

## Architectural conflicts requiring a decision

One was found, and it is resolvable without user input:

**`account.batch.payment` is Enterprise-licensed.** The brief asks to "use that
capability where it fits". A hard `depends` would make the LGPL-3 ATMTA suite
Enterprise-only. Resolution: optional, late-bound integration (§5.1) —
full functionality on Community, batch wrapper added automatically when
Enterprise is present. Documented rather than escalated, because it does not
destroy or block anything.

No destructive conflict was found. Proceeding to M1.
