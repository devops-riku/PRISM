# Backend architecture

PRISM uses feature-first DDD. Code is grouped by business capability first and
by technical layer second:

```text
app/
  features/
    quotations/
      domain/          estimate model, costing, rates, payments, kinds
      application/     prompt construction
      infrastructure/  quotation repository and Gemini adapter
      presentation/    quotation HTTP routes
    documents/
      domain/          proposal design, policies, and templates
      application/     proposal document lifecycle
      presentation/    proposal document HTTP routes
    intakes/
      application/     intake lifecycle and client projection
      infrastructure/  client tokens and uploaded files
      presentation/    studio routes and public client routes
    workspaces/        workspace context, settings, and numbering
    team/              identity, members, invitations, and team routes
    jobs/              background job lifecycle
    notifications/     persisted inbox and live event hub
    rendering/         Markdown, HTML, PDF, and money output
    platform/          health and reference endpoints
  shared/
    infrastructure/   SQL database, runtime configuration, attachment parsing
    presentation/http/ shared FastAPI dependencies and middleware
  main.py              composition root
```

Not every feature needs every DDD layer. Empty layers are omitted rather than
created for symmetry.

## Dependency rules

- A feature's `domain` contains deterministic business rules. It may depend on
  another feature's domain contract, but never on application, infrastructure,
  presentation, FastAPI, or Starlette.
- `application` coordinates use cases and must not contain HTTP types.
- `infrastructure` owns SQL/filesystem persistence, identity providers, email,
  model providers, and process-level adapters. It must not depend on presentation.
- `presentation` translates HTTP and rendering input/output. It may compose
  application services and adapters from multiple features.
- `shared/infrastructure` is an inward shared kernel and cannot depend on a
  feature. `shared/presentation/http` is intentionally a composition boundary
  and may use feature contracts.
- `main.py` builds FastAPI, installs middleware, runs startup preparation, and
  includes feature routers. It defines no routes.

Several existing application services still call persistence adapters directly.
This is a legacy implementation exception retained to keep the HTTP and domain
contracts stable. New integrations should be introduced behind
application-owned ports, and these adapters can be inverted incrementally.

## Persistence

Structured aggregates use SQLAlchemy Core through
`app.shared.infrastructure.database`. With no `DATABASE_URL`, local development
uses SQLite at `backend/generated/prism.db`; production supplies a PostgreSQL
URL using Psycopg 3. Aggregate payloads stay JSON-shaped so Pydantic domain
models remain authoritative, while indexed SQL columns provide workspace,
identity, token, ordering, and counter lookups.

Alembic owns the schema under `backend/migrations/`. Startup imports the former
JSON records once, preserving those files as a non-authoritative migration
archive. Matching SQL and external completion markers prevent a reset database
from silently restoring stale records or bearer tokens; production recovery
comes from database backups. The filesystem/DigitalOcean Spaces boundary
remains only for uploaded bytes and generated assets that do not belong in
relational rows.

## Feature contracts

Cross-feature imports use explicit canonical modules such as
`app.features.quotations.domain.models`. Domain models shared with another feature are treated as that feature's
public contract; private helpers remain underscore-prefixed.

The central estimate contract currently also contains proposal-document record
types. Splitting those records into `features/documents/domain` is a future
boundary refinement, not part of this behavior-preserving migration.

## Import policy

Only canonical imports under `app.features` and `app.shared` are valid. The
`app/` root contains only `features/`, `shared/`, `main.py`, and `__init__.py`.
Flat feature modules and layer-first packages are deliberately absent so a
dependency's owner is visible in every import statement.

Run `scripts/check_architecture.py` after moving modules or changing imports.
It validates the clean feature-only root, canonical imports, DDD dependency
direction, configuration paths, and the complete HTTP contract.
