# Wave 11 — the purchase-order bridge. Extensions of native `purchase.order`
# and `purchase.order.line` that reach across five procurement capabilities
# (request, control, sourcing, vendor, award) and belong to no single one of
# them. They lived in `real_estate_procurement` until that shell was emptied;
# Wave 9 recorded them as the one thing the shell still owned.
from . import purchase_order
