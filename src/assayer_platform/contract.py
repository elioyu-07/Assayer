"""Compatibility re-export of :mod:`assayer_plugin_sdk.contract`.

The canonical plugin contract now lives in the SDK. This module is kept so the
platform kernel's existing ``from .contract import ...`` imports keep working
during the SDK extraction migration.
"""

from assayer_plugin_sdk.contract import *  # noqa: F401,F403
from assayer_plugin_sdk.contract import _freeze, _mapping  # noqa: F401
