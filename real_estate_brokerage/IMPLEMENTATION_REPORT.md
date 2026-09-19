# MODULE 4 — `real_estate_brokerage` 0.4 (FROZEN)

## Implementation Report

**Baseline:** 0.1 (undocumented, untested)
**Delivered:** 0.4 — frozen
**Frozen dependencies:** `atmta_real_estate`, `real_estate_developer`,
`real_estate_checks` — all three still at their exact frozen test counts.

---

## 1. Executive summary

Brokerage 0.1 was a working demo with three structural problems, each of which
would have cost money in production.

It ran **a second customer pipeline**. `realestate.lead` duplicated `crm.lead`
in a database where the website, the public API and the customer portal were
already creating `crm.lead` records. Two pipelines meant two answers to "who is
this customer and what do they want", and no way to reconcile them.

It had **no arithmetic behind commissions**. Every commission line computed
itself as a percentage of the sale price, with nothing anywhere holding what the
company had actually earned. Three agents at 2% each billed out 6% of a sale and
no code objected, because there was no figure for them to be inconsistent with.

It had **no security at all**. Not weak security — none. Zero `ir.rule` records,
and no `company_id` on any model in the module. Every listing, offer,
transaction and confidential floor price in the database was readable by every
user who held the Agent group, across every company.

0.3 fixes all three, adds the channel-partner engine the brief asked for, and
does it without modifying a single line of the three frozen modules.

| | 0.1 | 0.3 |
|---|---|---|
| Customer pipeline | two (`crm.lead` + `realestate.lead`) | one (`crm.lead`) |
| `ir.rule` records | 0 | 11 |
| Models with `company_id` | 0 | 9 |
| Negotiation history | overwritten | `realestate.offer.revision` |
| Commission model | N independent % of sale price | one gross → constrained splits |
| Commission approval | none | manager, never your own |
| Clawback | none | reversal + mirrored line |
| Channel partners | none | brokers, agreements, lead protection |
| Matching | none | deterministic, explainable, DB-prefiltered |
| Tests | **0** | **259** (incl. 9 browser tours) |

---

## 2. The central architectural decision

**`crm.lead` is the canonical customer lead and opportunity.**
**`realestate.lead` is bridged and deprecated, not deleted.**

Phase 0 settled this from the code rather than from preference. A suite-wide
search for `realestate.lead` returned 14 hits, **every one of them inside
`real_estate_brokerage`**. Meanwhile `crm.lead` was already being written by the
website, the public API and the portal. One model had external consumers and one
did not; the one with consumers wins.

What was done instead of deleting:

- `realestate.lead` keeps its table, its ids and its `LEAD-00000` references.
- `create()` refuses new records unless the migration context is set, so the
  second pipeline cannot grow while the first one is being adopted.
- Every action on it raises a message naming the replacement.
- `crm_lead_id`, `migration_state` and `migration_note` record what happened to
  each row.
- `crm.lead` gains `re_legacy_lead_id` and `re_legacy_reference`, so history is
  traceable in both directions and documents printed before the migration still
  resolve.

`realestate.viewing` and `realestate.offer` carry **both** `lead_id` and
`crm_lead_id`, mirrored — the new field is filled from the old where the old was
set, and the old is never overwritten when already populated. Nothing that read
0.1's fields breaks.

---

## 3. Migration

`realestate.lead.migration` is an `AbstractModel` with a single `run(limit=None)`
entry point. It classifies rather than guesses, because the failure mode of a
lead migration is silently merging two different people.

| Outcome | When | What is written |
|---|---|---|
| `migrated` | no live opportunity for that contact | a new `crm.lead`, linked |
| `linked` | exactly one live opportunity, same `partner_id` | linked to it |
| `ambiguous` | exactly one live opportunity at the same commercial entity but a **different contact** | nothing linked; flagged |
| `multiple` | more than one live candidate | nothing linked; flagged |
| `skipped` | no `partner_id` at all | nothing linked; flagged |

`ambiguous` deliberately links nothing. Two contacts at one company are usually
two people, and a migration that merges them is not recoverable.

The run is **idempotent**: a second pass over the same rows produces no new
records and no changed links. Verified twice, including a retry after a partial
run.

---

## 4. Matching (M8)

Deterministic, explainable, and pre-filtered in the database.

**Hard constraints become a `search` domain** — availability, transaction type,
named projects, named property types, budget band with a deliberate margin, and
the user's own project authority. Only survivors reach Python. Loading every
property and filtering in Python is the pattern the brief forbids, and the
`limit` is honoured, which a browse-everything implementation would quietly
stop doing.

**Everything softer is scored**, because a buyer who said "3 bedrooms" will
still look at a well-priced 4-bedroom unit, and an engine that hides it is not
helping anybody.

```
MATCH SCORE: 88%
  ✓ Budget      1,000,000 is within budget
  ✓ Bedrooms    3 bedrooms
  ✓ Area        120 sqm
  ✓ Project     Palm Heights
  △ Floor       floor 5, wanted 8+
```

Weights: budget 30, bedrooms 15, area 15, project 15, type 10, location 5,
bathrooms 5, floor 3, view 2. The score is `earned / available`, so **a
criterion the customer did not express drops out of both sides** — a two-line
brief and a ten-line brief are equally satisfiable, and a sparse brief does not
drag every score down. A near miss earns half credit.

A match is a **record**, not a computed list. The question an agent is asked six
months later is "what did you offer them in March?", and a recomputed list
answers a different question. `criteria_json` freezes the reasoning; re-running
the engine updates the score but never overwrites the customer's own response.

No AI, no learned weights, nothing a salesperson cannot repeat to a customer.

---

## 5. Shortlist (M9)

The shortlist is the match record with `shortlisted`, `rank`,
`customer_response` and `rejection_reason`. One list spans **both inventory
channels** — Developer units and external listings — because a customer does not
care which of the company's two supply chains a flat came from. Visualisation
was explicitly out of scope and is not implemented.

---

## 6. Viewings V2 (M10)

Two things were missing from 0.1's four states, and both cost money.

**The lifecycle starts before the appointment exists.** An agent proposes three
units on Tuesday; the customer confirms one on Thursday. 0.1 had nowhere to put
that, so it was either invisible or a fake `scheduled` that corrupted every
no-show statistic. Hence `SUGGESTED`, and a separate `CONFIRMED` for "the
customer has actually said yes to this slot" — the gap between scheduled and
confirmed is exactly where no-shows are born.

**Rescheduling is not editing.** A reschedule now creates the successor and
moves the original to `RESCHEDULED`, linked both ways. `reschedule_count` walks
the chain. "They moved it four times" is a fact worth keeping, and an in-place
edit destroys it.

```
SUGGESTED ─▶ SCHEDULED ─▶ CONFIRMED ─▶ COMPLETED
                │             │
                ├──▶ RESCHEDULED (─▶ new viewing)
                ├──▶ CANCELLED
                └──▶ NO SHOW
```

Terminal states have no exits. Completion **requires** a structured outcome —
"they didn't like it" cannot be reported on. The outcome vocabulary is
deliberately the shortlist's, so a viewing verdict flows back to the match
record without anybody retyping it. It fills a blank response only; **the
customer's own words outrank an agent's summary of them**.

---

## 7. Offers and negotiation (M11)

0.1 stored one `amount`. So `buyer 900k → seller 1.05M → buyer 980k → accepted`
left a single row reading 980,000, and the whole negotiation — who moved, how
far, how many times — was gone.

`realestate.offer.revision` is append-only by construction: `write` refuses to
touch the numbers or the type (a `note` may be added; annotation is not
revision), and `unlink` refuses outright. `amount` on the offer stays the
current position, so nothing that already read it breaks.

Two things 0.1 did quietly that it no longer does:

**It accepted below the owner's floor.** The floor is now the mandate's minimum
where there is one (the signed instruction beats this company's own memo), and
acceptance under it requires a manager's approval — which is the record that
somebody with the standing to do so went back to the owner. Moving back under
the floor **voids an approval given for a higher number**, because that approval
was for a different offer.

**It rejected competing offers with nobody's approval.** Accepting one offer does
end the others, but doing it when a *higher* offer is open, with no reason
recorded, is how a brokerage ends up explaining itself to an owner. That now
requires `action_accept_over_higher(reason)` — manager-only, reason mandatory,
posted to the chatter. The check is a query, not a read of the non-stored
`competing_offer_ids`: a guard that decides whether an owner's best offer gets
binned must not depend on whether a cache happens to be fresh.

A mandate with `negotiation_authority = 'none'` blocks acceptance outright and
**no internal approval substitutes for it** — a manager here cannot grant an
authority the agency was never given.

---

## 8. Transactions and the Developer boundary (M12 / M24)

0.1's `action_close` did this for every transaction, whatever the inventory:

```python
rec.property_id.write({'owner_id': rec.buyer_id.id, 'state': 'sold'})
```

For an **external** listing that is correct. For **internal** Developer
inventory it is Brokerage reaching into the Developer module and declaring a
unit sold behind the back of the reservation engine, the release batches, the
payment plan and the collection schedule. Developer would still have believed
the unit was available and could have sold it a second time.

Internal transactions now **require** a linked `realestate.sale.contract`, whose
property must match, and closing writes only what is genuinely Brokerage's
record: the listing's state, sold date, final price and publication. It does
**not** write `property.state`, `owner_id`, a sale order or an invoice.
Regression-tested directly.

External deals close exactly as they did in 0.1 — the path is untouched.

Cancellation now requires a reason and records whether the deal fell through
**after contracts were signed**. A deal lost the afternoon it was recorded and
one lost after signing are not the same event, and only one of them costs
anybody money.

---

## 9. Commission engine (M13)

The order the brief asked for, implemented in that order:

```
   gross ──▶ splits ──▶ approval ──▶ payable ──▶ accounting ──▶ paid
     │          │           │            │            │           │
  mandate    % of the    manager      vendor       posted     reconciled
  or listing  gross,     signs off     bill        by Odoo    by Odoo
```

The **gross** is computed once, on the transaction, from the mandate the owner
signed (falling back to the listing's own terms). It is overridable, and
overriding it requires a reason, because it is the number every payout is
measured against. A manual gross is not walked over by a later recompute.

A **split** is a share of the gross, not of the sale price. It is arithmetically
impossible to pay out more than was earned. `commission_unallocated` is what the
company keeps.

**Upgrade safety:** the hard constraint is skipped when the gross is zero — a
pre-0.2 transaction whose listing carried no commission terms has no gross to be
measured against, and failing every legacy row on upgrade would teach nobody
anything. The enforcement moves to `_check_billable`, where it matters: **no
money leaves without a gross**, and none leaves while the splits exceed it.

Also fixed: `paid` no longer counts a **reversed** bill as paid. 0.1 did, which
is precisely backwards — a reversal is the accounting record of a payment being
undone.

---

## 10. Approval and clawback (M14)

0.1 let any agent with ordinary write access raise a vendor bill **to
themselves**, on a transaction that had not closed.

Now: manager-only approval, and **never your own** — that is the one control
that stops the payout process being a single person's signature. Billing
additionally requires the transaction to be `closed`; a commission is earned when
the deal completes, not when it is expected to.

`action_clawback(reason)` reverses the vendor bill and creates a mirrored
negative line, so the transaction's arithmetic stops counting the original and
the allocation frees back up. It is manager-only, needs a reason, and works once.

It deliberately **does not** reconcile the reversal against the original
payment or declare anything settled. Module 3 established for the whole suite
that Odoo owns payment truth, and this module does not pretend otherwise. See
§22 for what that leaves a human to do.

---

## 11. Brokers and channel partners (M6)

A broker is a **`res.partner`**, not a new model. They get invoiced, they have a
contact record, and on another deal they may be the buyer; a second identity
would mean reconciling two of everything forever.

What a partner does not have and a broker needs: standing, and its limits.
`broker_state`, a licence with an expiry, and a KYC state. All three are checked
in `_check_may_transact()`, which is asked at agreement activation and at every
registration — an expired licence is the difference between a payable
introduction and an unpayable one, so it is enforced rather than displayed.

Terms live on `realestate.broker.agreement`, per period, not on the partner.
Terms change, and current terms silently reinterpreting deals done under old
ones is a class of dispute worth designing out. One live agreement per broker
per company: two means two answers to what they are owed.

---

## 12. Lead registration and protection (M7)

The register has to settle, months later, a dispute nobody knew was coming.

```
   broker registers ─▶ collision check ─▶ approved ─▶ protected until <date>
                              │
                              ├─▶ already registered (silent about who)
                              ├─▶ already a direct customer
                              └─▶ protection lapsed → free again
```

**Refusing without leaking.** When broker B registers a customer broker A holds,
B is refused and must not learn that A exists. The check runs `sudo()` so the
answer is complete even though the asking broker cannot read the record that
produced it; the holder's identity goes only into `colliding_registration_id`,
which is manager-only. Browser-verified: the rejection notice is on screen and
the competing broker's name is not.

**Matching identity.** Phone first, then email. **Never names** — "Mohamed Ali"
collides with half a city, and a false positive here takes money off a broker
who did nothing wrong.

The phone key is the **last nine digits**, not the E164 form. This was found in
the browser layer: E164 needs a country to normalise against, and a broker
typing a local number into a company with no country set would have defeated
duplicate detection entirely while every test passed. Nine digits survives any
national prefix. The trade-off is a slightly generous match, so
`action_override_duplicate(reason)` gives a broker who was genuinely first a
route back — manager-only, reason mandatory, collision link preserved for review.

`protected_until` is **frozen at approval**. Renegotiating the agreement later
must not silently move a protection already granted. A converted registration
stays protected — converting is the broker doing exactly what the protection is
for, and the commission is claimed against a deal that has not happened yet.

---

## 13. Teams and project authority (M20)

Teams are Odoo's own `crm.team` and `crm.team.member`. No parallel hierarchy.

`crm.team` gains `allowed_realestate_project_ids` and
`realestate_project_scoped`. `_re_allowed_project_ids_for_user()` returns
**`None`** for an unrestricted user rather than an empty list, because conflating
"may see everything" with "may see nothing" is how project scoping ends up
either useless or a lockout.

**On Odoo 18's multi-team behaviour**, which M2 said to verify before relying on
it: multiple memberships are **off by default**, gated on the
`sales_team.membership_multi` config parameter. In the default mono mode, adding
a user to a second team **archives** the first membership. Both modes are
correct here and both are tested; this module neither requires nor enables
multi-membership.

---

## 14. Record rules and multi-company (M20 / M21)

0.1 had no `ir.rule` records and no `company_id` columns. Both now exist.

**Layer 1 — company isolation, global**, applying to everyone including
managers, on all nine Brokerage models that carry a company. Verified with a
*manager* of a rival company, who sees nothing: seniority does not escape
company isolation.

**Layer 2 — project scoping**, on the Agent group, with a permissive manager
rule. Rules in one group are ANDed and rules across groups are ORed, so the
manager rule is a genuine escape from the agent restriction while the global
company rules are ANDed with both — which is the intended shape.

`res.users.re_allowed_project_ids` resolves to *every* project when unrestricted,
so one domain serves both cases. `None` is the right answer for Python and a
useless one for an `ir.rule`, which cannot branch.

---

## 15. Confidential pricing (M22)

`listing.minimum_price` and `offer.floor_price` are manager-only via `groups=`.
`offer.below_floor` is not: an agent must know an approval is needed without
being told the number. Verified in the ORM, through `get_views`, and in a real
browser as an agent.

---

## 16. Attribution (M18)

0.1 could name a source and never tie it to a pound earned.

Spend and performance live on **`utm.source`** — the model M1 froze first-touch
attribution onto — and deliberately **not** on `realestate.marketing.channel`.
The two look interchangeable and are not: the marketing channel carries
`base_url`, "public site where listings are published", and is a *publication*
channel. Portals a listing is advertised on and sources an enquiry arrived
through overlap but are not the same set; hanging cost-per-deal off the wrong
one produces numbers that look right and mean nothing. (This was got wrong first
and corrected — see §24.)

`realestate.transaction.re_source_id` is a **stored related**, because Odoo 18
refuses to `_read_group` on a dotted path, and attribution that cannot be
grouped is attribution nobody will look at.

Re-tagging a lead does not move the credit: `re_first_source_id` is captured
once and never overwritten.

---

## 17. Response SLA (M19)

Measured from creation to the first outbound contact, against a deadline set
from the team's target. The deadline is **set once and never recomputed** — a
deadline that moves is not a deadline.

What counts as a response is two checks, and both matter: `message_type` in
`('email', 'comment')` separates a human message from Odoo's own tracking
notifications, which fire on every stage change; a non-internal subtype
separates a reply to the customer from a note to a colleague. Either check alone
counts something that is not a callback. Both are tested, including that a stage
change does not stop the clock.

`action_log_first_response()` covers a callback made by phone, which leaves no
message behind. An hourly cron moves silently-overdue leads to `breached` so a
report can find them.

---

## 18. Testing

**259 tests**, from zero.

| Area | Tests |
|---|---|
| CRM unification | 22 |
| Migration | 17 |
| Teams | 8 |
| Listings & mandates | 36 |
| Matching & shortlist | 25 |
| Viewings V2 | 22 |
| Offers & negotiation | 26 |
| Transactions & Developer boundary | 13 |
| Commission engine & clawback | 27 |
| Brokers & registration | 33 |
| Security, SLA, attribution | 26 |
| View rendering | 4 |
| Browser tours | 9 |

The view-render tests exercise every view **as an agent as well as a manager**,
because a field behind `groups=` referenced from an `invisible=` expression
loads fine at install and throws when the agent opens the form.

---

## 19. Browser verification

Nine tours in headless Chrome, authenticated server-side (no credentials are
typed anywhere):

| Tour | What it proves |
|---|---|
| `matching_tour` | the brief renders; matching runs from the UI and returns scored rows |
| `explain_tour` | the explanation is prose with per-criterion verdicts, not a JSON dump |
| `floor_hidden_tour` | an **agent** cannot see the owner's floor |
| `floor_visible_tour` | a **manager** can |
| `negotiation_tour` | all three rounds are on screen (0.1 would show one) |
| `registration_tour` | the refusal is visible and the competing broker is not named |
| `commission_tour` | gross, allocated and unallocated all render with real values |
| `developer_notice_tour` | an internal deal states whose the unit is |
| 6 × menu tours | every page the menus point at opens, with no broken values and no horizontal overflow |

**This layer found a defect nothing else did** — see §24.

---

## 20. Frozen modules

Not one line changed in `atmta_real_estate`, `real_estate_developer` or
`real_estate_checks`. No cross-module contract defect was found that could not
be solved inside Brokerage.

The one availability defect Phase 0 identified — 0.1 calling Module 1's legacy
check instead of Developer's richer `_check_available_for_sale()` — was fixed by
**calling the existing Developer method from Brokerage**. Developer's own source
already recorded the gap:

> *"Module 1 is frozen and is also used by Brokerage, so the richer check lives
> here."*

Nothing had ever called it. Now the listing activation path does.

Final clean-install regression: **1,130 tests, 0 failed**, with all three frozen
modules at their exact frozen counts — 299 / 247 / 325.

---

## 21. Access rights added for other modules' models

Brokerage's views reference `property.type`, `realestate.project`,
`realestate.phase`, `realestate.property` and `realestate.sale.contract`. Its
groups had ACLs for none of them, so a Brokerage-only user could not open an
opportunity at all. Read-only ACL rows were added **to Brokerage's own
`ir.model.access.csv`**, granting Brokerage's groups read access to those models.
No frozen module's security file was touched.

---

## 22. Gap classification

### Implemented
CRM unification, legacy bridge and migration; teams and project authority;
listings V2 with inventory type and ownership; owner mandates with frozen terms;
matching, shortlist and viewings V2; offers with revision history, floor
approval and competing-offer control; transactions with the Developer boundary;
the commission engine with approval and clawback; brokers, agreements and lead
registration with collision detection; record rules and multi-company isolation;
confidential pricing; attribution and cost-per-deal; the response SLA; views,
menus and browser verification.

### Partial
- **Marketing channel vs. source.** Spend and performance are on `utm.source`.
  `realestate.marketing.channel` remains 0.1's publication channel and gained
  nothing. Unifying them is a data-modelling decision with migration
  consequences and was not taken unilaterally.
- **Commission accounting.** Bills and reversals are created and posted;
  reconciling a clawback reversal against the original payment is left to
  accounting, per Module 3's Rule 3. This is a deliberate boundary, not an
  oversight — see §23.
- **`res.users.commission_share_default`** is now read as a share of the *gross*
  where 0.1 read it as a percentage of the *sale price*. Existing values will
  mean something different and should be reviewed before the first payout run.

### Deferred
- **Portal.** Out of scope per the brief. `realestate.property.match` and
  `realestate.lead.registration` are shaped for it (customer response, rank,
  broker-facing rejection that leaks nothing) but no portal controller,
  template or route exists.
- **Visualisation of shortlists.** Explicitly out of scope.
- **AI or learned scoring.** Explicitly out of scope; matching is deterministic.
- **Dashboard.** 0.1's placeholder is unchanged. The reporting added here is
  list/group-based.
- **Broker portal access.** Brokers are partners, not portal users; they cannot
  self-serve registrations.

### Localisation-specific
- **Phone matching** uses a nine-digit tail, chosen for Egyptian mobile numbers
  (11 digits with a leading 0). It is country-agnostic in behaviour but its
  collision characteristics were reasoned about for that format. A deployment
  with short national numbering should revisit `_re_phone_key`.
- **Broker licensing and KYC** model the general shape (number, expiry,
  verification state) rather than any specific regulator's requirements.

---

## 23. Unresolved accounting limitations

Stated plainly, as Module 3 required of itself.

**A clawback reversal is not reconciled.** `action_clawback` posts a credit note
against the vendor bill. If the original was already paid, the credit note and
the payment sit unreconciled until somebody in accounting matches them — or
until the amount is recovered from the broker's next payout, which is a business
decision this module does not make. The Brokerage-side state is correct and the
allocation frees up; the ledger shows an open credit until a human acts.

**A cancelled transaction does not reverse commissions automatically.**
Cancelling a deal that already had billed commissions leaves those commissions
in `billed` or `paid`. Clawback is manual and deliberate: a deal collapsing does
not always mean the introduction was worthless, and the decision belongs to a
manager, not to a state transition.

**The gross is not revalued when a sale price changes after closing.** The
computed gross tracks `sale_price`, but a closed transaction whose price is
edited will move the gross under splits that may already be billed. There is no
guard against this. The over-allocation flag will show it; nothing prevents it.

**Multi-currency is not modelled across the gross/split boundary.** All amounts
use the transaction's currency. A broker invoiced in another currency would need
conversion that does not exist here.

---

## 24. What the browser layer found that nothing else did

Two defects, both of which would have hit every user on day one, and neither of
which any Python test caught.

**The opportunity form was completely unopenable.** The Requirements tab
references `re_property_type_ids`, a `many2many_tags` on `property.type`.
Brokerage's groups had no ACL for `property.type`, so loading the form raised
`AccessError` and *nothing rendered* — no notebook, no tabs, no error a user
could act on. The module installed cleanly, 250 tests passed, and the main screen
of the module did not work. Fixed by adding the read ACLs in §21.

**Phone-based duplicate detection did not work.** A collision test passed because
both fixtures shared the default email; the phone never matched at all, because
E164 normalisation needs a country the test company did not have. Tightening the
test to distinct emails exposed it. Fixed by matching on the digit tail (§12).

A third, smaller one: the Matches menu defaulted to a "shortlisted only" filter,
so a new user opening it saw an empty list with no indication why. Removed.

---

## 25. Performance (M29)

- Every counter uses `_read_group`, never one query per record —
  `crm.lead.match_count`, `shortlist_count`, `match.viewing_count`,
  `agreement.registration_count`, `utm.source` performance.
- Matching pre-filters in SQL and honours `limit`; a test asserts the limit is
  respected, which a browse-everything rewrite would fail.
- `_compute_competition` issues one query for the whole recordset.
- Indexes on every field a rule or filter uses: `company_id`, `project_id`,
  `state`, `score`, `shortlisted`, `customer_response`, `protected_until`,
  `customer_phone_key`, `customer_email_normalized`, `re_sla_state`,
  `re_response_deadline`.
- `re_source_id` is stored so attribution can be grouped in SQL.

---

## 26. Backward compatibility

- No field was removed or renamed. No model was deleted.
- `realestate.lead` keeps its table, ids and references.
- `lead_id` on viewings and offers still works; `crm_lead_id` is added beside it.
- `offer.amount` still holds the current position.
- `commission.calculation_method` gains `share`; existing `percentage` and
  `fixed` rows compute exactly as before.
- The external close path is byte-for-byte 0.1's behaviour.
- Two behavioural changes are intentional and flagged: `commission.paid` no
  longer counts a reversed bill (§9), and `commission_share_default` changes
  meaning (§22).

---

## 27. What a deployment should do first

1. Run `realestate.lead.migration.run()` and review everything classified
   `ambiguous`, `multiple` or `skipped`. Nothing was linked for those.
2. Review `res.users.commission_share_default` on every agent — the number now
   means a share of the gross, not a slice of the sale price.
3. Set a gross commission basis on listings or mandates. Transactions with no
   gross cannot bill anybody, by design.
4. Decide whether teams should be project-scoped. Unscoped is the default and
   grants everything.
5. Set `crm.team.re_first_response_hours` per team, or zero it to switch the
   clock off.

---

## 28. Verification summary

| Gate | Result |
|---|---|
| Brokerage suite | 259 passed, 0 failed |
| Full ATMTA clean install | 1,130 passed, 0 failed |
| `atmta_real_estate` | 299 — unchanged from frozen |
| `real_estate_developer` | 247 — unchanged from frozen |
| `real_estate_checks` | 325 — unchanged from frozen |
| Browser tours | 9 passed |
| Views render as manager | all |
| Views render as agent | all |
| Frozen modules modified | none |
| Migration idempotent | verified twice |

---

# 29. FREEZE — Brokerage 0.4

Sections 1–28 describe 0.3, which was accepted technically with one
release-blocking issue outstanding. This section records the closeout.

## 29.1 The blocker: `res.users.commission_share_default`

One stored number changed economic meaning between releases:

| | 0.1 | 0.4 |
|---|---|---|
| `commission_share_default` | **X% of the transaction value** | **X% share of the gross brokerage commission** |

An agent stored as `2.5` was owed 2.5% of a sale price. Read under the new
architecture the same row says 2.5% of the agency's fee — on a 2% fee that is
0.05% of the sale, **a fiftieth** of what was agreed. Read the other way, `40`
meaning 40% of the fee becomes 40% of the sale price: a twenty-fold
overpayment.

Nothing was broken in either direction. The arithmetic was right and the input
meant something else, which is exactly why 259 tests stayed green through it.

The architecture was **not** reverted. `commission_share_default` now means a
default share of gross, and the chain remains

```
TRANSACTION VALUE → GROSS → SPLITS → ENTITLEMENTS → APPROVAL → PAYABLE → ACCOUNTING
```

Full method, classification rules and production runbook:
**`COMMISSION_MIGRATION_AUDIT.md`**.

## 29.2 Deterministic conversion

```
    new share of gross = (X / Y) × 100          Y = gross brokerage rate

    1% of sale ÷ 2.5% fee × 100 = 40% of gross

    on 1,000,000:   old 1,000,000 × 1%           = 10,000
                    new (1,000,000 × 2.5%) × 40% = 10,000   ✓
```

Monetary equivalence is asserted rather than assumed — as a formula, across a
range of sale prices, and end-to-end through a real commission line to a real
vendor bill (payout dry run Case C).

A conversion landing above 100% of the gross is classified **invalid**, not
capped. 3% of the sale against a 2% fee is more than the entire fee; the two
legacy numbers cannot both be right, and capping would invent a figure nobody
agreed to.

## 29.3 Ambiguous records

Determinism requires exactly one historical gross rate. An agent whose history
spans 2% and 2.5% has no single correct answer, and none is guessed.

For every record that is not safely convertible:

- the original value is preserved on `commission_share_legacy_value` and in the
  immutable evidence record;
- `commission_share_default` is **cleared to zero** — leaving the number in
  place is the precise failure this migration exists to prevent, and a zero
  cannot be silently mistaken for an entitlement;
- a warning appears on the agent's own form, on the record that fixes it;
- automatic allocation refuses **by name**, so an operator is told rather than
  quietly given one line fewer;
- resolution is `action_review_commission_share(value, note)` — Brokerage
  Manager only, reason mandatory, reviewer and timestamp recorded.

There is also `action_mark_share_already_new(note)`, for a database whose
defaults were already set in the new semantics. It has to be *declared* rather
than detected: an audit cannot tell a `40` meaning 40% of gross from a `40`
meaning 40% of a sale price, so only a human who knows can say — on the record,
with a reason. A later run then leaves the value entirely alone.

## 29.4 Payout blocking behaviour

Blocked — the commission path only:

| Action | Blocked when |
|---|---|
| `action_add_default_commissions` | any agent on the deal has an unresolved default |
| `action_approve` | the line's share came from an unresolved default |
| `action_approve` | the gross is zero and there is no explicit fixed entitlement |
| `action_create_vendor_bill` | same two conditions |
| `action_mark_paid` | same two conditions |

Not blocked: CRM, opportunities, matching, shortlists, viewings, listings,
mandates, offers, acceptance, transactions, broker registration. An unresolved
payout default is not a reason to stop somebody selling. A manually entered
share is also unaffected — a number a human typed is a decision, not an
inherited ambiguity.

The zero-gross compatibility decision stands: pre-0.2 rows survive the upgrade.
Existing is not the same as payable. Legacy data may survive; invalid money
movement may not.

## 29.5 Entitlement snapshots

An entitlement is a promise made on a particular day on a particular basis, so
it stops depending on configuration that may move afterwards. Frozen at
creation and re-frozen at approval: `snapshot_gross_amount`,
`snapshot_share_percentage`, `snapshot_amount`, `snapshot_method`,
`snapshot_taken_on`, `effective_date`, `share_source`, `source_rule`,
`source_agreement_id`.

Once approved, `amount` reads the snapshot. Editing a closed deal's price no
longer restates what an agent was already told they had earned, nor disagrees
with a vendor bill already raised. Before approval the figure still tracks the
gross — it is a working number, not yet a promise.

## 29.6 Owner-mandate authority — intentional business behaviour

**Where an active listing mandate reserves offer acceptance to the property
owner, Brokerage Manager approval does not override it.**

This is deliberate and is not a permission gap. A manager's approval is an
*internal control*; a mandate reserving acceptance is a *contractual limit on
the agency's authority*. One cannot grant the other — nobody inside the agency
can confer an authority the owner never gave it. Retained under regression test
in two places (`TestOwnerFloor`, `TestOwnerMandateAuthority`), including the
case where a manager approves the below-floor price first and is still refused.

## 29.7 Defects found during closeout

Three, none of which the 0.3 suite would have caught.

**Double conversion on re-run.** `run()` accepts an explicit recordset and so
could reach a user the candidate filter would have excluded. A second pass read
a converted 40% as though it were still a percentage of the sale price, divided
by the gross rate again to get 1,600%, and discarded it as invalid — silently
zeroing a correctly migrated agent. Anything already carrying a verdict is now
refused at the top of the audit.

**The clawback reversal was left in draft.** `_reverse_moves` produces an
unposted credit note. A clawback that produces a draft document has not put the
exposure on the ledger at all; it sits in somebody's drafts until noticed. The
reversal is now posted. Reconciling it against the original payment is still
deliberately left to accounting.

**Over-allocation was compared against a gross of zero.** That is not a
statement about anything, and it blocked the one case the brief requires to
work — an explicit fixed entitlement on a legacy row with no gross.

## 29.8 A trap left in place, and pinned

`transaction.py` still carries 0.1's `action_add_default_commissions`, which
reads `commission_share_default` straight into a `percentage` line — a
percentage of the *sale price*, the exact defect this migration undoes. It is
shadowed at runtime by the override in `commission_v2.py`, and that shadowing
is import-order dependent.

It was left in place rather than deleted at freeze time, and pinned by
`TestLegacySeedingIsShadowed` instead: the test asserts that seeding produces a
40% share of a 20,000 gross (8,000) and **not** 40% of the sale price
(400,000). If an import order ever changes, that fails loudly rather than
quietly paying somebody fifty times too much.

## 29.9 UI added for the closeout

- Agent form: warning banner, migration state, preserved legacy value, link to
  the evidence.
- **Brokerage → Configuration → Commission Share Migration** — the evidence,
  defaulting to records needing review.
- **Brokerage → Configuration → Agents Needing Review**.
- The inline commission list on the transaction predated the gross/split
  architecture: it showed a percentage of the sale price and a "create vendor
  bill" button with no approval anywhere. It now carries the share, the state,
  the snapshot and a manager-only approve action, and the bill button appears
  only once a line is approved.

## 29.10 Browser results

19 tours in headless Chrome across the surfaces the closeout brief names.
All pass. None type credentials — `start_tour` authenticates server-side.

| Surface | Verified |
|---|---|
| Opportunity form | requirements render with real values |
| Matching | runs from the UI, returns scored rows |
| Match explanation | prose with per-criterion verdicts, no raw structure |
| Listing | confidential floor hidden from an agent, visible to a manager |
| Mandate | list opens, no broken values |
| Viewing | reachable, renders |
| Offer | all three negotiation rounds on screen |
| Transaction | Developer-authority notice shown on internal inventory |
| Commission | gross, allocated and unallocated all render |
| Commission split | multiple splits with state and share |
| Payable workflow | a billed split shows it has reached a payable |
| Broker registration | refusal visible, competing broker never named |
| Duplicate detection | both claims listed, one refused with its reason |
| Commission migration | warning on the agent record; evidence browsable |
| Dashboard | 0.1's placeholder renders without breaking |
| RTL | genuine Arabic session, asserted from computed direction |
| Tablet (768×1024) | listing list and transaction form, no horizontal overflow |
| Multi-company | second company's listings absent |
| Restricted Sales Agent | every reachable page renders without AccessError |
| Brokerage Manager | every page renders |
| Production assets | minified bundle carries the dashboard; cold-cache load |

Two browser-layer notes worth keeping. Odoo sets text direction on
`.o_action_manager`, **not** on `<html>` — the root element carries no `dir`
attribute at all, so an RTL assertion made there reads "ltr" in a perfectly
good Arabic session and proves the opposite of what it appears to. And a
monetary field in edit mode renders as an `<input>` whose `innerText` is empty
however full the field is; a tour reading only the text makes an assertion that
looks strict and passes on nothing.

## 29.11 Cross-module compatibility

- Frozen modules **unmodified**: `atmta_real_estate`,
  `real_estate_developer`, `real_estate_checks`.
- Frozen test counts held exactly: **299 / 247 / 325**.
- Full 14-module fresh install from an empty database: **1,223 tests, 0
  failed, 0 errors**, all 14 modules installed.
- `real_estate_api` and `real_estate_portal` **install cleanly alongside** —
  both read and write `crm.lead`, which this module now owns as the single
  customer spine. Stated precisely: neither ships a test suite of its own
  (`tests/` is empty in both), so "compatible" here means they install, their
  views build and their `crm.lead` extensions coexist with Brokerage's — not
  that their behaviour is covered by tests. Building suites for them is outside
  this module's scope and is the obvious next candidate.
- Legacy upgrade verified on a dataset carrying legacy leads, legacy user
  defaults and legacy percentage commission rows simultaneously.
- Both migrations — CRM lead and commission share — are idempotent, verified by
  running each twice and asserting no new records and no changed links.

## 29.12 Final test totals

| Suite | Tests |
|---|---|
| `real_estate_brokerage` | **352** |
| `atmta_real_estate` | 299 — frozen, unchanged |
| `real_estate_developer` | 247 — frozen, unchanged |
| `real_estate_checks` | 325 — frozen, unchanged |

Closeout added 93 tests to 0.3's 259: commission-share migration (41), payout
dry run A–H (22), entitlement snapshots (6), legacy upgrade (6), asset gates
(8), closeout browser tours (10).

## 29.13 Remaining deferred items

Unchanged from §22–23 and still true at freeze:

- **Portal** — out of scope. The match and registration models are shaped for
  it; no controller, template or route exists.
- **Dashboard** — 0.1's placeholder. Verified to render; not rebuilt.
- **Shortlist visualisation**, **AI/learned scoring** — explicitly out of scope.
- **Broker portal access** — brokers are partners, not portal users.
- **Clawback reconciliation** — the reversal is created and posted; matching it
  against the original payment is accounting's, per Rule 3.
- **Automatic commission reversal on cancellation** — clawback stays manual and
  deliberate; a collapsed deal does not always mean the introduction was
  worthless.
- **Gross revaluation after closing** — the over-allocation flag surfaces it;
  nothing prevents it. Approved entitlements are now protected by the snapshot.
- **Multi-currency across the gross/split boundary** — all amounts use the
  transaction's currency.
- **The production commission-share audit has not been run.** No production
  dataset exists in this environment. The engine is built, tested and verified
  against seeded representative data; running it against real agents is a
  deployment action and **must happen before the first payout run**. Runbook in
  `COMMISSION_MIGRATION_AUDIT.md` §6.

## 29.14 Freeze

Module 4 is frozen at **0.4**.

The frozen regression gate for anything that follows:

```
    atmta_real_estate        299
    real_estate_developer    247
    real_estate_checks       325
    real_estate_brokerage    352
```

No further Brokerage feature work.
