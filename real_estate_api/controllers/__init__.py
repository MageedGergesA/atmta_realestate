# db_router must import FIRST: it monkey-patches Request._get_session_and_dbname
# so all routes registered after it see the X-Odoo-Database header.
from . import db_router
from . import _base
from . import api_v1_catalog
from . import api_v1_interest
from . import api_v1_map
from . import api_v1_partners
from . import api_v1_portal
from . import api_v1_embed_token
from . import api_v1_image
from . import embed_v1
