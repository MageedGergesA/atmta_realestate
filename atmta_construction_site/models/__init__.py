# Wave 18 — site execution. What was planned to be built, what was actually
# built, and what it consumed.
#
# The bill of quantities is the measured scope. Milestones and tasks are the
# plan. Daily reports are the record: work done, labour and equipment on site,
# deliveries received, delays suffered. Cost lines are what it consumed.
#
# One relation points upward into the contractual clock: a daily report that
# records a delay raises a delay event, which belongs to claims. That is a
# declared dependency, not a seam, because the row has to point at a real
# record.
#
# All of it is measurement rather than money. What turns a measured quantity
# into a certified amount is a payment certificate, and those stay in
# `real_estate_construction`, reached through a seam.
from . import milestone
from . import construction_task
from . import boq
from . import cost_line
from . import daily_report
from . import labor_log
