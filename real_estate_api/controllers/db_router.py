"""Allow API clients to pick the target database via an HTTP header
or ``?db=`` query parameter.

Odoo's default ``Request._get_session_and_dbname`` resolves the database
from (a) the session cookie or (b) a single-DB shortcut when only one
database matches ``dbfilter``. For a public REST API with no cookies,
that means multi-DB hosts force clients into the database picker page
or to set up subdomain routing — neither is friendly to programmatic
callers.

We monkey-patch the resolver to ALSO honour:

* ``X-Odoo-Database`` header — for server-to-server API calls.
* ``?db=`` query parameter — for browser-loaded URLs (iframes,
  redirects), where the client cannot set custom headers.

Both are consulted last so that legitimate session cookies still win,
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
* Header AND ``?db=`` query both present but disagreeing → 403.
"""

import logging

import werkzeug.exceptions

from odoo.http import Request, db_filter

_logger = logging.getLogger(__name__)

HEADER_NAME = 'X-Odoo-Database'
QUERY_PARAM = 'db'

_original_get_session_and_dbname = Request._get_session_and_dbname


def _get_session_and_dbname_with_header(self):
    session, dbname = _original_get_session_and_dbname(self)

    header_db = self.httprequest.headers.get(HEADER_NAME)
    # ``args`` is werkzeug's MultiDict of query-string params. Reading
    # it cannot raise; missing key just returns None.
    query_db = self.httprequest.args.get(QUERY_PARAM) if self.httprequest.args else None

    if header_db and query_db and header_db != query_db:
        # Two explicit choices that disagree — refuse strictly rather
        # than silently picking one.
        raise werkzeug.exceptions.Forbidden(
            "%s=%r conflicts with ?%s=%r — choose one."
            % (HEADER_NAME, header_db, QUERY_PARAM, query_db)
        )
    requested_db = header_db or query_db
    if not requested_db:
        return session, dbname

    if dbname and dbname != requested_db:
        # Either a session cookie already nailed the database, or Odoo's
        # monodb shortcut did. Either way, asking for a different db on
        # this request is a contradiction we refuse strictly.
        raise werkzeug.exceptions.Forbidden(
            "Requested db %r conflicts with the database already bound "
            "to this request (%r). Send the request without a session "
            "cookie, or match the bound database."
            % (requested_db, dbname)
        )

    host = self.httprequest.environ.get('HTTP_HOST', '')
    if not db_filter([requested_db], host=host):
        # dbfilter rejected the requested db — leave the resolution as-is
        # so the default 404 / db-picker behaviour fires. Never silently
        # substitute a different database.
        return session, dbname

    session.can_save = False
    session.db = requested_db
    _logger.info("real_estate_api: resolved db=%r via %s",
                 requested_db, 'header' if header_db else 'query param')
    return session, requested_db


Request._get_session_and_dbname = _get_session_and_dbname_with_header
