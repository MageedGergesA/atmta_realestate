# Shared vocabulary and services first — they define no models that extend
# others, so nothing depends on load order here.
from . import visual_states
from . import visual_access
from . import visual_assets

# The 0.1 models, before anything that extends them.
from . import spec_tag
from . import building_floor
from . import project_maquette
from . import property_maquette
from . import unit_picker
from . import building_region
from . import building_preview

# Then the layer built on top. `visual_floor_template` extends
# `realestate.building.floor` and `visual_unit_type` extends
# `realestate.property`, so both must load after the files that define them —
# importing them first raises "Model does not exist in registry" at install.
from . import visual_commercial
from . import visual_validation
from . import visual_config
from . import visual_unit_type
from . import visual_floor_template
from . import visual_gallery
from . import visual_migration
from . import visual_modes
from . import visual_deeplink
