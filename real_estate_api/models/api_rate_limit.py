"""Cheap per-key/per-ip rate limiter backed by a small table.

This isn't Redis — it's fixed-window counters in Postgres. For the kind of
traffic a real-estate website draws (read-mostly + sporadic lead submits)
that's plenty. Cron prunes old windows every hour.

Limits are read from ``ir.config_parameter``:

* ``real_estate_api.rate_limit.public.per_minute``  (default 60)
* ``real_estate_api.rate_limit.authed.per_minute``  (default 300)
* ``real_estate_api.rate_limit.interest.per_hour``  (default 5, per IP)
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class ApiRateLimit(models.Model):
    _name = 'realestate.api.ratelimit'
    _description = 'Real Estate API Rate Limit Counter'
    _rec_name = 'bucket_key'

    bucket_key = fields.Char(required=True, index=True)
    window_start = fields.Datetime(required=True, index=True)
    window_seconds = fields.Integer(required=True)
    count = fields.Integer(default=0)

    _sql_constraints = [
        ('bucket_window_uniq',
         'UNIQUE(bucket_key, window_start, window_seconds)',
         'One counter per (bucket, window).'),
    ]

    @api.model
    def _check_and_increment(self, *, bucket_key, limit, window_seconds):
        """Atomically bump the counter for the current window.

        Returns True if still under limit (and the call has been accounted
        for), False if over. Caller is expected to convert False to a 429.
        """
        if not bucket_key or limit <= 0 or window_seconds <= 0:
            return True  # disabled / nonsensical config → don't throttle

        now = fields.Datetime.now()
        # Snap to the window start (UTC seconds).
        window_start = now.replace(microsecond=0)
        window_start = window_start.replace(
            second=(window_start.second // window_seconds) * window_seconds
            if window_seconds < 60 else 0,
            minute=(window_start.minute // (window_seconds // 60)) * (window_seconds // 60)
            if 60 <= window_seconds < 3600 else window_start.minute,
        )
        # Truncate further for hour windows.
        if window_seconds >= 3600:
            window_start = window_start.replace(minute=0, second=0)

        # Upsert pattern — try update, fall back to create.
        self.flush_model()
        self.env.cr.execute(
            """
            INSERT INTO realestate_api_ratelimit
                (bucket_key, window_start, window_seconds, count,
                 create_date, write_date, create_uid, write_uid)
            VALUES (%s, %s, %s, 1, NOW() AT TIME ZONE 'UTC',
                    NOW() AT TIME ZONE 'UTC', %s, %s)
            ON CONFLICT (bucket_key, window_start, window_seconds)
            DO UPDATE SET count = realestate_api_ratelimit.count + 1,
                          write_date = NOW() AT TIME ZONE 'UTC'
            RETURNING count
            """,
            (bucket_key, window_start, window_seconds, self.env.uid, self.env.uid),
        )
        (new_count,) = self.env.cr.fetchone()
        return new_count <= limit

    @api.model
    def _gc(self):
        """Drop windows that have rolled past."""
        cutoff = fields.Datetime.now() - timedelta(days=1)
        self.sudo().search([('window_start', '<', cutoff)]).unlink()
