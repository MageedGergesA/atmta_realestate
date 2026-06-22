"""Allow API clients to pick the target database via an HTTP header.

Odoo's default ``Request._get_session_and_dbname`` resolves the database
from (a) the session cookie or (b) a single-DB shortcut when only one
database matches ``dbfilter``. For a public REST API with no cookies,
that means multi-DB hosts force clients into the database picker page
or to set up subdomain routing — neither is friendly to programmatic
callers.

We monkey-patch the resolver to ALSO honour ``X-Odoo-Database``. The
header is consulted last so that legitimate session cookies still win,
and we apply ``db_filter`` to the requested name so this never widens
access — only what the host would already serve is selectable.

Pattern borrowed from ``egyptair_tenders/controllers/db_router.py`` so
all in-house APIs accept the same header.

Strict semantics:
* Header + session cookie naming a DIFFERENT db → 403 (not silently
  preferring one).
* Header naming a db that ``dbfilter`` rejects → fall through to the
  default behaviour (which will 404 the request) — no silent fallback
  onto another db.
"""

import logging

import werkzeug.exceptions

from odoo.http import Request, db_filter

_logger = logging.getLogger(__name__)

HEADER_NAME = 'X-Odoo-Database'

_original_get_session_and_dbname = Request._get_session_and_dbname


def _get_session_and_dbname_with_header(self):
    session, dbname = _original_get_session_and_dbname(self)

    header_db = self.httprequest.headers.get(HEADER_NAME)
    if not header_db:
        return session, dbname

    if dbname and dbname != header_db:
        # Either a session cookie already nailed the database, or Odoo's
        # monodb shortcut did. Either way, asking for a different db on
        # this request is a contradiction we refuse strictly.
        raise werkzeug.exceptions.Forbidden(
            "%s=%r conflicts with the database already bound to this "
            "request (%r). Send the header without a session cookie, or "
            "match the bound database."
            % (HEADER_NAME, header_db, dbname)
        )

    host = self.httprequest.environ.get('HTTP_HOST', '')
    if not db_filter([header_db], host=host):
        # dbfilter rejected the requested db — leave the resolution as-is
        # so the default 404 / db-picker behaviour fires. Never silently
        # substitute a different database.
        return session, dbname

    session.can_save = False
    session.db = header_db
    _logger.info("real_estate_api: resolved db=%r via %s header",
                 header_db, HEADER_NAME)
    return session, header_db


Request._get_session_and_dbname = _get_session_and_dbname_with_header
