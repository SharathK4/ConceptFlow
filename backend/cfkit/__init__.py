"""cfkit - the house style for ConceptFlow lessons.

Generated scenes start with::

    from manim import *
    from cfkit import *

and subclass :class:`ConceptScene`. That gives them narration-timed beats, the
dark stage, the palette and type, keyframes and a layout report for the
reviewing agents, and a handful of pre-styled building blocks. Everything else
is plain Manim Community.
"""

from . import camera as _camera
from . import compat as _compat
from . import components as _components
from . import texcache as _texcache
from . import theme as _theme
from .compat import *  # noqa: F401,F403
from .components import *  # noqa: F401,F403
from .scene import Beat, ConceptScene
from .theme import *  # noqa: F401,F403

__all__ = [*_compat.__all__, *_components.__all__, *_theme.__all__, "Beat", "ConceptScene"]

_texcache.install()
_camera.install()
_theme.apply_defaults()
