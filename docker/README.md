# Docker deployment

The Docker stack runs four services behind one public entry point:

- Traefik publishes HTTP/HTTPS and discovers only explicitly labelled services.
- `/api` (including the notification WebSocket) routes to FastAPI.
- Every other path routes to the Nginx-served React application.
- PostgreSQL is reachable only by the backend on an internal Docker network.

PostgreSQL data and generated/uploaded files use separate named volumes. The
backend waits for PostgreSQL, applies Alembic migrations, and then starts
Uvicorn. No database or application container port is published directly.

Two Compose deployments are available:

- `docker-compose.yml` includes its own Traefik service.
- `docker-compose.external.yml` connects PRISM to an existing Traefik instance
  through the external `asseqr-system_app-network` Docker network.

## Local Docker run

From the repository root:

```sh
cp docker/.env.example docker/.env
# Set POSTGRES_PASSWORD and GEMINI_API_KEY in docker/.env
docker compose --env-file docker/.env -f docker/docker-compose.yml up --build -d
```

In PowerShell, `Copy-Item docker/.env.example docker/.env` is the equivalent of
the first command.

Open <http://localhost> and check <http://localhost/api/health>. To use another
host port, change `HTTP_PORT`; when doing so, also set `APP_ORIGIN` and
`ALLOWED_ORIGINS` to the complete browser origin, including that port.

View status and logs with:

```sh
docker compose --env-file docker/.env -f docker/docker-compose.yml ps
docker compose --env-file docker/.env -f docker/docker-compose.yml logs -f backend
```

Stop the containers without deleting data:

```sh
docker compose --env-file docker/.env -f docker/docker-compose.yml down
```

Named volumes are deleted only when `down --volumes` is used. That permanently
removes the PostgreSQL database and locally stored generated/uploaded files.

## HTTPS with Let's Encrypt

Point the hostname's DNS at the Docker host, make ports 80 and 443 reachable,
then set `APP_HOST` and `LETSENCRYPT_EMAIL` in `docker/.env`. Start the base and
TLS overlay together:

```sh
docker compose --env-file docker/.env -f docker/docker-compose.yml -f docker/docker-compose.tls.yml up --build -d
```

The overlay redirects HTTP to HTTPS and stores ACME state in its own named
volume. The Traefik dashboard is deliberately not exposed.

Traefik and Uvicorn request access logs are deliberately disabled. Client
access tokens occur in public API paths, so ordinary request logs would turn a
bearer credential into retained log data. Service errors and migration output
still appear in `docker compose logs`.

## Existing Traefik at prism.neptune.ph

`docker-compose.external.yml` is a standalone stack without a Traefik
container. It expects the external proxy network to exist on the deployment
host. The network name is fixed to `asseqr-system_app-network` as required.
The `websecure` entrypoint and `letsencrypt` certificate resolver can be
changed in `docker/.env` without editing Compose.
Only HTTPS routers are registered; the external Traefik installation must
already redirect its HTTP entrypoint to `websecure` if HTTP redirects are wanted.

From the repository root:

```sh
docker network inspect asseqr-system_app-network
docker compose --env-file docker/.env -f docker/docker-compose.external.yml config
docker compose --env-file docker/.env -f docker/docker-compose.external.yml up --build -d
```

The real, ignored `docker/.env` is configured for
`https://prism.neptune.ph`. Point that DNS name at the external Traefik host
before starting the stack. PostgreSQL remains on PRISM's private internal
network and is not exposed to the proxy or host.

Client access tokens currently occur in public API paths. The PRISM backend
disables Uvicorn access logs, but this Compose project does not control the
external Traefik process; keep that proxy's request access log disabled or
redacted so those URLs are not retained.

## Database modes

Docker always supplies a PostgreSQL `DATABASE_URL`. Running the backend normally
outside Docker leaves `DATABASE_URL` unset and therefore keeps the local SQLite
default. PostgreSQL is not published to the host; use `docker compose exec` for
administration, for example:

```sh
docker compose --env-file docker/.env -f docker/docker-compose.yml exec postgres psql -U prism -d prism
```

The image never contains `backend/.env`, the local virtual environment, or
existing files from `backend/generated`. To migrate an existing file-backed
installation, back it up first and copy its generated data into the
`generated_data` volume before the backend's first migration/import run.

## Backups and scaling

Named volumes survive container replacement; they are not backups. Schedule
real PostgreSQL backups with `pg_dump`, keep copies off the Docker host, define
a retention policy, and regularly prove they restore into a separate database.
For example, with the default database/user:

```sh
docker compose --env-file docker/.env -f docker/docker-compose.yml exec -T postgres pg_dump --username prism --dbname prism --format=custom > prism-postgres.dump
```

Back up the `generated_data` volume separately using a volume snapshot or your
host's backup system. It contains locally stored uploads and generated files;
those bytes are not included in a PostgreSQL dump. A recoverable deployment
needs both the database dump and that file backup.

Run exactly one backend replica. Current aggregate state transitions are
protected by process-local locks; two backend processes can race across those
locks even when both use the same PostgreSQL database. Do not use
`--scale backend=2` until those transitions use database row/advisory locks or
equivalent database-level concurrency control. Frontend replicas do not carry
this limitation.
