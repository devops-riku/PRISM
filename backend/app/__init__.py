"""PRISM backend package.

Deliberately inert: importing `app` must have no side effects and must not pull
in `config`, `gemini_service` or `renderers`. Every module imports what it needs
by its full path (`from app.config import ...`), which keeps the import graph
acyclic and lets `config`, `costing`, `prompts` and `storage` be imported and
tested without an API key present.
"""

__all__: list[str] = []
