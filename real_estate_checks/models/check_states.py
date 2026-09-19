# -*- coding: utf-8 -*-
"""The Checks vocabulary, in one place and importable without the ORM.

Every state string in this module is defined here so that the state machine can
be reasoned about — and asserted against in tests — without reading five model
files. Deliberately no `odoo` imports: this is data, and the migration scripts
need it before the registry exists.

**Nothing in `CHECK_STATE` that shipped in 0.1 has been renamed or removed.**
Downstream code (notably `real_estate_developer._blocking_checks`) matches on
these strings, and production rows carry them. New states are added; old ones
keep their exact meaning.
"""

# ---------------------------------------------------------------------------
# The instrument's physical / legal lifecycle
# ---------------------------------------------------------------------------
#: 0.1 values, unchanged: draft, registered, deposited, cleared, bounced,
#: replaced, cancelled. Added in 0.2: in_clearing, returned.
CHECK_STATE = [
    ('draft', 'Draft'),
    ('registered', 'Registered'),
    ('deposited', 'Presented / Deposited'),
    ('in_clearing', 'In Clearing'),
    ('cleared', 'Cleared'),
    ('bounced', 'Bounced'),
    ('replaced', 'Replaced'),
    ('returned', 'Returned to Customer'),
    ('cancelled', 'Cancelled'),
]

#: Physically in the developer's possession and not yet at the bank.
CHECK_ON_HAND = ('draft', 'registered')

#: At the bank, outcome unknown. This is the window in which an accounting
#: payment exists but no cash has been confirmed.
CHECK_AT_BANK = ('deposited', 'in_clearing')

#: The instrument's story is over — for good or ill.
CHECK_TERMINAL = ('cleared', 'replaced', 'returned', 'cancelled')

#: States from which a cheque may be added to a deposit batch.
CHECK_DEPOSITABLE = ('registered',)

#: A cheque in one of these states carries live treasury exposure: it is at the
#: bank, or it failed at the bank and nothing has resolved it yet. Commercial
#: changes must not run underneath one. See `check_exposure.py`.
CHECK_BLOCKING_EXPOSURE = ('deposited', 'in_clearing', 'bounced')

#: Money has genuinely arrived. Historical fact; never rewritten.
CHECK_SETTLED = ('cleared',)


# ---------------------------------------------------------------------------
# The accounting dimension — computed from Odoo, never authored here (Rule 3)
# ---------------------------------------------------------------------------
#: Read off `account.payment` and `account.move`. This is a *view* of Odoo's
#: state, not a second copy of it: nothing in this module ever writes an
#: accounting field to make one of these values true.
CHECK_ACCOUNTING_STATE = [
    ('no_payment', 'No Payment'),
    ('payment_registered', 'Payment Registered'),
    ('in_payment', 'In Payment'),
    ('reconciled', 'Bank Reconciled'),
    ('reversed', 'Reversed'),
]


# ---------------------------------------------------------------------------
# Deposit batches
# ---------------------------------------------------------------------------
#: 0.1 values unchanged; `reconciled` added so a fully bank-matched slip has a
#: terminal state distinct from "we pressed the clear button".
DEPOSIT_STATE = [
    ('draft', 'Draft'),
    ('confirmed', 'At Bank'),
    ('cleared', 'All Cleared'),
    ('partial', 'Partially Bounced'),
    ('reconciled', 'Bank Reconciled'),
    ('cancelled', 'Cancelled'),
]

DEPOSIT_LIVE = ('confirmed', 'partial')


# ---------------------------------------------------------------------------
# Presentation attempts (M9)
# ---------------------------------------------------------------------------
PRESENTATION_STATE = [
    ('presented', 'Presented'),
    ('clearing', 'In Clearing'),
    ('cleared', 'Cleared'),
    ('bounced', 'Bounced'),
    ('cancelled', 'Cancelled'),
]

PRESENTATION_OPEN = ('presented', 'clearing')


# ---------------------------------------------------------------------------
# Allocation (M3)
# ---------------------------------------------------------------------------
ALLOCATION_STATE = [
    ('draft', 'Draft'),
    ('active', 'Active'),
    ('cancelled', 'Cancelled'),
]

#: Only active allocations count against the cheque's face value and against
#: an obligation's cover. Cancelled ones are kept for the audit trail.
ALLOCATION_LIVE = ('active',)


# ---------------------------------------------------------------------------
# Bounces (M10)
# ---------------------------------------------------------------------------
#: 0.1 values, all preserved verbatim — production rows carry them.
BOUNCE_REASON = [
    ('insufficient', 'Insufficient Funds'),
    ('stop_payment', 'Stop Payment Order'),
    ('signature', 'Signature Mismatch'),
    ('closed', 'Account Closed'),
    ('technical', 'Technical / Bank Error'),
    ('post_dated', 'Presented Before Due Date'),
    ('stale', 'Stale / Out of Date'),
    ('amount_mismatch', 'Amount / Words Mismatch'),
    ('other', 'Other'),
]

BOUNCE_RESOLUTION = [
    ('pending', 'Pending'),
    ('re_presented', 'Re-presented'),
    ('replaced', 'Replaced by New Cheque'),
    ('settled_cash', 'Settled by Other Means'),
    ('written_off', 'Written Off'),
    ('legal', 'Referred to Legal'),
    ('cancelled', 'Cheque Cancelled'),
]

BOUNCE_UNRESOLVED = ('pending',)


# ---------------------------------------------------------------------------
# Maturity buckets (M5 / M21)
# ---------------------------------------------------------------------------
#: Treasury concepts. They say when paper matures, not when cash arrives, and
#: nothing derived from them is ever presented as revenue.
MATURITY_BUCKET = [
    ('past_due', 'Past Due'),
    ('today', 'Due Today'),
    ('d7', 'Within 7 Days'),
    ('d30', '8–30 Days'),
    ('d60', '31–60 Days'),
    ('d90', '61–90 Days'),
    ('d180', '91–180 Days'),
    ('d365', '181–365 Days'),
    ('beyond', 'Over 365 Days'),
]

#: Upper bound in days for each bucket, in order. `None` means "everything
#: else". Shared by the field compute and the forecast report so the two can
#: never disagree.
MATURITY_BOUNDS = [
    ('past_due', -1),
    ('today', 0),
    ('d7', 7),
    ('d30', 30),
    ('d60', 60),
    ('d90', 90),
    ('d180', 180),
    ('d365', 365),
    ('beyond', None),
]


def maturity_bucket_for(days_to_due):
    """Days-until-due → bucket key. Single definition, used everywhere."""
    if days_to_due is None:
        return False
    for key, bound in MATURITY_BOUNDS:
        if bound is None:
            return key
        if key == 'past_due':
            if days_to_due < 0:
                return key
            continue
        if days_to_due <= bound:
            return key
    return 'beyond'


# ---------------------------------------------------------------------------
# Custody (M4)
# ---------------------------------------------------------------------------
LOCATION_KIND = [
    ('sales_office', 'Sales Office'),
    ('treasury', 'Treasury'),
    ('safe', 'Safe / Vault'),
    ('branch', 'Branch'),
    ('bank', 'Bank'),
    ('customer', 'Returned to Customer'),
    ('legal', 'Legal'),
    ('archive', 'Archive'),
]

CUSTODY_REASON = [
    ('receipt', 'Received from Customer'),
    ('transfer', 'Internal Transfer'),
    ('deposit', 'Sent to Bank'),
    ('return_from_bank', 'Returned by Bank'),
    ('return_to_customer', 'Returned to Customer'),
    ('legal', 'Handed to Legal'),
    ('archive', 'Archived'),
    ('opening', 'Opening Balance'),
]
