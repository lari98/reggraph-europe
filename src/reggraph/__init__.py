"""RegGraph Europe: decision-support platform for EU and Swiss financial groups.

The package is split into small modules, each versioned on its own
(see ``module_versions.json`` and ``docs/VERSIONING.md``).

The platform supports decisions. It never moves money, never issues legally
binding instructions and never replaces a supervisor (Pitch Pack, section 6).
"""

__all__ = ["__version__"]

# Product version. Bumped only by ``tools/module_manifest.py release``.
__version__ = "0.5.0"
