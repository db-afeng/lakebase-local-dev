# Databricks notebook source
# MAGIC %md
# MAGIC # 2 · Build the storefront database and load sample data
# MAGIC
# MAGIC **Workshop goal:** turn an existing Lakebase project into a usable DataCart
# MAGIC storefront database. You will apply the application's versioned schema migrations,
# MAGIC load a small sample catalog and order history, and verify the result.
# MAGIC
# MAGIC You may start here after creating your project through the Lakebase UI.
# MAGIC **Notebook 1 is optional.** This notebook accepts your existing project ID and
# MAGIC does not create a new project.
# MAGIC
# MAGIC ### What you need
# MAGIC
# MAGIC - A Lakebase Autoscaling project with an existing branch, database, and read-write endpoint.
# MAGIC - The project-owner identity, or a database administrator allowed to create roles,
# MAGIC   grant role membership, and create schemas.
# MAGIC - A dedicated workshop database: sample records use fixed IDs, including customer ID 1.
# MAGIC
# MAGIC **Outcome:** an `ecommerce` schema containing customers, products, inventory,
# MAGIC orders, and order items, ready for the app deployed in notebook 3.

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
# MAGIC
# MAGIC Use the same destination in notebooks 2 and 3. Keep `BRANCH_ID = "production"`
# MAGIC and `DATABASE_NAME = "databricks_postgres"` for a new project, or enter the
# MAGIC existing branch and SQL database name you intend to use.

# COMMAND ----------

LAKEBASE_PROJECT_ID = (
    DEFAULT_PROJECT_ID  # Override for a project created through clickops.
)
BRANCH_ID = "production"
DATABASE_NAME = "databricks_postgres"

# COMMAND ----------

# Notebook 0 passes its settings in the shared %run session. Consume them once
# so a later standalone run returns to the editable defaults above.
if "_DATACART_NEXT_INPUTS" in globals():
    _step_inputs = globals().pop("_DATACART_NEXT_INPUTS")
    LAKEBASE_PROJECT_ID = _step_inputs["LAKEBASE_PROJECT_ID"]
    BRANCH_ID = _step_inputs["BRANCH_ID"]
    DATABASE_NAME = _step_inputs["DATABASE_NAME"]
    del _step_inputs


# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 · Inspect the selected database
# MAGIC
# MAGIC The next cell discovers your existing branch, database resource, and read-write
# MAGIC endpoint. A SQL name such as `databricks_postgres` can have an API ID such as
# MAGIC `databricks-postgres`; the code resolves that distinction.
# MAGIC
# MAGIC **Check the printed destination before continuing.** Later steps act on that
# MAGIC database. This discovery step does not create any resources.

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
# Deleted projects remain readable during retention, but their branches are unavailable.
# Stop before database access; recovering old data must be an explicit choice.
project_resource = w.postgres.get_project(name=project)
if project_resource.delete_time:
    raise RuntimeError(
        f"{project} is soft-deleted (deleted {project_resource.delete_time}; "
        f"scheduled purge {project_resource.purge_time or 'not reported'}). "
        "Choose a different LAKEBASE_PROJECT_ID for a fresh workshop, or explicitly "
        "restore this project in Lakebase before rerunning. No resources were changed."
    )
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
# MAGIC ## Step 3 · Establish consistent schema ownership
# MAGIC
# MAGIC A notebook runs as **you**; a deployed Databricks App runs as its own **service
# MAGIC principal**. If those identities create database objects independently, later
# MAGIC schema changes can fail because one identity does not own the other's tables.
# MAGIC
# MAGIC We use a dedicated PostgreSQL role, `datacart_storefront_owner`, to own the
# MAGIC storefront schema and objects consistently.
# MAGIC
# MAGIC | Identity / role | Responsibility |
# MAGIC | --- | --- |
# MAGIC | Your workspace identity | Authenticates this notebook and prepares the database |
# MAGIC | `datacart_storefront_owner` | Owns the storefront objects; `NOLOGIN` means it cannot authenticate directly |
# MAGIC | App service principal | Authenticates the app; notebook 3 grants it membership in the owner role |
# MAGIC
# MAGIC Run the next cell to prepare the owner role and your membership. No workspace
# MAGIC SCIM group is needed. The cell refuses to take over an existing `ecommerce`
# MAGIC schema owned by a different role; it does not drop or silently reassign it.
# MAGIC
# MAGIC **Expected output:** `Ownership role ready: datacart_storefront_owner`.

# COMMAND ----------

# Authenticate this database session as the notebook's workspace user.
username = w.current_user.me().user_name
# Generate a fresh short-lived OAuth password for each connection attempt.
for attempt in range(4):
    credential = w.postgres.generate_database_credential(endpoint=target["endpoint"])
    try:
        conn = psycopg.connect(
            host=target["host"],
            dbname=target["database_name"],
            user=username,
            password=credential.token,
            sslmode="require",  # Encrypt the connection to Lakebase.
            connect_timeout=20,
            options="",  # Keep your administrator role for role creation/grants.
        )
        break
    except psycopg.OperationalError:
        if attempt == 3:
            raise
        # Allow a recently provisioned or sleeping endpoint time to become available.
        time.sleep(2**attempt)
# Do not leave the OAuth credential object in the notebook's global variables.
del credential
# The connection context commits on success, rolls back on error, and closes.
with conn:
    current_user = conn.execute("SELECT current_user").fetchone()[0]
    # NOLOGIN makes this a shared ownership role, not another login identity.
    existing = conn.execute(
        "SELECT rolcanlogin FROM pg_roles WHERE rolname=%s", (OWNER_ROLE,)
    ).fetchone()
    if existing is None:
        conn.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(OWNER_ROLE))
        )
    elif existing[0]:
        raise RuntimeError(
            f"{OWNER_ROLE} already exists as a login role; choose a clean workshop database."
        )
    # Grant your authenticated login permission to assume the owner role.
    # Quote identifiers with psycopg.sql; role/database names are not SQL values.
    conn.execute(
        sql.SQL("GRANT {} TO {}").format(
            sql.Identifier(OWNER_ROLE), sql.Identifier(current_user)
        )
    )
    conn.execute(
        sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
            sql.Identifier(target["database_name"]), sql.Identifier(OWNER_ROLE)
        )
    )
    # Refuse to silently take ownership of an unrelated pre-existing schema.
    existing_schema = conn.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=%s", (SCHEMA,)
    ).fetchone()
    if existing_schema and existing_schema[0] != OWNER_ROLE:
        raise RuntimeError(
            f"Schema {SCHEMA} belongs to {existing_schema[0]!r}. This setup does not transfer or drop existing schemas."
        )
print(f"Ownership role ready: {OWNER_ROLE}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 · Apply the real application migrations
# MAGIC
# MAGIC Run the next cell to execute **`alembic upgrade head`** using this repository's
# MAGIC migration files. Alembic records the applied revision in
# MAGIC `ecommerce.alembic_version`, so the database has a reproducible schema history.
# MAGIC
# MAGIC The initial migration creates these tables and their relationships:
# MAGIC
# MAGIC ```text
# MAGIC customers ──< orders >── products ──< inventory
# MAGIC                  │          │
# MAGIC                  └──< order_items
# MAGIC ```
# MAGIC
# MAGIC The migration connects with your short-lived OAuth credential and assumes the
# MAGIC shared owner role. Credentials are passed only to the migration subprocess;
# MAGIC they are not written into app source or printed as connection strings.
# MAGIC
# MAGIC **Expected output on the first run:** migration `0001` is applied. On a rerun,
# MAGIC Alembic checks the current revision and applies only outstanding migrations.
# MAGIC This cell creates the structure; the next step supplies the data.

# COMMAND ----------

username = w.current_user.me().user_name
credential = w.postgres.generate_database_credential(endpoint=target["endpoint"])
# Give only the Alembic child process its database credentials and owner role.
# Keep the notebook process environment unchanged and never print this dictionary.
env = os.environ.copy()
env.update(
    PGHOST=target["host"],
    PGPORT="5432",
    PGUSER=username,
    PGPASSWORD=credential.token,
    PGDATABASE=target["database_name"],
    PGSSLMODE="require",
    PGOPTIONS=f"-c role={OWNER_ROLE}",
    DB_SCHEMA=SCHEMA,
)
try:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )
finally:
    # Notebook cells retain globals, including after an exception.
    del credential, env
print("Alembic migrations applied.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 5 · Seed a small, usable store
# MAGIC
# MAGIC Run the next cell to insert deterministic workshop fixtures:
# MAGIC
# MAGIC | Table | Initial rows | What they enable |
# MAGIC | --- | ---: | --- |
# MAGIC | `customers` | 5 | Demo customer Alice Smith and sample buyers |
# MAGIC | `products` | 12 | A catalog across several categories |
# MAGIC | `inventory` | 12 | Stock availability and checkout checks |
# MAGIC | `orders` | 6 | Existing order history and best sellers |
# MAGIC | `order_items` | 6 | Line-item detail for the sample orders |
# MAGIC
# MAGIC **Customer ID 1 is Alice Smith**, the customer used by the current demo app.
# MAGIC The seed cell also advances all five table sequences so later app inserts receive
# MAGIC fresh IDs instead of colliding with the fixtures.
# MAGIC
# MAGIC ### What happens on a rerun?
# MAGIC
# MAGIC Existing fixture records and stock quantities are preserved; missing fixture IDs
# MAGIC are inserted. The seed cell does not reset purchases you made in the app. Counts
# MAGIC can therefore be larger than the initial values after you use the store.
# MAGIC
# MAGIC Use a dedicated database. Unrelated data that occupies fixture IDs or unique
# MAGIC email addresses can conflict; this is a workshop seed, not a general data merge.

# COMMAND ----------

# Stable fixture IDs make reruns reproducible. Prices are strings converted to
# Decimal below so order totals use exact decimal arithmetic.
PRODUCTS = [
    (1, "Wireless Headphones", "79.99", "Electronics"),
    (2, "Portable Speaker", "49.99", "Electronics"),
    (3, "USB-C Charger", "24.99", "Electronics"),
    (4, "Cotton T-Shirt", "19.99", "Clothing"),
    (5, "Lightweight Hoodie", "44.99", "Clothing"),
    (6, "Running Shoes", "89.99", "Clothing"),
    (7, "Coffee Maker", "59.99", "Home"),
    (8, "Desk Lamp", "34.99", "Home"),
    (9, "Insulated Water Bottle", "22.99", "Outdoors"),
    (10, "Yoga Mat", "29.99", "Sports"),
    (11, "Hiking Backpack", "69.99", "Outdoors"),
    (12, "Notebook Set", "12.99", "Stationery"),
]

# Authenticate this database session as the notebook's workspace user.
username = w.current_user.me().user_name
# Generate a fresh short-lived OAuth password for each connection attempt.
for attempt in range(4):
    credential = w.postgres.generate_database_credential(endpoint=target["endpoint"])
    try:
        conn = psycopg.connect(
            host=target["host"],
            dbname=target["database_name"],
            user=username,
            password=credential.token,
            sslmode="require",  # Encrypt the connection to Lakebase.
            connect_timeout=20,
            # Assume the shared owner role after authenticating as yourself.
            options=f"-c role={OWNER_ROLE}",
        )
        break
    except psycopg.OperationalError:
        if attempt == 3:
            raise
        # Allow a recently provisioned or sleeping endpoint time to become available.
        time.sleep(2**attempt)
# Do not leave the OAuth credential object in the notebook's global variables.
del credential
# The connection context commits on success, rolls back on error, and closes.
with conn:
    # Serialize concurrent seed runs. PostgreSQL releases this lock at transaction end.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('datacart-storefront-seed'))")
    # The app uses customer 1 as Alice; do not reuse that ID if it is unrelated data.
    existing = conn.execute(
        "SELECT email FROM ecommerce.customers WHERE id=1"
    ).fetchone()
    if existing and existing[0] != "alice@example.com":
        raise RuntimeError(
            "Customer ID 1 already belongs to different data; use a fresh workshop database."
        )
    with conn.cursor() as cur:
        # Insert missing fixture IDs only. ON CONFLICT preserves previous workshop work.
        cur.executemany(
            "INSERT INTO ecommerce.customers(id,name,email) VALUES(%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            [
                (1, "Alice Smith", "alice@example.com"),
                (2, "Bob Chen", "bob@example.com"),
                (3, "Charlie Davis", "charlie@example.com"),
                (4, "Diana Patel", "diana@example.com"),
                (5, "Ethan Wilson", "ethan@example.com"),
            ],
        )
        # Check existing product identities before inserting their matching inventory/orders.
        for product in PRODUCTS:
            row = cur.execute(
                "SELECT name FROM ecommerce.products WHERE id=%s", (product[0],)
            ).fetchone()
            if row and row[0] != product[1]:
                raise RuntimeError(
                    f"Product ID {product[0]} belongs to different data; use a fresh workshop database."
                )
        cur.executemany(
            "INSERT INTO ecommerce.products(id,name,price,category) VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            PRODUCTS,
        )
        # Preserve existing stock quantities, including changes made through checkout.
        cur.executemany(
            "INSERT INTO ecommerce.inventory(id,product_id,quantity,warehouse,reorder_level) VALUES(%s,%s,%s,'US-East',10) ON CONFLICT DO NOTHING",
            [(p[0], p[0], 5 if p[0] == 3 else 50 + p[0] * 3) for p in PRODUCTS],
        )
        # Insert both the legacy order summary and its matching line item.
        # The storefront reads these two representations in different views.
        for oid, customer, pid, qty, status in [
            (1, 1, 1, 1, "delivered"),
            (2, 1, 4, 2, "shipped"),
            (3, 2, 7, 1, "confirmed"),
            (4, 3, 9, 3, "delivered"),
            (5, 4, 2, 1, "pending"),
            (6, 5, 10, 2, "delivered"),
        ]:
            price = Decimal(PRODUCTS[pid - 1][2])
            total = price * qty
            cur.execute(
                "INSERT INTO ecommerce.orders(id,customer_id,product_id,quantity,total,currency,status,order_date) VALUES(%s,%s,%s,%s,%s,'USD',%s,'2026-01-15 10:00:00') ON CONFLICT(id) DO NOTHING",
                (oid, customer, pid, qty, total, status),
            )
            cur.execute(
                "INSERT INTO ecommerce.order_items(id,order_id,product_id,quantity,unit_price,line_total) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                (oid, oid, pid, qty, price, total),
            )
        # Explicit fixture IDs do not advance PostgreSQL sequences automatically.
        # Move each sequence forward without rewinding IDs already used by app writes.
        for table in ("customers", "products", "inventory", "orders", "order_items"):
            sequence = cur.execute(
                "SELECT pg_get_serial_sequence(%s,'id')", (f"ecommerce.{table}",)
            ).fetchone()[0]
            max_id = cur.execute(
                sql.SQL("SELECT COALESCE(MAX(id),1) FROM ecommerce.{}").format(
                    sql.Identifier(table)
                )
            ).fetchone()[0]
            last = cur.execute(
                sql.SQL("SELECT last_value FROM {}.{}").format(
                    sql.Identifier("ecommerce"), sql.Identifier(sequence.split(".")[-1])
                )
            ).fetchone()[0]
            cur.execute("SELECT setval(%s,%s,true)", (sequence, max(last, max_id)))
    # Report committed fixture sizes; counts can grow after workshop purchases.
    counts = {
        table: conn.execute(
            sql.SQL("SELECT count(*) FROM ecommerce.{}").format(sql.Identifier(table))
        ).fetchone()[0]
        # Explicit fixture IDs do not advance PostgreSQL sequences automatically.
        # Move each sequence forward without rewinding IDs already used by app writes.
        for table in ("customers", "products", "inventory", "orders", "order_items")
    }
print(json.dumps(counts, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6 · Verify the database before deployment
# MAGIC
# MAGIC Run the next cell. Confirm both of these results:
# MAGIC
# MAGIC 1. **Alembic revision:** `0001` for this version of the repository.
# MAGIC 2. **Demo customer:** `(1, 'Alice Smith', 'alice@example.com')`.
# MAGIC
# MAGIC Together with the row counts from the previous step, these checks confirm that
# MAGIC the schema and data needed for the basic storefront are ready.

# COMMAND ----------

# Authenticate this database session as the notebook's workspace user.
username = w.current_user.me().user_name
# Generate a fresh short-lived OAuth password for each connection attempt.
for attempt in range(4):
    credential = w.postgres.generate_database_credential(endpoint=target["endpoint"])
    try:
        conn = psycopg.connect(
            host=target["host"],
            dbname=target["database_name"],
            user=username,
            password=credential.token,
            sslmode="require",  # Encrypt the connection to Lakebase.
            connect_timeout=20,
            # Assume the shared owner role after authenticating as yourself.
            options=f"-c role={OWNER_ROLE}",
        )
        break
    except psycopg.OperationalError:
        if attempt == 3:
            raise
        # Allow a recently provisioned or sleeping endpoint time to become available.
        time.sleep(2**attempt)
# Do not leave the OAuth credential object in the notebook's global variables.
del credential
# The connection context commits on success, rolls back on error, and closes.
with conn:
    print(
        "Alembic revision:",
        conn.execute("SELECT version_num FROM ecommerce.alembic_version").fetchone()[0],
    )
    print(
        "Demo customer:",
        conn.execute(
            "SELECT id,name,email FROM ecommerce.customers WHERE id=1"
        ).fetchone(),
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## If a step fails
# MAGIC
# MAGIC | What you see | What to do |
# MAGIC | --- | --- |
# MAGIC | Project, branch, or database not found | Check the input variables against the Lakebase UI; rerun the input and discovery cells after correcting them |
# MAGIC | Database connection or login fails | Check endpoint availability, network access from compute, and your PostgreSQL OAuth login |
# MAGIC | Role creation or membership grant fails | Run as the project creator or an authorized database administrator |
# MAGIC | `ecommerce` is owned by a different role | Use a clean workshop database, or have its owner plan an explicit migration; this notebook will not take ownership automatically |
# MAGIC | Legacy tables exist without Alembic state | Use a fresh workshop database or an administrator-reviewed migration plan |
# MAGIC | Seed IDs or email addresses conflict | Use a dedicated workshop database rather than overwriting unrelated records |
# MAGIC
# MAGIC ## Checkpoint · Continue to notebook 3
# MAGIC
# MAGIC Open **3_create_datacart_app** and use the **same project ID, branch ID, and
# MAGIC PostgreSQL database name**. The next notebook grants the app its database access,
# MAGIC builds the frontend, and deploys the storefront.
# MAGIC
# MAGIC This notebook populates the **core store**. Reviews, loyalty, promotions, and
# MAGIC supplier-demand summaries need additional tables and are outside this setup.
# MAGIC An empty optional feature does not mean the core seed failed.
