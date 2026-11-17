# DataCart Storefront

Includes the React frontend, FastAPI backend, Alembic migrations, Docker stack,
and Lakebase branch tooling. Credentials, caches, build output, and installed
dependencies are generated locally. Docker Compose uses a `lakebase-local-dev-`
project prefix to keep this checkout separate from the original storefront.

Prerequisites: Docker with Compose, Make, Git with `user.name` configured,
Python 3, and `jq`. No Databricks credentials are needed for local PostgreSQL.

## Local development with Docker

The local stack builds the React storefront from source and serves it with
Nginx, alongside the FastAPI backend and PostgreSQL 17.

```bash
make dev-local
```

Always use the Make targets rather than raw `docker compose` commands. Each
development context gets one eight-character ID shared by its Compose project
and Lakebase branch. Cursor worktrees reuse the ID embedded in an exact
`cursor/<id>` Git branch; other branches derive it from the raw Git user and
branch names. This isolates containers, networks, images, and `postgres-data`
volumes between feature branches.

The Compose build uses the Databricks PyPI and npm proxies by default. Override
`PIP_INDEX_URL` or `NPM_REGISTRY` when different package indexes are required.

Docker assigns each development context a free loopback port for the
storefront. From a second terminal, print its URL with:

```bash
make url
```

Append `/supplier` for the supplier view or `/api/health` for backend health.
FastAPI and PostgreSQL are not published on host ports; the storefront proxies
API requests to FastAPI over the worktree's private Compose network.

The backend source is bind-mounted, so Python changes trigger an automatic
reload. Frontend source changes require rebuilding the frontend image:

```bash
make compose ARGS='build frontend'
make compose ARGS='up -d frontend'
```

Useful commands:

```bash
make compose ARGS='logs -f backend'
make compose ARGS='exec postgres psql -U datacart -d datacart'
make dev-down
make dev-destroy  # Also deletes only this branch context's local database data.
```

Alembic migrations run automatically before the backend starts. A fresh
PostgreSQL volume creates an empty `ecommerce` schema; Alembic does not load fixtures.
Run `make dev-down` before switching branches in the same checkout so the old
branch context does not keep running.

## Local development with Lakebase

Lakebase mode omits the local PostgreSQL container and connects the backend to
an ephemeral Lakebase Autoscaling branch copied from `production`.

Prerequisites:

- Databricks CLI with `databricks postgres` support (0.294.0 or newer)
- An authenticated Databricks CLI profile, `jq`, and Python 3
- An existing Lakebase project, specified by `LAKEBASE_PROJECT_NAME`
- Your Databricks identity belongs to `lakebase-app-schema-owner`

Set `LAKEBASE_PROJECT_NAME` in your environment or `lakebase.config`.
Connections use the `lakebase-app-schema-owner` group role by default.
Choose your Databricks profile explicitly; override the group role when needed.

Start Lakebase development:

```bash
LAKEBASE_PROJECT_NAME=<project-id> LAKEBASE_PROFILE=<profile> make dev-lakebase
```

This creates or reuses a branch named
`dev-<sanitized git user.name>-<sanitized git branch>-<context-id>`, omitting a
duplicate suffix when a Cursor branch already ends in that ID. It waits for the
read-write endpoint, writes a mode-0600 worktree-local `.env.lakebase`, and
starts Compose with `compose.lakebase.yaml`. For non-Cursor branches, the
derived ID preserves distinctions between Git refs that sanitize to the same
text. Branch Alembic migrations therefore remain owned by the same group role
as production. The generated credential expires after about one hour. Refresh
it and recreate only this development context's backend with:

```bash
LAKEBASE_PROJECT_NAME=<project-id> LAKEBASE_PROFILE=<profile> make refresh-lakebase
```

Stop either development mode with `make dev-down`. Development branches expire
after six hours; stopping Compose does not delete one immediately.

No seed utility is included. Local PostgreSQL starts with the schema only.
Lakebase branches inherit their parent branch's data.

### Optional post-checkout provisioning

To enable checkout-triggered provisioning, first set your chosen
`LAKEBASE_PROFILE` and `LAKEBASE_PROJECT_NAME` in `lakebase.config`
(or export them in your Git environment),
then register this clone:

```bash
make hooks-install
```

The installer works with standard Git and Python 3. It sets a repository-local
`core.hooksPath` to a generated dispatcher directory in Git's metadata. Existing
hooks stay in their original location, including hooks selected by a global or
repository-local `core.hooksPath`. The dispatcher forwards all standard Git hooks
(and custom hook files present at installation) with their arguments, input, and
exit status. Existing `post-checkout` runs first; DataCart setup runs afterward,
even if the existing hook fails. DataCart provisioning failures do not change the
existing hook's exit status.

Relative hook paths are resolved from Git's hook working directory, so linked
worktrees use their own checkout's hooks and `lakebase.config`. The installation
is shared by the clone's worktrees unless a worktree has an explicit
`core.hooksPath` override. Run installation in that worktree to configure it
separately. The installer never edits global configuration or existing scripts.
Installation is idempotent and does not provision Lakebase immediately.

Branch checkouts provision Lakebase asynchronously; file checkouts are ignored.
Each checkout writes progress to `lakebase-branch-post-commit.log`. The hook
prepares `.env.lakebase` but does not restart containers. `make dev-lakebase`
waits for provisioning or retries synchronously when necessary.

To remove the dispatcher and restore the previous local setting (or resume the
inherited global/default hooks directory):

```bash
make hooks-uninstall
```

Uninstall refuses to overwrite subsequently changed configuration or remove
modified wrapper files. If `core.hooksPath` was changed to a different directory
after installation, uninstall leaves it and the saved installation alone; restore
the DataCart path first to use automatic cleanup. Keep generated hook files
unchanged. Reinstall to capture additional nonstandard hook names.

Earlier versions registered a user hook under `~/.databricks/user-githooks/`.
This installer leaves that external file alone. While forwarding an older hook,
the dispatcher suppresses duplicate DataCart provisioning.

Run the local checks with:

```bash
make check
```

## Current limitations

- Local PostgreSQL has no seed data. Add products, inventory, and customer ID 1
  before exercising a purchase. Verified: `/api/account` currently returns HTTP
  500 when that customer is missing.
- Carts live in backend memory and use a shared demo customer (ID 1). They reset
  when the backend restarts; this is not a multi-user account system.
- The optional supplier view reads an externally maintained `product_demand`
  table. This app does not create or update demand summaries; the view is empty
  without that table and can show stale data if its external source stops updating.
  It has no separate supplier authorization.
- Reviews, loyalty, promotions, and other optional features require additional
  tables or columns beyond the initial migration.
- Lakebase requires an existing project, selected CLI profile, and the configured
  group role. Credentials expire after about an hour; use `make refresh-lakebase`.
- Deployment configuration is a template: supply `project_id` explicitly (for
  example, `--var project_id=<project-id>`), review the workspace host and group
  role, and build the frontend before deploying. The bundle name is now
  `datacart-storefront`; existing deployments under a different bundle name keep
  their original state and require an explicit migration if they are to be reused.
