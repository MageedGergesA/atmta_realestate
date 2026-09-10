# Wave 13 — the commercial floor. A contractor, and the package that is the
# agreement with one of them for one scope.
#
# These two are the most-referenced models in the construction domain: 20 and
# 25 inbound files. Everything above them -- documents, quality, site
# operations -- points at them with Many2one fields, and a Many2one cannot
# point upward. That is why they had to come down before any capability above
# could be extracted, and why seams, which work for behaviour, could not have
# substituted for the move.
#
# What derives from models further up (variations, extensions of time, claims,
# milestones, payment certificates, retention) is declared here and computed
# through seams that answer neutrally. `real_estate_construction` supplies the
# relations and overrides the seams.
from . import contractor
from . import contract_package
from . import purchase_order_contract
