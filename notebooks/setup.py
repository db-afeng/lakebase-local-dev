# Databricks notebook source
# MAGIC %md
# MAGIC # Shared workshop setup
# MAGIC
# MAGIC Notebooks 0, 1, 2, and 3 run this notebook with `%run ./setup` before their input
# MAGIC variables. You do not need to run it separately. `%run` shares `ROOT`, library imports,
# MAGIC constants, default resource names, and `w` with the calling notebook; `dbutils.notebook.run` would use a separate
# MAGIC session and is not a substitute.
# MAGIC
# MAGIC ## Before you begin
# MAGIC
# MAGIC Clone or import the **complete DataCart repository** into a Databricks Workspace
# MAGIC Git folder or directory. Keep `notebooks/`, `scripts/`, `migrations/`, `server/`,
# MAGIC `frontend/`, `app.py`, `alembic.ini`, and `requirements.txt` together.
# MAGIC Importing only the notebooks will not work.
# MAGIC
# MAGIC Attach Python 3.12+ compute with network access to Lakebase and the package
# MAGIC registries. Notebook 2 also needs Node.js 20+; notebook 2 installs npm if needed.
# MAGIC Authentication uses the identity running the notebook. No local CLI profile,
# MAGIC token, or `.env.lakebase` file needs to be copied into the workspace.
# MAGIC
# MAGIC ## Install the Python libraries
# MAGIC
# MAGIC The SDK manages workspace resources, Psycopg connects to PostgreSQL, and Alembic
# MAGIC and SQLAlchemy apply the app's migrations. The SDK and protobuf versions match
# MAGIC the validated runtime environment.
# MAGIC
# MAGIC This notebook intentionally does not restart Python: a restart inside `%run`
# MAGIC can interrupt its caller. Start with a fresh Python session. If you already
# MAGIC imported incompatible library versions, restart Python using the notebook UI
# MAGIC and run the calling notebook from the beginning. Setup runs before inputs, so
# MAGIC all input variables are assigned afterward.

# COMMAND ----------

# MAGIC %pip install "databricks-sdk==0.167.0" "protobuf==5.29.5" "psycopg[binary,pool]>=3.2.0" alembic sqlalchemy pyyaml

# COMMAND ----------

# MAGIC %md
# MAGIC ## Locate the source and authenticate
# MAGIC
# MAGIC Find the repository from this notebook's working directory, import the libraries,
# MAGIC and create a workspace client. Resource operations are written directly in
# MAGIC notebooks 0–3 so you can inspect each step. This only initializes the
# MAGIC session; it does not create projects, change the database, or deploy an app.
# MAGIC
# MAGIC If repository discovery fails, open the notebook inside the complete repository.
# MAGIC If a library import fails after installation, restart Python and rerun the caller.

# COMMAND ----------

# Standard-library tools support temporary builds, package verification, and API output.
import base64
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import urlopen
from uuid import uuid4

# Psycopg handles PostgreSQL sessions; the SDK manages workspace resources.
import psycopg
from psycopg import sql
import yaml
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service import apps, postgres, workspace

# Both notebook-created tables and the app use this database-only ownership role.
OWNER_ROLE = "datacart_storefront_owner"
SCHEMA = "ecommerce"

# Walk upward from the notebook directory until the app and migration files are found.
# Checking source files rather than a fixed user path also supports Workspace Git folders.
ROOT = next(
    (
        p
        for p in [Path.cwd(), *Path.cwd().parents]
        if (p / "app.py").is_file()
        and (p / "alembic.ini").is_file()
        and (p / "migrations").is_dir()
        and (p / "frontend").is_dir()
    ),
    None,
)
if ROOT is None:
    raise RuntimeError(
        "Open this notebook inside the complete DataCart repository in /Workspace."
    )
# In a workspace notebook, the SDK uses the identity running this notebook.
# No locally configured CLI profile or token is required.
w = WorkspaceClient()
print(f"Workshop source: {ROOT}")

# Resource names use only the username before @, with unsupported characters
# replaced individually by hyphens. Lowercase and trim edge hyphens to keep names valid.
username = w.current_user.me().user_name
USER_SUFFIX = re.sub(r"[^a-z0-9-]", "-", username.split("@", 1)[0].lower()).strip("-")
if not USER_SUFFIX:
    raise ValueError(
        "Your workspace username has no letters or digits usable in a resource name."
    )
DEFAULT_PROJECT_ID = f"datacart-{USER_SUFFIX}"
DEFAULT_APP_NAME = f"datacart-storefront-{USER_SUFFIX}"
# Keep the complete suffix: do not silently truncate or append a hash.
# If a default exceeds a service limit, override it in the notebook's input block.
print(f"Default project: {DEFAULT_PROJECT_ID}\nDefault app: {DEFAULT_APP_NAME}")
