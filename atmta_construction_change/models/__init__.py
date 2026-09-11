# Wave 16 — change control. What somebody noticed might change the contract,
# and the priced, negotiated, approved instrument that changes it.
#
# A change event is the notice: raised from an RFI, a submittal, an NCR, a
# site instruction. A change order is the money: lines by impact side, markup,
# revisions, and the approvals it had to clear.
#
# What an approved order *does* -- move budget, alter commitment, record
# revenue, convert a forecast anticipation -- is not here. Those records are
# `real_estate_construction` models, reached through seams, so this module can
# state the whole workflow without owning the ledger it writes to.
from . import change_event
from . import change_order
