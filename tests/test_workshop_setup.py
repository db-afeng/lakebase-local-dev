"""Execute notebook cells with mocked services to check resource safety."""

import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from databricks.sdk.errors import NotFound
from databricks.sdk.service import apps, postgres

NOTEBOOKS = Path(__file__).resolve().parents[1] / "notebooks"


def code_cells(name):
    return [
        cell.strip()
        for cell in (NOTEBOOKS / f"{name}.py").read_text().split("# COMMAND ----------")
        if "# MAGIC" not in cell and cell.strip()
    ]


def cell_containing(name, text):
    return next(cell for cell in code_cells(name) if text in cell)


def session(w, **inputs):
    if not isinstance(w.current_user.me().user_name, str):
        w.current_user.me.return_value.user_name = "workshop.user@example.com"
    namespace = {"__name__": "__notebook__"}
    with (
        patch("databricks.sdk.WorkspaceClient", return_value=w),
        redirect_stdout(io.StringIO()),
    ):
        exec(compile(code_cells("setup")[-1], "setup", "exec"), namespace)
    namespace.update(inputs)
    return namespace


def execute(code, namespace):
    with redirect_stdout(io.StringIO()):
        exec(compile(code, "notebook cell", "exec"), namespace)
    return namespace


class WorkshopSetupTest(unittest.TestCase):
    def test_resource_names_use_username_local_part(self):
        cases = {
            "alex.feng@databricks.com": "alex-feng",
            "Alex_Feng+work@example.com": "alex-feng-work",
            "a..b@example.com": "a--b",
            "-alice-@example.com": "alice",
            "person": "person",
        }
        for username, suffix in cases.items():
            with self.subTest(username=username):
                w = Mock()
                w.current_user.me.return_value.user_name = username
                ns = session(w)
                self.assertEqual(ns["USER_SUFFIX"], suffix)
                self.assertEqual(ns["DEFAULT_PROJECT_ID"], f"datacart-{suffix}")
                self.assertEqual(
                    ns["DEFAULT_APP_NAME"], f"datacart-storefront-{suffix}"
                )

    def test_resource_suffix_is_not_silently_truncated(self):
        w = Mock()
        w.current_user.me.return_value.user_name = (
            "verylongworkshopusername@example.com"
        )
        ns = session(w)
        self.assertEqual(
            ns["DEFAULT_APP_NAME"], "datacart-storefront-verylongworkshopusername"
        )
        ns.update(APP_NAME=ns["DEFAULT_APP_NAME"])
        with self.assertRaisesRegex(ValueError, "Override APP_NAME"):
            execute(
                cell_containing("2_create_datacart_app", "expected = apps.AppResource"),
                ns,
            )
        w.apps.get.assert_not_called()

    def test_project_display_name_uses_cleaned_username_suffix(self):
        w = Mock()
        w.current_user.me.return_value.user_name = "alex.feng@example.com"
        w.postgres.get_project.side_effect = NotFound("not created yet")
        ns = session(w, LAKEBASE_PROJECT_ID="datacart-alex-feng")
        execute(
            cell_containing(
                "0_optional_create_lakebase_project", "w.postgres.create_project"
            ),
            ns,
        )
        self.assertEqual(
            w.postgres.create_project.call_args.kwargs["project"].spec.display_name,
            "DataCart Storefront alex-feng",
        )

    def test_app_creation_waits_for_service_principal_without_starting_compute(self):
        w = Mock()
        pending = apps.App(name="demo-app")
        ready = apps.App(name="demo-app", service_principal_client_id="ready-client-id")
        w.apps.get.side_effect = [NotFound("not created yet"), pending, ready]
        w.apps.create.return_value.response = pending
        ns = session(
            w,
            APP_NAME="demo-app",
            target={"branch": "projects/demo/branches/production", "database": "db"},
        )
        ns["time"] = Mock()
        ns["time"].monotonic.return_value = 0
        execute(
            cell_containing("2_create_datacart_app", "expected = apps.AppResource"), ns
        )
        self.assertEqual(ns["client_id"], "ready-client-id")
        self.assertEqual(w.apps.get.call_count, 3)
        self.assertEqual(ns["time"].sleep.call_count, 2)
        w.apps.create.assert_called_once()
        w.apps.start.assert_not_called()

    def test_app_identity_wait_times_out(self):
        w = Mock()
        w.apps.get.side_effect = NotFound("not created yet")
        w.apps.create.return_value.response = apps.App(name="demo-app")
        ns = session(
            w,
            APP_NAME="demo-app",
            target={"branch": "projects/demo/branches/production", "database": "db"},
        )
        ns["time"] = Mock()
        ns["time"].monotonic.side_effect = [0, 301]
        with self.assertRaisesRegex(TimeoutError, "within 5 minutes"):
            execute(
                cell_containing("2_create_datacart_app", "expected = apps.AppResource"),
                ns,
            )
        w.apps.start.assert_not_called()

    def test_status_only_endpoint_and_distinct_database_ids(self):
        w = Mock()
        w.postgres.list_databases.return_value = [
            postgres.Database(
                name="projects/demo/branches/production/databases/databricks-postgres",
                status=postgres.DatabaseDatabaseStatus(
                    postgres_database="databricks_postgres"
                ),
            )
        ]
        endpoint = postgres.Endpoint(
            name="projects/demo/branches/production/endpoints/primary",
            status=postgres.EndpointStatus(
                endpoint_type=postgres.EndpointType.ENDPOINT_TYPE_READ_WRITE,
                hosts=postgres.EndpointHosts(host="db.example"),
            ),
        )
        w.postgres.list_endpoints.return_value = [endpoint]
        w.postgres.get_endpoint.return_value = endpoint
        ns = session(
            w,
            LAKEBASE_PROJECT_ID="projects/demo",
            BRANCH_ID="production",
            DATABASE_NAME="databricks_postgres",
        )
        execute(cell_containing("1_prepare_schema_and_seed", "databases = list"), ns)
        self.assertTrue(ns["target"]["database"].endswith("/databricks-postgres"))
        self.assertEqual(ns["target"]["database_name"], "databricks_postgres")
        self.assertEqual(ns["target"]["host"], "db.example")

    def test_invalid_project_ids(self):
        for value in (
            "",
            "UPPERCASE",
            "demo/branches/production",
            "has spaces",
            "ab",
            "trailing-",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                execute(
                    cell_containing("1_prepare_schema_and_seed", "databases = list"),
                    session(Mock(), LAKEBASE_PROJECT_ID=value),
                )

    def test_unrelated_app_is_not_rebound(self):
        w = Mock()
        w.apps.get.return_value = apps.App(name="existing-app", resources=[])
        ns = session(
            w,
            APP_NAME="existing-app",
            target={
                "branch": "projects/demo/branches/production",
                "database": "demo-db",
            },
        )
        with self.assertRaisesRegex(RuntimeError, "different database"):
            execute(
                cell_containing("2_create_datacart_app", "expected = apps.AppResource"),
                ns,
            )
        w.apps.create.assert_not_called()
        w.apps.create_update.assert_not_called()
        w.apps.deploy.assert_not_called()

    def test_shared_setup_and_procedural_notebook_inputs(self):
        notebooks = sorted(NOTEBOOKS.glob("*.py"))
        self.assertEqual(
            [p.stem for p in notebooks],
            [
                "0_optional_create_lakebase_project",
                "1_prepare_schema_and_seed",
                "2_create_datacart_app",
                "3_teardown_datacart",
                "setup",
            ],
        )
        self.assertFalse(
            (NOTEBOOKS.parent / "scripts" / "datacart_workshop.py").exists()
        )
        for notebook in notebooks:
            source = notebook.read_text()
            self.assertTrue(source.startswith("# Databricks notebook source\n"))
            tree = ast.parse(source)
            self.assertFalse(
                any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for node in ast.walk(tree)
                )
            )
            self.assertNotIn("dbutils.widgets", source)
            self.assertNotIn("datacart_workshop", source)
            self.assertFalse(
                any(
                    isinstance(node, ast.Name) and node.id == "workshop"
                    for node in ast.walk(tree)
                )
            )
            self.assertNotIn("restartPython", source)
            if notebook.stem == "setup":
                self.assertIn("# MAGIC %pip install", source)
                self.assertIn("w = WorkspaceClient()", source)
                continue
            cells = source.split("# COMMAND ----------")
            run_index = next(
                i for i, cell in enumerate(cells) if "# MAGIC %run ./setup" in cell
            )
            input_index = next(
                i
                for i, cell in enumerate(cells)
                if any(
                    isinstance(node, ast.Assign)
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "LAKEBASE_PROJECT_ID"
                    for node in ast.parse(cell).body
                )
            )
            self.assertLess(run_index, input_index)
            config = cells[input_index]
            names = {
                node.targets[0].id
                for node in ast.parse(config).body
                if isinstance(node, ast.Assign)
            }
            expected = {"LAKEBASE_PROJECT_ID"}
            if notebook.name.startswith(("1", "2")):
                expected |= {"BRANCH_ID", "DATABASE_NAME"}
            if notebook.name.startswith("2"):
                expected |= {"APP_NAME", "SOURCE_PATH"}
            if notebook.name.startswith("3"):
                expected |= {"APP_NAME", "DRY_RUN"}
                self.assertIn("DRY_RUN = True", config)
            self.assertEqual(names, expected)
            self.assertNotIn("%pip", source)
            self.assertNotIn("WorkspaceClient()", source)

    def teardown_client(self):
        w = Mock()
        w.apps.get.return_value = apps.App(
            name="demo-app",
            resources=[
                apps.AppResource(
                    name="postgres",
                    postgres=apps.AppResourcePostgres(
                        branch="projects/demo/branches/production",
                        database="projects/demo/branches/production/databases/db",
                    ),
                )
            ],
        )
        w.postgres.get_project.return_value = postgres.Project(name="projects/demo")
        w.postgres.list_branches.return_value = [
            postgres.Branch(name="projects/demo/branches/production")
        ]
        return w

    def teardown(self, w, project="demo", dry_run=True):
        ns = session(
            w, LAKEBASE_PROJECT_ID=project, APP_NAME="demo-app", DRY_RUN=dry_run
        )
        execute(cell_containing("3_teardown_datacart", "plan ="), ns)
        execute(cell_containing("3_teardown_datacart", "w.apps.delete"), ns)
        return ns["plan"]

    def test_teardown_preview_never_deletes(self):
        w = self.teardown_client()
        plan = self.teardown(w)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["branches"], ["projects/demo/branches/production"])
        w.apps.delete.assert_not_called()
        w.postgres.delete_project.assert_not_called()

    def test_teardown_resolves_ui_uuid_to_readable_project_id(self):
        w = self.teardown_client()
        project_uuid = "12345678-1234-1234-1234-123456789abc"
        w.postgres.list_projects.return_value = [
            postgres.Project(name="projects/demo", uid=project_uuid)
        ]
        plan = self.teardown(w, project=project_uuid)
        self.assertEqual(plan["project"], "projects/demo")
        self.assertEqual(plan["project_input"], project_uuid)
        w.postgres.list_projects.assert_called_once_with(show_deleted=True)
        w.postgres.list_branches.assert_called_once_with(parent="projects/demo")
        w.apps.delete.assert_not_called()

    def test_teardown_does_not_delete_soft_deleted_project_again(self):
        w = self.teardown_client()
        w.apps.get.side_effect = NotFound("gone")
        w.postgres.get_project.return_value = postgres.Project(
            name="projects/demo", delete_time="2026-11-17T00:00:00Z"
        )
        plan = self.teardown(w, dry_run=False)
        self.assertFalse(plan["project_exists"])
        w.postgres.list_branches.assert_not_called()
        w.postgres.delete_project.assert_not_called()

    def test_teardown_error_shows_selected_and_bound_project(self):
        w = self.teardown_client()
        with self.assertRaisesRegex(
            RuntimeError,
            "projects/demo-other.*Actual Lakebase bindings:.*projects/demo/branches/production",
        ):
            self.teardown(w, "demo-other", False)
        w.apps.delete.assert_not_called()

    def test_teardown_refuses_other_project_before_deletion(self):
        w = self.teardown_client()
        with self.assertRaisesRegex(RuntimeError, "not bound exclusively"):
            self.teardown(w, "demo-other", False)
        w.apps.delete.assert_not_called()
        w.postgres.delete_project.assert_not_called()

    def test_teardown_deletes_app_before_project_and_keeps_retention(self):
        w = self.teardown_client()
        w.apps.get.side_effect = [w.apps.get.return_value, NotFound("gone")]
        self.teardown(w, dry_run=False)
        w.apps.delete.assert_called_once_with(name="demo-app")
        w.postgres.delete_project.assert_called_once_with(
            name="projects/demo", purge=False
        )
        w.postgres.delete_project.return_value.wait.assert_called_once_with()
        calls = [str(call) for call in w.mock_calls]
        self.assertLess(
            calls.index("call.apps.delete(name='demo-app')"),
            calls.index(
                "call.postgres.delete_project(name='projects/demo', purge=False)"
            ),
        )

    def test_teardown_app_failure_preserves_project(self):
        w = self.teardown_client()
        w.apps.delete.side_effect = RuntimeError("deletion failed")
        with self.assertRaisesRegex(RuntimeError, "deletion failed"):
            self.teardown(w, dry_run=False)
        w.postgres.delete_project.assert_not_called()

    def test_teardown_rerun_skips_absent_resources(self):
        w = self.teardown_client()
        w.apps.get.side_effect = NotFound("gone")
        w.postgres.get_project.side_effect = NotFound("gone")
        self.teardown(w, dry_run=False)
        w.apps.delete.assert_not_called()
        w.postgres.delete_project.assert_not_called()

    def test_teardown_permission_error_is_not_absence(self):
        w = self.teardown_client()
        w.postgres.get_project.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            self.teardown(w, dry_run=False)
        w.apps.delete.assert_not_called()
        w.postgres.delete_project.assert_not_called()

    def test_teardown_input_change_requires_new_preview(self):
        w = self.teardown_client()
        ns = session(w, LAKEBASE_PROJECT_ID="demo", APP_NAME="demo-app", DRY_RUN=True)
        execute(cell_containing("3_teardown_datacart", "plan ="), ns)
        ns["DRY_RUN"] = False
        with self.assertRaisesRegex(RuntimeError, "Inputs changed"):
            execute(cell_containing("3_teardown_datacart", "w.apps.delete"), ns)
        w.apps.delete.assert_not_called()
        w.postgres.delete_project.assert_not_called()


if __name__ == "__main__":
    unittest.main()
