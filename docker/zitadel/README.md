# Zitadel (zitadel.neptune.ph)

A self-hosted [Zitadel](https://zitadel.com) for PRISM's sign-in. It runs as its own compose project
(`neptune-zitadel`), separate from PRISM and from Task Queue's instance (`taskq-zitadel`), so nothing done to
either stack touches it.

PRISM keeps its own sign-in screen. Its API calls Zitadel as the service account `prism-api` (instance role
`IAM_LOGIN_CLIENT`) to check passwords and emailed codes, then issues PRISM's own tokens. That account's token is
`ZITADEL_TOKEN` in PRISM's `docker/.env`. It can start a sign-in for anyone: keep it as secret as a database
password.

PRISM's people live in the `PRISM` organization (`ZITADEL_ORG_ID` in PRISM's `docker/.env`).

## Files

This folder in the PRISM repository is the source; the running copy is `/var/www/repos/zitadel` on Neptune,
with the real `.env` beside it (never committed).

- `docker-compose.yml`: a verbatim copy of upstream `deploy/compose/docker-compose.yml` at the pinned version
  (v4.19.5). Don't edit it; put changes in the override or the Neptune overlay.
- `docker-compose.override.yml`: local additions (forwards the first admin password).
- `docker-compose.neptune.yml`: this server's names, networks, edge proxy, SMTP host and sign-up policy.
- `neptune-edge.conf`: the nginx edge Traefik routes `zitadel.neptune.ph` to.
- `.env.example`: upstream's template. The server's `.env` sets `ZITADEL_DOMAIN=zitadel.neptune.ph`,
  `ZITADEL_EXTERNALPORT=443`, `ZITADEL_EXTERNALSECURE=true`, `ZITADEL_PUBLIC_SCHEME=https`,
  `COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml:docker-compose.neptune.yml`, generated secrets,
  and `RESEND_SMTP_PASSWORD`.
- `.env` (server only): the real secrets (mode 600). `COMPOSE_FILE` in it loads all three compose files.
- `prism-api.pat`, `prism-api-dev.pat`, `prism-ids.env` (server only): the service account tokens made at setup
  and the PRISM organization, project and account ids.

## Run

```sh
cd /var/www/repos/zitadel
docker network create neptune-zitadel-link   # first time only
docker compose up -d --wait
```

## What was configured after the first start

Through the admin API with the short-lived bootstrap token (expires 2026-10-29):

- Organization `PRISM` and project `PRISM` in it.
- Machine users `prism-api` (Neptune) and `prism-api-dev` (developer machines, token expires 2027-04-25), both
  with the instance role `IAM_LOGIN_CLIENT`.
- The Resend SMTP provider, with credentials, activated.
- Secret generators "Email verification" and "One-time password by email": 6 digits, 10 minutes. PRISM's sign-in
  screen draws six digit boxes.
- Message texts for "Verify email" and "Verify email OTP" (English) worded for PRISM. Zitadel's defaults have no
  footer, and the missing translation is logged at warning level with the code in it.

| What | URL |
|---|---|
| Console | https://zitadel.neptune.ph/ui/console |
| Login | https://zitadel.neptune.ph/ui/v2/login |
| OIDC discovery | https://zitadel.neptune.ph/.well-known/openid-configuration |

The first admin is `zitadel-admin@zitadel.zitadel.neptune.ph` with the password in `ZITADEL_ADMIN_PASSWORD`
in `.env`. That value only applies on the very first start; change the password in the console afterwards.

## Secrets

- `ZITADEL_MASTERKEY` is exactly 32 characters and can never change once the database is initialized. Back it
  up: without it the encrypted data is unrecoverable.
- `POSTGRES_ADMIN_PASSWORD` is repeated inside `ZITADEL_DATABASE_POSTGRES_DSN` (hex, so no escaping).
- `LOGIN_SESSION_COOKIE_SECRET` is at least 32 characters.

## Google and Facebook

Configured in the console under the PRISM organization (Settings, then Identity Providers), not in PRISM.
PRISM shows a button only for providers that are active there. The redirect URL to register with Google and
Meta is shown on the provider's page in the console (it ends in `/idps/callback`).

## Email

Every code goes out by email through Resend (`smtp.resend.com:2587`, since the droplet blocks 587; user
`resend`, the API key as password) from `PRISM <invites@neptune.ph>`. The emails use Zitadel's default texts,
which can be changed under Default Settings, then Message Texts.

## Upgrade

1. Copy the new upstream `deploy/compose/docker-compose.yml` over this one and diff `.env` against upstream's
   `.env.example`.
2. Bump `ZITADEL_VERSION` in `.env`.
3. `docker compose pull && docker compose up -d --wait`
