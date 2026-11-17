# Databricks notebook source
# MAGIC %md
# MAGIC # 0 · Run the complete DataCart setup
# MAGIC
# MAGIC **Workshop goal:** create or reuse your Lakebase project, prepare its storefront
# MAGIC schema and sample data, then build and deploy DataCart as a Databricks App.
# MAGIC Edit one input block below and choose **Run all**. Each step must succeed before
# MAGIC the following step starts. This creates billable Lakebase and app resources.
# MAGIC
# MAGIC | Notebook | Purpose |
# MAGIC | --- | --- |
# MAGIC | **0 · Run all** | Run notebooks 1, 2, and 3 in order with the inputs below |
# MAGIC | **1 · Optional project creation** | Create or inspect the project |
# MAGIC | **2 · Schema and seed** | Apply Alembic and load sample data |
# MAGIC | **3 · App deployment** | Build and deploy the storefront |
# MAGIC | **4 · Teardown** | Preview and explicitly delete your workshop resources |
# MAGIC
# MAGIC For a guided walkthrough, run notebooks 1–3 individually instead. If you created
# MAGIC the project through clickops, you can start with notebook 2; this run-all route
# MAGIC also reuses an existing project with its default production branch and database.
# MAGIC Teardown is separate and is never called by this notebook.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 · Prepare the workshop session
# MAGIC
# MAGIC Keep the complete repository together in the workspace. Attach Python 3.12+
# MAGIC compute with working Node.js 20+ and network access to Lakebase and package
# MAGIC registries. Your identity needs project/database administrator permissions and
# MAGIC permission to create Databricks Apps. See **setup** for shared prerequisites.

# COMMAND ----------

# MAGIC %run ./setup

# COMMAND ----------

# MAGIC %md
# MAGIC ## Your inputs
# MAGIC
# MAGIC Defaults use the cleaned part of your workspace username before `@`. For example,
# MAGIC `alex.feng@example.com` gives project `datacart-alex-feng` and app
# MAGIC `datacart-storefront-alex-feng`. Override either name when needed, including app
# MAGIC names longer than 30 characters. Existing apps are reused only for the same
# MAGIC database. Use a dedicated workshop database; the seed reserves fixture IDs.
# MAGIC
# MAGIC New projects include `production` and `databricks_postgres`. Use other branch
# MAGIC and database values only when they already exist in your project. Leave
# MAGIC `SOURCE_PATH` blank to stage releases under your workspace user directory.

# COMMAND ----------

LAKEBASE_PROJECT_ID = DEFAULT_PROJECT_ID
BRANCH_ID = "production"
DATABASE_NAME = "databricks_postgres"
APP_NAME = DEFAULT_APP_NAME
SOURCE_PATH = ""

# COMMAND ----------

# Save one set of participant inputs before child notebooks assign their defaults.
# %run shares Python globals; each child consumes only its own one-time override.
_DATACART_RUN_ALL_CONFIG = {
    "LAKEBASE_PROJECT_ID": LAKEBASE_PROJECT_ID,
    "BRANCH_ID": BRANCH_ID,
    "DATABASE_NAME": DATABASE_NAME,
    "APP_NAME": APP_NAME,
    "SOURCE_PATH": SOURCE_PATH,
}
# Reject invalid settings before project creation can incur charges.
project_id = LAKEBASE_PROJECT_ID.strip().removeprefix("projects/")
if not re.fullmatch(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", project_id):
    raise ValueError("Enter a valid Lakebase project ID or projects/<id> path.")
if not re.fullmatch(r"[a-z][a-z0-9-]*", BRANCH_ID):
    raise ValueError("Enter an existing branch ID, not a full resource path.")
if not DATABASE_NAME.strip():
    raise ValueError("DATABASE_NAME cannot be empty.")
resolved_app_name = APP_NAME.strip() or DEFAULT_APP_NAME
if not re.fullmatch(r"[a-z][a-z0-9-]{0,28}[a-z0-9]", resolved_app_name):
    raise ValueError("APP_NAME must be 2–30 lowercase letters, digits, or hyphens.")
print(
    f"Project: {project_id}\nBranch: {BRANCH_ID}\nDatabase: {DATABASE_NAME}\nApp: {APP_NAME or DEFAULT_APP_NAME}"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 · Create or inspect the Lakebase project
# MAGIC
# MAGIC Run notebook 1 inline. It creates missing project infrastructure or reuses an
# MAGIC existing project without changing its settings, then discovers the default
# MAGIC branch, database, and writable endpoint. It does not seed data.

# COMMAND ----------

# Pass a copy: this step cannot change the settings saved for later steps.
_DATACART_NEXT_INPUTS = dict(_DATACART_RUN_ALL_CONFIG)

# COMMAND ----------

# MAGIC %run ./1_optional_create_lakebase_project

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 · Apply migrations and seed the database
# MAGIC
# MAGIC Run notebook 2 with the same project and selected database. It prepares shared
# MAGIC schema ownership, applies Alembic, and inserts missing fixtures while preserving
# MAGIC existing rows and stock quantities. Any exception stops **Run all** here.

# COMMAND ----------

_DATACART_NEXT_INPUTS = dict(_DATACART_RUN_ALL_CONFIG)

# COMMAND ----------

# MAGIC %run ./2_prepare_schema_and_seed

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 · Build and deploy the storefront
# MAGIC
# MAGIC Run notebook 3 with your original app and source-path settings. It waits for the
# MAGIC app identity, grants database access, builds React, uploads a fresh source release,
# MAGIC and starts the app. Startup can take several minutes.

# COMMAND ----------

_DATACART_NEXT_INPUTS = dict(_DATACART_RUN_ALL_CONFIG)

# COMMAND ----------

# MAGIC %run ./3_create_datacart_app

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checkpoint · Open your storefront
# MAGIC
# MAGIC The URL below requires workspace authentication. Open it, inspect the catalog,
# MAGIC add a product, and complete checkout. App and Lakebase compute remain available
# MAGIC after notebook compute stops. Use **4_teardown_datacart** when finished.
# MAGIC
# MAGIC To retry after a failure, fix its cause and run this notebook again. The child
# MAGIC notebooks preserve existing workshop data and reuse matching resources. Their
# MAGIC procedural code and troubleshooting remain available in notebooks 1–3.

# COMMAND ----------

# The app object and resolved destination are returned through the shared session.
print("DataCart Storefront:", app.url)
print("Project:", target["project"])
print("Database:", target["database"])
del _DATACART_RUN_ALL_CONFIG
