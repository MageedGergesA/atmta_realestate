# Construction — Upgrade and Operations

Operational runbook for `real_estate_construction` **18.0.1.0.0 (frozen)**.

Final gate: fresh install and full in-place upgrade of all fourteen modules,
both RC=0 at **2099 tests, 0 failed, 0 errors**; Construction **563 tests**;
6 HOOT unit tests; 4 browser tours on minified production assets; legacy
migration idempotent; integrity audit CRITICAL 0 / HIGH 1 / MEDIUM 1 / LOW 0.

This document assumes you are upgrading a live database that has been running
an earlier version of this module. Read it before you start, not while the
upgrade is running.

---

## What this upgrade does, and what it deliberately does not

**It does:** add tables and columns, add 94 record rules, classify legacy data
and write a classification report to the log.

**It does not:**

- create a budget baseline where the legacy sources disagree;
- rewrite any posted accounting entry, including pre-M7 retention postings;
- correct historic over-certification;
- populate project teams (which would activate access restriction on every
  project on the day of the upgrade);
- invent forecasts, claims, extensions of time, NCRs, RFIs or document
  revisions from ambiguous legacy data.

Where the data does not support a determination, the upgrade says so in the
log and leaves the record alone. That is a deliberate choice: a plausible
guess that reaches a cost report is far more expensive than a gap somebody has
to fill in.

---

## Before the upgrade

### 1. Backups — both of them

```bash
pg_dump -Fc -U odoo -h <host> <database> > pre_upgrade_<database>_<date>.dump
tar czf pre_upgrade_filestore_<date>.tgz ~/.local/share/Odoo/filestore/<database>
```

A database backup without its filestore restores a system whose attachments —
drawings, transmittal payloads, claim evidence — are gone. Take both.

### 2. Record what you are upgrading from

```bash
psql -U odoo -h <host> -d <database> -Atc \
  "SELECT name, latest_version FROM ir_module_module
   WHERE name LIKE 'real_estate%' OR name = 'atmta_real_estate' ORDER BY name"
```

Keep the output. Rollback means restoring the backup *and* the matching code.

### 3. Run the integrity audit on the current database

```python
env['realestate.construction.integrity.audit'].run()
```

Findings at `critical` should be understood before you upgrade, not after.
The audit is read-only and can be run as often as you like.

### 4. Finance review — legacy retention

Certificates posted before M7 recorded retention as a negative expense line,
so construction cost was understated by the retention held. The upgrade
**quantifies this and stops.**

```python
audit = env['realestate.construction.integrity.audit'].run()
[f for f in audit['findings'] if f['key'] == 'legacy_retention']
```

The finding reports the certificate count and the exact understatement.
Reclassifying it is a Finance decision with a period, a journal and an
approver attached to it — see the Finance checklist below. **Do not** rewrite
the posted entries.

### 5. Budget classification review

```python
env['realestate.construction.budget.migration'].classify()
```

Each project is returned with `status`:

| Status | Meaning | Action |
|---|---|---|
| `deterministic` | One source, or several that agree | A cost controller can create a draft budget from it |
| `ambiguous` | Sources disagree | **Somebody must choose.** No baseline is created |
| `legacy_only` | A baseline already exists | Nothing to do |
| `no_source` | No expected budget, milestones or BOQ | Nothing to classify |

### 6. Company and project mapping review

Legacy records have no company. The upgrade derives one **only** where the
project makes it deterministic; anything else is logged as `AMBIGUOUS` and
left unset. Review those before going live — a record with no company is
visible to nobody once the global rules are in force.

---

## Running the upgrade

```bash
python3 odoo-bin -c <config> -d <database> \
  -u atmta_real_estate,real_estate_developer,real_estate_checks,\
real_estate_brokerage,real_estate_handover,real_estate_portal,real_estate_api,\
real_estate_construction,real_estate_contract_template,\
real_estate_customer_service,real_estate_investment,real_estate_maquette,\
real_estate_plan,real_estate_procurement \
  --stop-after-init --log-level=info 2>&1 | tee upgrade_<date>.log
```

Upgrade the whole suite, not `real_estate_construction` alone: the module
depends on the others and the load order matters.

### Migration scripts that will run

| Script | When | What |
|---|---|---|
| `18.0.1.0.0/post-migrate.py` | after models load | classifies company, budget, retention, certification; logs counts |
| `18.0.1.0.0/end-migrate.py` | after every dependency | runs the integrity audit read-only and logs findings |

### Log lines to look for

```
grep -E "MIGRATED|DETERMINISTIC|AMBIGUOUS|NEEDS_FINANCE_REVIEW|LEGACY_VALID" upgrade_<date>.log
grep "Construction migration classification" upgrade_<date>.log
grep "Construction integrity" upgrade_<date>.log
```

`AMBIGUOUS` and `NEEDS_FINANCE_REVIEW` lines are **expected** on a legacy
database. They are work items, not failures.

### Expected warnings

- `bad query: … duplicate key value violates unique constraint …` during the
  test run — these are the constraint tests proving the guards work.
- Budget classification warnings for projects whose sources disagree.
- Retention reclassification warning if pre-M7 certificates exist.

**Duration** depends entirely on data volume and hardware. Time it on a
restored copy of production first; no number from a development machine means
anything for yours.

---

## After the upgrade

Work through this in order. Stop at the first item that fails.

### 1. Integrity audit

```python
report = env['realestate.construction.integrity.audit'].run()
report['by_severity']
[f['key'] for f in report['blocking']]
```

`blocking` must be empty. Anything in it concerns money, company isolation or
authoritative history.

### 2. Control Tower

Open **Construction → Control Tower**, select a live project, and confirm:

- the cost cards carry figures, and the freshness line shows both the data
  date and the forecast date;
- the health status has sentences under it;
- the cost sheet lists cost codes and the rows add up to the cards;
- an unforecast cost code reads `N/A` and not `0.00`;
- a drilldown opens the records behind the number.

### 3. Cost sheet reconciliation, one project

Pick a project with posted cost and check by hand:

```
Current Budget      = Original Budget + Approved Budget Changes
Current Commitment  = Original Commitment + Approved Commitment Changes
EAC                 = Actual + ETC
Forecast Variance   = Current Budget − EAC
```

### 4. Accounting actual

Take one cost code, drill into the analytic lines, and confirm the total
matches the cost sheet. Confirm tax is **not** included.

### 5. One certificate, end to end

Raise a certificate on a test project with retention, certify it, create the
bill, and confirm:

- construction actual increases by the **gross** certified amount;
- the retention appears as a credit to the retention liability account;
- the retention register shows the amount held;
- releasing retention does not change actual cost.

### 6. One project per company

With multiple companies active at once, confirm each project reports its own
figures and no other company's records appear anywhere.

### 7. Security roles

Confirm a site engineer cannot open the Claim register, and a cost controller
can open the cost sheet but not claim assessments.

---

## Rollback

There is **no database downgrade.** Rolling back means restoring what you
backed up.

```bash
dropdb -U odoo -h <host> <database>
createdb -U odoo -h <host> -T template0 <database>
pg_restore -U odoo -h <host> -d <database> pre_upgrade_<database>_<date>.dump
rm -rf ~/.local/share/Odoo/filestore/<database>
tar xzf pre_upgrade_filestore_<date>.tgz -C ~/.local/share/Odoo/filestore/
git checkout <pre-upgrade-commit>
```

Restore the filestore and the code to the same point as the database. A
restored database running new code is not a rollback.

---

## Finance checklist

Before the first certificate is raised on the upgraded system:

- [ ] **Contractor Retention Account** configured (Settings → Accounting →
      Construction). Certificates refuse to post retention without it, by
      design — an unconfigured account fails loudly rather than posting to the
      expense account.
- [ ] **Contractor Advance Account** configured.
- [ ] **Owner Retention Receivable** and **Owner Advances Received**
      configured if owner-side billing is used.
- [ ] Purchase tax configuration confirmed. Control figures are
      **tax-exclusive**; tax analytic lines are excluded deliberately.
- [ ] Journals mapped per company.
- [ ] One certificate posted on a test project and the entry inspected:
      `Dr expense (gross) / Cr retention payable / Cr accounts payable (net)`.
- [ ] One retention release posted and inspected:
      `Dr retention payable / Cr accounts payable`.
- [ ] **Legacy retention reclassification decided.** The audit gives the
      amount; Finance decides the period, the journal and whether to
      reclassify at all. The module does not post it.
- [ ] Construction actual reconciled to the ledger for one project.

## Commercial checklist

- [ ] Contract packages carry original and current contract values.
- [ ] Approved variations reconcile to the change orders behind them.
- [ ] Certificate balances agree with the vendor bills.
- [ ] Advances show the correct outstanding balance.
- [ ] Retention register balance agrees with the liability account.
- [ ] Open claims reviewed; claimed, assessed, determined and settled figures
      all present where applicable.
- [ ] Extensions of time reviewed; **original completion dates unchanged**.
- [ ] Current completion dates equal original plus approved extensions.

## Project controls checklist

- [ ] Every live project has a baselined budget, or an entry on the ambiguous
      list with a named owner.
- [ ] Commitment reconciles to purchase orders and awarded packages.
- [ ] Actual reconciles to the ledger.
- [ ] Forecast coverage reviewed; uncovered cost codes are either forecast or
      explicitly marked as needing none.
- [ ] EAC and variance reviewed with the project manager.
- [ ] Unassigned commitment and unassigned actual worked down.
- [ ] Stale forecasts refreshed.
- [ ] Approved-but-unimplemented changes either implemented or explained.

## Security checklist

- [ ] Users assigned to the right construction group: User, Cost Control / QS,
      Commercial / Claims, or Manager.
- [ ] Company assignments correct for every user.
- [ ] **Project teams**: leaving `Construction Team` empty keeps a project
      visible company-wide, exactly as before. Naming anybody restricts it.
      Decide this per project; it is not all-or-nothing.
- [ ] A site engineer confirmed unable to read claims.
- [ ] Accounting drilldown confirmed to respect accounting rights: a cost
      controller without accounting access sees the aggregate and gets an
      access error on the drilldown. That is correct behaviour.
- [ ] Legacy dashboard confirmed unreachable except in developer mode.

---

## Operating notes

### The legacy dashboard is deprecated

**Construction → Control Tower** is the canonical management surface. The old
dashboard aggregated across every project and company, derived "budget" from
milestone amounts and "actual" from manual cost lines, and therefore
contradicts the authoritative definitions. It is retained for one compatibility
release behind developer mode and will be removed.

### Configuration that is deliberately yours to set

| Parameter | Default | What it governs |
|---|---|---|
| `forecast_stale_days` | 35 | When a forecast is called stale |
| `forecast_variance_attention_pct` | 2.0 | Health: attention threshold |
| `forecast_variance_risk_pct` | 5.0 | Health: at-risk threshold |
| `forecast_coverage_min_pct` | 90.0 | Health: coverage threshold |
| `rfi_overdue_attention` | 3 | Health: information threshold |
| `ncr_high_attention` | 2 | Health: quality threshold |
| `high_risk_attention` | 3 | Health: risk threshold |
| `change_approval_sla_days` | 30 | Change ageing SLA |
| `risk_scale_maximum` | 5 | Probability and impact scale |
| `self_approval_limit` | — | Maker/checker limit on change orders |
| `allow_self_determination` | False | Whether a claim author may determine it |
| `allow_self_approval` | False | Whether NCR disposition may be self-approved |

Notice periods, retention percentages and advance recovery rates are **per
contract**, on the package — nothing assumes a standard form.

### Things this module will refuse to do

These are design decisions, not bugs:

- post retention without a configured liability account;
- certify beyond the authorised BOQ quantity or the contract value;
- recover more advance than was paid;
- release more retention than is held;
- approve a forecast with an unanswered line;
- move a completion date except through an implemented extension of time;
- treat a claim, a risk or a change event as authorised money.
