# Commission Share Semantic Migration — Audit Method & Runbook

**Field:** `res.users.commission_share_default`
**Blocking for:** Brokerage 0.4 freeze
**Status:** engine built, tested and verified; **the production audit has not
been run** — see §6.

---

## 1. What changed

| | Brokerage 0.1 | Brokerage 0.4 |
|---|---|---|
| Meaning | **X% of the transaction value** | **X% share of the gross brokerage commission** |
| Applied to | the sale price directly | the agency's fee |
| Stored as | a `Float` on `res.users` | the same `Float`, unchanged |

The number did not move. Its economics did.

An agent stored as `2.5` was owed 2.5% of a sale price. Read under the new
architecture, the same row says they are owed 2.5% of the agency's fee — on a
2% fee that is **0.05% of the sale**, a fiftieth of what was agreed. The
opposite framing is worse: `40`, meaning 40% of the fee, read as 40% of the
sale price is a twenty-fold overpayment.

Nothing is broken in either case. The arithmetic is correct and the input means
something else, which is precisely why every technical test stayed green.

---

## 2. The conversion

```
    old:  X% of transaction value
    fee:  Y% of transaction value        (the gross brokerage commission)

    new share of gross = (X / Y) × 100
```

Worked example, from the brief:

```
    old agent commission        1%   of sale price
    gross brokerage commission  2.5% of sale price

    new share = 1 / 2.5 × 100 = 40% of gross

    on a 1,000,000 sale:
        old:  1,000,000 × 1%          =  10,000
        new:  (1,000,000 × 2.5%) × 40% =  10,000     ✓
```

Monetary equivalence is asserted, not assumed — `verify_equivalence()` on the
runner, and `TestMonetaryEquivalence` across a range of sale prices and
end-to-end through a real commission line.

**The conversion is only deterministic when there is exactly one Y.** An agent
who worked one project at a 2% fee and another at 2.5% has no single correct
answer, and nothing here invents one.

---

## 3. Classification

Every non-migrated user carrying the field is classified from their own
history: the transactions they were lister or selling agent on, plus every
transaction their existing commission lines point at. The gross rate of each is
`commission_gross_amount / sale_price × 100`.

| Class | Condition | Action taken |
|---|---|---|
| `safe` | exactly one distinct historical gross rate, and the conversion lands in 0–100% | converted; original preserved |
| `already_new` | a Brokerage Manager has declared the value already in the new semantics | untouched |
| `ambiguous` | more than one distinct gross rate, **or** history whose only gross rate is zero | value preserved, default **cleared**, review required |
| `unused` | zero default, or a non-zero default with no commission and no transaction anywhere | value preserved, default **cleared**, review required |
| `invalid` | outside 0–100, or a conversion exceeding 100% of the gross | value preserved, default **cleared**, review required |

### `already_new` has to be declared, not detected

An audit cannot tell a `40` that means 40% of gross from a `40` that means 40%
of a sale price — the numbers are identical and only the person who set them
knows which. So `res.users.action_mark_share_already_new(note)` exists:
Brokerage Manager only, reason mandatory, evidenced. A later `run()` then leaves
the value entirely alone rather than dividing it by anything.

### Why anything unconvertible is cleared rather than left alone

Leaving the number in place is the exact failure this migration exists to
prevent. A zero cannot be silently mistaken for an entitlement; `2.5` can. The
original is never destroyed — it moves to
`res.users.commission_share_legacy_value` and is written into the immutable
evidence record.

Clearing it does not make the failure silent: `_default_split_lines` **refuses
by name** for an agent whose default is unresolved, so an operator seeding
splits is told who and why rather than quietly getting one line short.

### Why a conversion over 100% is `invalid` and not capped

3% of the sale against a 2% agency fee is more than the entire fee. The two
legacy numbers cannot both be right, and capping at 100% would invent a figure
nobody agreed to in order to make an inconsistency disappear.

---

## 4. What is blocked, and what is not

Blocked — the commission path only:

- `action_add_default_commissions()` refuses, naming the agents.
- A commission line whose `share_source` is `user_default` and whose agent is
  unresolved cannot be **approved**, **billed** or **paid**.

Not blocked — everything else:

- CRM, opportunities, matching, shortlists, viewings, listings, mandates,
  offers, acceptance, transactions, broker registration.

An unresolved payout default is not a reason to stop somebody selling. A
manually entered share is also unaffected: a number a human typed is a
decision, not an inherited ambiguity.

**Resolution** is `res.users.action_review_commission_share(value, note)` —
Brokerage Manager only, reason mandatory, recorded against the evidence with
the reviewer and timestamp.

---

## 5. Evidence

`realestate.commission.share.migration`, one immutable row per audited user:

| Field | Holds |
|---|---|
| `original_value` / `original_semantics` | the number and what it meant |
| `migrated_value` / `migrated_semantics` | the number and what it means now |
| `gross_rate_used`, `conversion_basis` | the Y it was divided by, and the arithmetic |
| `gross_rates_found` | every distinct rate the history touched |
| `result`, `note` | the classification and why |
| `transaction_count`, `commission_count` | the evidence base |
| `migrated_on`, `migrated_by_id` | when and by whom |
| `reviewed_by_id`, `reviewed_on`, `review_note` | the manual resolution, if any |

`write` refuses everything except the three review fields; `unlink` refuses
outright. Browsable at **Brokerage → Configuration → Commission Share
Migration**.

Individual entitlements carry their own frozen basis —
`snapshot_gross_amount`, `snapshot_share_percentage`, `snapshot_amount`,
`snapshot_method`, `snapshot_taken_on`, `effective_date`, `share_source`,
`source_rule` — so a payout is reconstructable from the line itself, without
reference to whatever the configuration says today.

---

## 6. Running it on production — and what has *not* been done

The engine is built, tested and verified. **The audit has not been run against
a production dataset**, because none exists in this environment: the databases
used for this work are built fresh from the module manifests and contain no
legacy `commission_share_default` values. Every result below §3 was verified
against seeded representative data, not against real agents.

That step is a deployment action and belongs to whoever holds the production
database. It must happen **before the first payout run**.

```python
# 1. AUDIT — reads only, changes nothing.
runner = env['realestate.commission.share.migration.runner']
for row in runner.audit():
    print(row['user'], row['original_value'], row['result'],
          row['gross_rates_found'], row['note'])
```

Review that output. Then:

```python
# 2. RUN — converts what is safe, clears and flags what is not.
runner.run()
```

Then, in the UI:

1. **Brokerage → Configuration → Commission Share Migration** — defaults to the
   records needing review.
2. **Brokerage → Configuration → Agents Needing Review** — the agents whose
   automatic allocation is blocked.
3. Resolve each with a Brokerage Manager, recording the reason.

`run()` is idempotent and safe to re-run: anything already carrying a verdict is
skipped entirely. This is enforced rather than hoped for — an early version
re-read a converted 40% as though it were still a percentage of the sale price,
turned it into 1,600% and discarded it as invalid, silently zeroing a correctly
migrated agent. `test_a_converted_agent_is_not_re_converted` exists because of
that.

---

## 7. Related decision: zero-gross legacy rows

Pre-0.2 commission rows whose transaction has no gross are allowed to survive
the upgrade — that compatibility decision stands. Existing is not the same as
being payable:

- A commission with a zero or undefined gross basis **cannot be approved**,
  billed or paid.
- Unless it carries an explicit **fixed** entitlement greater than zero, which
  is a complete entitlement on its own.
- Correcting the basis — setting the gross from the mandate, the listing terms
  or manually with a reason — unblocks it.

Legacy data may survive. Invalid money movement may not.

---

## 8. Test coverage

| Area | Tests |
|---|---|
| Audit classification | 10 |
| `already_new` declaration | 6 |
| Run, evidence and idempotency | 10 |
| Monetary equivalence | 3 |
| Ambiguous blocking and resolution | 9 |
| Entitlement snapshot | 6 |
| Payout dry run cases A–H | 22 |
| Browser (warning, evidence, splits, payable) | 4 |
