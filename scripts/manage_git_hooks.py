#!/usr/bin/env python3
"""Install a repository-scoped hook dispatcher without modifying existing hooks."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

# Include all standard Git hooks, even those not present at installation time.
HOOKS = """applypatch-msg pre-applypatch post-applypatch pre-commit pre-merge-commit
prepare-commit-msg commit-msg post-commit pre-rebase post-checkout post-merge
pre-push pre-receive update proc-receive post-receive post-update
reference-transaction push-to-checkout pre-auto-gc post-rewrite sendemail-validate
fsmonitor-watchman p4-changelist p4-prepare-changelist p4-post-changelist
p4-pre-submit post-index-change""".split()
MARKER = "# datacart-storefront standard Git hook dispatcher"


def git(*args, check=True):
    return subprocess.run(
        ["git", *args], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=check,
    )


def values(scope):
    result = git("config", scope, "--null", "--get-all", "core.hooksPath", check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip())
    return result.stdout.rstrip("\0").split("\0") if result.returncode == 0 else []


def effective_path():
    result = git("config", "--path", "--get", "core.hooksPath", check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip())
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def is_managed(path):
    return path and (Path(path) / "state.json").is_file() and (
        Path(path) / "post-checkout"
    ).is_file() and MARKER in (Path(path) / "post-checkout").read_text()


def install():
    root = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    git_dir = Path(git("rev-parse", "--absolute-git-dir").stdout.strip())
    os.chdir(root)
    previous_path = effective_path()
    if is_managed(previous_path):
        state = json.loads((Path(previous_path) / "state.json").read_text())
        if state["common_dir"] != str(common):
            raise RuntimeError("Active DataCart hooks belong to another repository.")
        print("DataCart checkout hook is already installed.")
        return

    # Respect an explicit worktree override without changing sibling worktrees.
    enabled = git("config", "--bool", "extensions.worktreeConfig", check=False).stdout.strip() == "true"
    scope = "--worktree" if enabled and values("--worktree") else "--local"
    base = git_dir if scope == "--worktree" else common
    destination = base / "datacart-hooks"
    if destination.exists():
        raise RuntimeError(f"Refusing to overwrite existing hook state: {destination}")
    old_values = values(scope)
    original = previous_path if previous_path is not None else str(common / "hooks")
    original_dir = Path(original)
    if not original_dir.is_absolute():
        original_dir = root / original_dir
    if original_dir.resolve() == destination.resolve():
        raise RuntimeError("Existing core.hooksPath points to the reserved DataCart hook directory.")
    names = set(HOOKS)
    if original_dir.is_dir():
        names.update(p.name for p in original_dir.iterdir() if p.is_file() and not p.name.endswith(".sample"))
    # Git hook names never contain directory separators; avoid overwriting our metadata.
    names.difference_update({"state.json", "dispatch.py"})

    stage = Path(tempfile.mkdtemp(prefix="datacart-hooks-", dir=base))
    try:
        state = {
            "version": 1, "scope": scope, "previous_values": old_values,
            "original_hooks_path": original, "common_dir": str(common),
        }
        (stage / "state.json").write_text(json.dumps(state, indent=2) + "\n")
        shutil.copyfile(__file__, stage / "dispatch.py")
        for name in names:
            wrapper = stage / name
            wrapper.write_text(
                "#!/usr/bin/env bash\n" + MARKER + "\nexec python3 "
                + shlex.quote(str(destination / "dispatch.py"))
                + " dispatch " + shlex.quote(name) + ' "$@"\n'
            )
            wrapper.chmod(0o755)
        stage.rename(destination)
        result = git("config", scope, "--replace-all", "core.hooksPath", str(destination), check=False)
        if result.returncode:
            shutil.rmtree(destination)
            raise RuntimeError(result.stderr.strip())
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"Installed DataCart checkout hook at {destination}")
    print(f"Existing hooks are forwarded to: {original}")


def uninstall():
    current = effective_path()
    if not is_managed(current):
        print("No active DataCart hook installation; hook configuration left unchanged.")
        return
    directory = Path(current)
    state = json.loads((directory / "state.json").read_text())
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if state["common_dir"] != common:
        raise RuntimeError("Hook state belongs to another repository; refusing to change it.")
    scope = state["scope"]
    if values(scope) != [current]:
        raise RuntimeError("Hook configuration changed after installation; refusing to overwrite it.")
    # Refuse to discard edits or additions made inside the managed directory.
    for path in directory.iterdir():
        if path.name in {"state.json", "dispatch.py"}:
            continue
        expected = (
            "#!/usr/bin/env bash\n" + MARKER + "\nexec python3 "
            + shlex.quote(str(directory / "dispatch.py"))
            + " dispatch " + shlex.quote(path.name) + ' "$@"\n'
        )
        if not path.is_file() or path.read_text() != expected:
            raise RuntimeError(f"Managed hook was modified: {path}; preserve it before uninstalling.")
    git("config", scope, "--unset-all", "core.hooksPath")
    for value in state["previous_values"]:
        git("config", scope, "--add", "core.hooksPath", value)
    shutil.rmtree(directory)
    print("Removed DataCart hooks and restored the previous hook configuration.")


def dispatch(name, args):
    directory = Path(__file__).resolve().parent
    state = json.loads((directory / "state.json").read_text())
    original = Path(state["original_hooks_path"]) / name
    status = 0
    if original.is_file() and os.access(original, os.X_OK):
        # Avoid duplicate provisioning if a legacy dispatcher calls our repo hook.
        env = os.environ.copy()
        if name == "post-checkout":
            env["DATACART_SKIP_POST_CHECKOUT"] = "1"
        status = subprocess.run([str(original.absolute()), *args], env=env).returncode
    if name == "post-checkout":
        root = git("rev-parse", "--show-toplevel", check=False)
        common = git("rev-parse", "--path-format=absolute", "--git-common-dir", check=False)
        if root.returncode == 0 and common.stdout.strip() == state["common_dir"]:
            hook = Path(root.stdout.strip()) / ".githooks/post-checkout"
            if hook.is_file() and os.access(hook, os.X_OK):
                subprocess.run([str(hook), *args], check=False)
    return status if status >= 0 else 128 - status


if __name__ == "__main__":
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "install":
            install()
        elif len(sys.argv) == 2 and sys.argv[1] == "uninstall":
            uninstall()
        elif len(sys.argv) >= 3 and sys.argv[1] == "dispatch":
            sys.exit(dispatch(sys.argv[2], sys.argv[3:]))
        else:
            raise RuntimeError("Usage: manage-post-checkout-hook.sh install|uninstall")
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print(f"hooks: {error}", file=sys.stderr)
        sys.exit(1)
