# Databricks notebook source
# MAGIC %md
# MAGIC # 0 · Create your Lakebase project — optional
# MAGIC
# MAGIC **Workshop goal:** prepare the database infrastructure that DataCart Storefront
# MAGIC will use. By the end of this notebook, you will have a Lakebase Autoscaling
# MAGIC project with its initial branch, PostgreSQL database, and read-write endpoint.
# MAGIC
# MAGIC | Notebook | What you will do |
# MAGIC | --- | --- |
# MAGIC | **0 · Optional** | Create the Lakebase project, or use the workspace UI |
# MAGIC | **1 · Database setup** | Apply Alembic migrations and load storefront sample data |
# MAGIC | **2 · App deployment** | Build and deploy DataCart with access to that database |
# MAGIC | **3 · Teardown** | Preview cleanup, then delete the app and dedicated project |
# MAGIC
# MAGIC ## Choose your starting point
# MAGIC
# MAGIC **Prefer the workspace UI?** Open Lakebase in your Databricks workspace and create
# MAGIC an **Autoscaling** project. Keep the default `production` branch,
# MAGIC `databricks_postgres` database, and read-write compute. Wait for provisioning,
# MAGIC copy the project ID, and go directly to **1_prepare_schema_and_seed**.
# MAGIC You can skip every execution cell in this notebook.
# MAGIC
# MAGIC **Prefer a notebook?** Continue below as a workspace user with permission to
# MAGIC create Lakebase projects. Choose a project ID dedicated to this workshop.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 · Run shared setup
# MAGIC
# MAGIC Run the next cell first. It installs the workshop libraries, locates the complete
# MAGIC repository, and authenticates with your workspace identity. Open **setup** for
# MAGIC shared prerequisites and troubleshooting.
# MAGIC
# MAGIC Edit the Python input block below before running the remaining cells in order.
# MAGIC If you change an input later, rerun that block and all cells below it.

# COMMAND ----------

# MAGIC %run ./setup

# COMMAND ----------

# MAGIC %md
# MAGIC ## Your inputs
# MAGIC
# MAGIC `LAKEBASE_PROJECT_ID` defaults to `datacart-<username-before-@>` from shared setup.
# MAGIC For example, `alex.feng@example.com` becomes `datacart-alex-feng`.
# MAGIC Override it with your existing project ID if you used clickops.
# MAGIC A full `projects/<id>` resource path is also accepted. Use the ID rather than its
# MAGIC display name or PostgreSQL hostname. IDs have 3–63 lowercase letters, digits, or
# MAGIC hyphens, start with a letter, and end with a letter or digit.

# COMMAND ----------

LAKEBASE_PROJECT_ID = (
    DEFAULT_PROJECT_ID  # Override for a project created through clickops.
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Understand the resources you are creating
# MAGIC
# MAGIC | Resource | Its purpose in this workshop |
# MAGIC | --- | --- |
# MAGIC | **Project** | Groups your Lakebase branches and compute resources |
# MAGIC | **Branch** | Provides an isolated database environment; `production` is the initial branch's name |
# MAGIC | **Database** | Stores the storefront tables; the default SQL name is `databricks_postgres` |
# MAGIC | **Endpoint / compute** | Provides the hostname and compute used by PostgreSQL clients |
# MAGIC
# MAGIC The project-creation API provisions the initial resources together. This notebook
# MAGIC does not create a separate development branch. Later, `make dev-lakebase` creates
# MAGIC development branches from the seeded parent for local Git-based development.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 · Create or inspect the project
# MAGIC
# MAGIC Run the next cell after entering your project ID.
# MAGIC
# MAGIC - If the project does not exist, this cell creates it and waits for provisioning.
# MAGIC - If it already exists, this cell reuses it without replacing its settings.
# MAGIC - It checks for the initial `production` branch and `databricks_postgres` database,
# MAGIC   then resolves the read-write endpoint and hostname.
# MAGIC
# MAGIC This step creates billable infrastructure when it creates a new project. It does
# MAGIC not create storefront tables or insert data; notebook 1 handles those tasks.

# COMMAND ----------

# Set LAKEBASE_PROJECT_ID in the input block above, for example datacart-yourname.
# A full projects/<id> resource path is also accepted.
# Accept either the project ID or its full projects/<id> resource path.
project_id = LAKEBASE_PROJECT_ID.strip().removeprefix("projects/")
if not re.fullmatch(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", project_id):
    raise ValueError(
        "Enter your Lakebase project ID (3–63 lowercase letters, digits, hyphens)."
    )
project = f"projects/{project_id}"
# Reuse an existing project without changing it; create only when it is not found.
try:
    w.postgres.get_project(name=project)
    print(f"Reusing {project}")
except NotFound:
    # The create operation also provisions the production branch, default database,
    # and primary endpoint. Wait before trying to discover those child resources.
    w.postgres.create_project(
        project_id=project.split("/")[-1],
        project=postgres.Project(
            spec=postgres.ProjectSpec(display_name="DataCart Storefront")
        ),
    ).wait()
    print(f"Created {project}")

BRANCH_ID = "production"
DATABASE_NAME = "databricks_postgres"

# COMMAND ----------

# MAGIC %md
# MAGIC ### Inspect the provisioned database
# MAGIC
# MAGIC The next cell reads the initial branch, finds the database by its SQL name, and
# MAGIC resolves the read-write endpoint. Database API IDs and SQL names can differ.
# MAGIC If the project has no default branch/database, select its existing destination
# MAGIC explicitly in notebooks 1 and 2.

# COMMAND ----------

# Accept either the project ID or its full projects/<id> resource path.
project_id = LAKEBASE_PROJECT_ID.strip().removeprefix("projects/")
if not re.fullmatch(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", project_id):
    raise ValueError(
        "Enter your Lakebase project ID (3–63 lowercase letters, digits, hyphens)."
    )
project = f"projects/{project_id}"
if not re.fullmatch("[a-z][a-z0-9-]*", BRANCH_ID):
    raise ValueError("Enter a branch ID, not a full resource path.")
w.postgres.get_project(name=project)
branch = f"{project}/branches/{BRANCH_ID}"
w.postgres.get_branch(name=branch)
# Match the SQL database name from its status; the API resource ID can use hyphens.
databases = list(w.postgres.list_databases(parent=branch))
matches = [
    d for d in databases if d.status and d.status.postgres_database == DATABASE_NAME
]
if len(matches) != 1:
    raise RuntimeError(
        f"Database {DATABASE_NAME!r} not found uniquely in {branch}. Select an existing database."
    )
# Select the writable endpoint. Depending on the API response, its type can appear
# in the requested specification or in the observed status.
endpoints = [
    e
    for e in w.postgres.list_endpoints(parent=branch)
    if (
        (e.spec.endpoint_type if e.spec else None)
        or (e.status.endpoint_type if e.status else None)
    )
    == postgres.EndpointType.ENDPOINT_TYPE_READ_WRITE
]
if len(endpoints) != 1:
    raise RuntimeError(
        f"Expected one read-write endpoint in {branch}; found {len(endpoints)}."
    )
# Fetch current endpoint status for the hostname instead of constructing one.
endpoint = w.postgres.get_endpoint(name=endpoints[0].name)
host = endpoint.status.hosts.host if endpoint.status and endpoint.status.hosts else None
if not host:
    raise RuntimeError("Endpoint has no hostname yet. Wait for provisioning and retry.")
# Keep the resolved paths and hostname together for the following cells.
# These values are connection metadata, not database passwords.
target = {
    "project": project,
    "branch": branch,
    "database": matches[0].name,
    "database_name": DATABASE_NAME,
    "endpoint": endpoint.name,
    "host": host,
}
print(json.dumps(target, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checkpoint · Inspect the output
# MAGIC
# MAGIC You should see either **Created** or **Reusing**, followed by a dictionary with
# MAGIC `project`, `branch`, `database`, `database_name`, `endpoint`, and `host`.
# MAGIC
# MAGIC Confirm that the project ID is yours, the branch ends in `/branches/production`,
# MAGIC and the SQL database name is `databricks_postgres`. The hostname is connection
# MAGIC metadata; it is not a password and is not the project ID you enter later.
# MAGIC
# MAGIC You can also open the project in the Lakebase UI and inspect its branch,
# MAGIC database, and compute. No `ecommerce` storefront tables are expected yet.
# MAGIC
# MAGIC ## If a step fails
# MAGIC
# MAGIC | What you see | What to do |
# MAGIC | --- | --- |
# MAGIC | Invalid or empty project ID | Set LAKEBASE_PROJECT_ID in the input block, then rerun the input cell and creation cell |
# MAGIC | Project creation permission error | Use an identity allowed to create projects, or ask the workshop administrator to create one |
# MAGIC | Project exists but expected branch/database is missing | Inspect it in the UI; use its intended existing branch/database in notebooks 1 and 2, or choose a fresh workshop project |
# MAGIC | Endpoint has no hostname yet | Wait for provisioning in the UI, then rerun the creation/inspection cell |
# MAGIC
# MAGIC ## Continue to notebook 1
# MAGIC
# MAGIC Open **1_prepare_schema_and_seed** and enter this same project ID. That notebook
# MAGIC works equally with a project created here or through clickops.
# MAGIC
# MAGIC Keep the project while completing the workshop. Finishing a notebook does not
# MAGIC stop Lakebase compute. After the workshop, use **3_teardown_datacart** or the
# MAGIC workspace UI to remove your resources when you no longer need them.
