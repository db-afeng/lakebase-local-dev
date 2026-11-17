# Databricks notebook source
# MAGIC %md
# MAGIC # 4 · Tear down DataCart Storefront
# MAGIC
# MAGIC **Workshop goal:** remove the Databricks App and the Lakebase project used for
# MAGIC this workshop when you have finished exploring the storefront.
# MAGIC
# MAGIC This notebook deletes the **entire project**, including every branch, endpoint,
# MAGIC database, PostgreSQL role, and all data. This includes development branches
# MAGIC created during local development. Use a project dedicated to this workshop;
# MAGIC a project created through clickops has exactly the same deletion scope.
# MAGIC Export any data you want to keep before continuing. Other apps or clients using
# MAGIC this project will lose their database connection.
# MAGIC
# MAGIC Run as an identity permitted to delete both resources. The app's dedicated
# MAGIC service principal is deleted with the app. This notebook retains the repository,
# MAGIC workshop notebooks, workspace source-release directories, and local files.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 1 · Run shared setup
# MAGIC
# MAGIC Run the next cell to initialize the workshop libraries and workspace client.
# MAGIC Shared prerequisites and troubleshooting are documented in **setup**.
# MAGIC This step does not delete anything.

# COMMAND ----------

# MAGIC %run ./setup

# COMMAND ----------

# MAGIC %md
# MAGIC ## Your inputs
# MAGIC
# MAGIC Enter the project ID used in notebooks 1–3 and the **exact app name printed by
# MAGIC notebook 3**. A full `projects/<id>` path or the project UUID is accepted. The app name is deliberately
# MAGIC required, including when notebook 3 generated it from your username.
# MAGIC A UUID is resolved to its readable project resource ID before checking bindings.
# MAGIC Existing resources keep their original names; copy the names actually used to create them.
# MAGIC
# MAGIC Keep `DRY_RUN = True` for the first pass. Review the preview, then change it to
# MAGIC `False` and rerun the input block, preview, and deletion cells.
# MAGIC No widgets, tokens, CLI profiles, branch IDs, or database names are needed.

# COMMAND ----------

LAKEBASE_PROJECT_ID = ""  # Required: the dedicated workshop project to delete.
APP_NAME = ""  # Required: copy the exact name printed by notebook 3.
DRY_RUN = True  # Preview first; set False only when ready to delete both resources.

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 2 · Preview the selected resources
# MAGIC
# MAGIC The next cell prints the app name, project path, resource existence, and the
# MAGIC project's branches. This cell always performs read-only discovery.
# MAGIC Confirm these are your workshop resources and that **all** project data can go.
# MAGIC The branch list describes the current project; it does not restrict deletion.
# MAGIC
# MAGIC The preview checks the app's Lakebase bindings against your project. The next
# MAGIC execution step, with `DRY_RUN = False`, deletes the app, waits for it to disappear, then deletes the
# MAGIC project and waits for that operation. If app deletion fails, project deletion
# MAGIC is not started. Missing resources are skipped, allowing a rerun after a partial
# MAGIC teardown. Permission errors are surfaced rather than treated as missing resources.
# MAGIC
# MAGIC The deletion cell requests normal project deletion with `purge=False`, preserving
# MAGIC the platform's retention behavior. The current API documents a seven-day
# MAGIC recovery period before permanent deletion; verify availability in your
# MAGIC workspace if recovery is needed. App deletion is a separate operation.

# COMMAND ----------

# Invalidate an earlier preview before any validation or API call can fail.
plan = None
# The UI may show the project's UUID (uid), while Apps bindings use its readable ID.
# Resolve UUID input through Lakebase instead of comparing unlike identifiers.
project_input = LAKEBASE_PROJECT_ID.strip().removeprefix("projects/")
project_is_uuid = bool(
    re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        project_input,
    )
)
if project_is_uuid:
    matching_projects = [
        item
        for item in w.postgres.list_projects(show_deleted=True)
        if item.uid and item.uid.lower() == project_input.lower()
    ]
    if len(matching_projects) != 1:
        raise ValueError(
            "Project UUID was not found uniquely. Enter its readable project ID "
            "(for example datacart-alex-feng), or check your workspace access."
        )
    project_resource = matching_projects[0]
    project = project_resource.name
    print(f"Resolved project UUID {project_input} to {project}")
else:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", project_input):
        raise ValueError(
            "Enter the readable Lakebase project ID, a projects/<id> path, or its UUID."
        )
    project = f"projects/{project_input}"
    try:
        project_resource = w.postgres.get_project(name=project)
    except NotFound:
        project_resource = None

# A soft-deleted project can still be returned by get_project during retention.
# Treat it as already deleted; listing its branches or deleting it again is unnecessary.
project_exists = project_resource is not None and not project_resource.delete_time
print(f"Selected project: {project}")
# Require an explicit app name rather than inferring which resource to delete.
APP_NAME = APP_NAME.strip()
if not re.fullmatch("[a-z][a-z0-9-]{0,28}[a-z0-9]", APP_NAME):
    raise ValueError(
        "Enter the exact app name from notebook 3 (2–30 lowercase letters, digits, hyphens)."
    )
if type(DRY_RUN) is not bool:
    raise ValueError("DRY_RUN must be the Python boolean True or False.")
# Only NotFound counts as already deleted; permission and service errors must stop us.
try:
    app = w.apps.get(name=APP_NAME)
except NotFound:
    app = None
if app is not None:
    bindings = [r.postgres for r in app.resources or [] if r.postgres]
    # Match full resource path boundaries so project demo cannot match demo-other.
    # Every Lakebase binding on this app must belong to the selected project.
    if not bindings or any(
        (
            not binding.branch
            or not binding.branch.startswith(project + "/branches/")
            or (not binding.database)
            or (not binding.database.startswith(binding.branch + "/databases/"))
            for binding in bindings
        )
    ):
        actual_bindings = [
            {"branch": binding.branch, "database": binding.database}
            for binding in bindings
        ]
        raise RuntimeError(
            f"App {APP_NAME!r} is not bound exclusively to selected project {project!r}. "
            f"Actual Lakebase bindings: {json.dumps(actual_bindings)}. "
            "Check LAKEBASE_PROJECT_ID and APP_NAME; nothing was deleted."
        )
# Inventory active branches only; the entire project remains the deletion scope.
branches = (
    [branch.name for branch in w.postgres.list_branches(parent=project)]
    if project_exists
    else []
)
# This preview is also the input snapshot checked by the deletion cell below.
plan = {
    "app": APP_NAME,
    "app_exists": app is not None,
    "project": project,
    "project_input": project_input,
    "project_exists": project_exists,
    "branches": branches,
    "dry_run": DRY_RUN,
    "scope": "Entire project, including every branch, endpoint, database, role, and all data",
}
print(json.dumps(plan, indent=2))
result = plan
print("Preview only. No resources were deleted.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 3 · Delete the reviewed resources
# MAGIC
# MAGIC This cell only deletes when `DRY_RUN = False`. After changing inputs, rerun the
# MAGIC input block and preview before this cell. The guard requires the preview to match
# MAGIC the current inputs. App deletion must complete before project deletion starts.

# COMMAND ----------

# Refuse to use a failed preview or a preview made with different input values.
if (
    not isinstance(plan, dict)
    or plan["app"] != APP_NAME.strip()
    or plan["project_input"] != LAKEBASE_PROJECT_ID.strip().removeprefix("projects/")
    or plan["dry_run"] != DRY_RUN
):
    raise RuntimeError("Inputs changed. Rerun the preview before deleting.")
# The default True value makes Run all safe for previewing resource selection.
if DRY_RUN:
    print("DRY_RUN is True; deletion skipped.")
else:
    if app is not None:
        # Delete the app and its dedicated service principal before removing its database.
        w.apps.delete(name=APP_NAME)
        # App deletion may finish asynchronously. Never delete the project while it remains.
        deadline = time.monotonic() + 120
        while True:
            try:
                w.apps.get(name=APP_NAME)
            except NotFound:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "App deletion is still in progress. Project deletion was not started; rerun later."
                )
            time.sleep(2)
        print(f"Deleted app: {APP_NAME}")
    else:
        print(f"App already absent: {APP_NAME}")
    if project_exists:
        # Delete the entire project through its lifecycle API, retaining normal recovery.
        # Do not delete branches individually or request immediate permanent purge.
        w.postgres.delete_project(name=project, purge=False).wait()
        print(f"Deleted project: {project}")
    else:
        print(f"Project already absent: {project}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Checkpoint · Confirm cleanup
# MAGIC
# MAGIC A preview ends with **Preview only. No resources were deleted.** Actual deletion
# MAGIC prints **Deleted app** and **Deleted project**, or **already absent** on a rerun.
# MAGIC Check the Apps and Lakebase workspace pages to confirm the resources are removed
# MAGIC from active use. A deleted Lakebase project may remain visible during retention.
# MAGIC
# MAGIC Source-release folders under your workspace user directory remain available
# MAGIC for inspection. Remove your own release folders manually if desired. To stop
# MAGIC a local development stack, run `make dev-down` in that checkout before removing it.
# MAGIC Update `lakebase.config` before starting local development against a new project.
# MAGIC
# MAGIC ## If a step fails
# MAGIC
# MAGIC | What you see | What to do |
# MAGIC | --- | --- |
# MAGIC | Empty or invalid input | Copy the project ID and exact app name from your workshop output, then rerun the input block |
# MAGIC | App is bound to a different project, or has no Lakebase binding | Compare the selected path and actual binding paths printed in the error. Use the project attached to this app; the preview refuses deletion rather than guessing |
# MAGIC | Permission denied | Use the resource owner or an administrator with permission to delete these resources |
# MAGIC | App deletion timeout | Wait and inspect the app in the UI, then rerun; the project has not been deleted by this attempt |
# MAGIC | Project deletion fails after the app was deleted | Inspect the Lakebase error and rerun after resolving it; the missing app is skipped |
# MAGIC
# MAGIC Keep `DRY_RUN = True` in the notebook version you share with other participants.
