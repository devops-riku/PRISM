"""PRISM backend package.

Deliberately inert: importing ``app`` has no side effects. Runtime code uses
explicit canonical paths under ``app.features`` and ``app.shared`` so every
dependency's owner is visible without loading the composition root.
"""

__all__: list[str] = []
