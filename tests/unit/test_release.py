"""Real filesystem release tests. The Docker failure test uses an explicit fake runner."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("northstar_release", ROOT/"scripts"/"release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base/"source"
        self.store = self.base/"store"
        self.source.mkdir()
        for name in release.ROOT_FILES:
            (self.source/name).write_text("fixture " + name)
        (self.source/"apps").mkdir()
        (self.source/"apps"/"main.py").write_text("VERSION = 1")
        (self.source/"migrations").mkdir()
        (self.source/"migrations"/"initial.py").write_text("schema 1")

    def tearDown(self):
        self.temporary.cleanup()

    def versions(self):
        one = release.stage(self.source, self.store, "v1")
        (self.source/"apps"/"main.py").write_text("VERSION = 2")
        two = release.stage(self.source, self.store, "v2")
        return one, two

    def test_stage_idempotent_and_no_private_files(self):
        (self.source/".env").write_text("PRIVATE_SENTINEL=never publish")
        (self.source/"apps"/".env.production").write_text("PRIVATE_SENTINEL=never publish")
        (self.source/"apps"/"node_modules").mkdir()
        (self.source/"apps"/"node_modules"/"secret.txt").write_text("never publish")
        identity = release.stage(self.source, self.store, "v1")
        self.assertEqual(identity, release.stage(self.source, self.store, "v1"))
        manifest = release.verify(self.store, identity)
        self.assertTrue(all(".env" not in name and "node_modules" not in name for name in manifest["files"]))

    def test_file_tampering_and_unmanifested_file_rejected(self):
        identity = release.stage(self.source, self.store, "v1")
        snapshot = self.store/identity
        original = (snapshot/"apps"/"main.py").read_text()
        (snapshot/"apps"/"main.py").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            release.verify(self.store, identity)
        (snapshot/"apps"/"main.py").write_text(original)
        (snapshot/"extra.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "Unmanifested"):
            release.verify(self.store, identity)

    def test_activate_and_rollback_persist_across_reloads(self):
        one, two = self.versions()
        release.activate(self.store, one)
        release.activate(self.store, two)
        state = release.rollback(self.store)
        self.assertEqual(state["active"], one)
        self.assertEqual(state["previous"], two)
        self.assertEqual(release.status(self.store), state)
        self.assertFalse(state["deployed"])

    def test_repeat_activation_preserves_prior_release(self):
        one, two = self.versions()
        release.activate(self.store, one)
        release.activate(self.store, two)
        release.activate(self.store, two)
        self.assertEqual(release.status(self.store)["previous"], one)

    def test_mutation_lock_prevents_concurrent_release_change(self):
        one, _ = self.versions()
        with release.release_lock(self.store):
            with self.assertRaisesRegex(ValueError, "Another release"):
                release.activate(self.store, one)

    def test_expected_state_blocks_stale_rollback(self):
        one, two = self.versions()
        release.activate(self.store, one)
        previous = release.status(self.store)
        release.activate(self.store, two)
        with self.assertRaisesRegex(ValueError, "Active release changed"):
            release.activate(self.store, one, expected_state=previous)
        self.assertEqual(release.status(self.store)["active"], two)

    def test_failed_deployment_runs_previous_release_without_deleting_volumes(self):
        one, two = self.versions()
        release.activate(self.store, one)
        old = dict(release.status(self.store), deployed=True, status="deployed_ready")
        release.atomic_json(self.store/"state.json", old)
        env_file = self.base/"private.env"
        env_file.write_text("fixture only")
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs["env"]["NORTHSTAR_IMAGE"]))
            if kwargs["env"]["NORTHSTAR_IMAGE"].endswith(two):
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)
        with self.assertRaisesRegex(RuntimeError, "prior deployed release was restored"):
            release.activate(self.store, two, deploy=True, env_file=env_file, runner=runner)
        self.assertEqual(release.status(self.store), old)
        self.assertTrue(any(image.endswith(one) for _, image in calls))
        self.assertTrue(all("down" not in command and "--volumes" not in command for command, _ in calls))

    def test_schema_change_requires_explicit_compatibility_review(self):
        one, _ = self.versions()
        release.activate(self.store, one)
        (self.source/"migrations"/"initial.py").write_text("schema changed")
        new = release.stage(self.source, self.store, "v3")
        with self.assertRaisesRegex(ValueError, "Migration files changed"):
            release.activate(self.store, new, deploy=True, env_file=self.base/"unused.env")

    def test_private_env_generation_uses_distinct_secrets_and_refuses_overwrite(self):
        path = self.base/"private"/"production.env"
        result = release.init_env(path, "northstar.example.com", "operator@example.com")
        values = dict(line.split("=", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))
        names = [name for name in values if name.endswith(("PASSWORD", "TOKEN", "SECRET"))]
        self.assertEqual(len(names), 10)
        self.assertEqual(len(set(values[name] for name in names)), 10)
        self.assertTrue(all(len(values[name]) >= 32 for name in names))
        self.assertFalse(result["cloud_activated"])
        with self.assertRaises(FileExistsError):
            release.init_env(path, "northstar.example.com", "operator@example.com")

    def test_unsafe_identifiers_and_hosts_rejected(self):
        with self.assertRaises(ValueError):
            release.stage(self.source, self.store, "../escape")
        with self.assertRaises(ValueError):
            release.verify(self.store, "../escape")
        with self.assertRaises(ValueError):
            release.init_env(self.base/"invalid.env", "https://example.com/path", "operator@example.com")

    def test_production_networks_and_secret_scopes(self):
        import yaml
        config = yaml.safe_load((ROOT/"compose.production.yaml").read_text())
        services = config["services"]
        self.assertEqual([name for name, value in services.items() if value.get("ports")], ["edge"])
        self.assertTrue(config["networks"]["private"]["internal"])
        for name in ("api", "worker", "crm", "support", "migrate", "seed"):
            self.assertTrue(services[name]["read_only"])
            self.assertEqual(services[name]["cap_drop"], ["ALL"])
        self.assertNotIn("CIR_MIGRATION_DATABASE_URL", services["api"]["environment"])
        self.assertIn("CIR_MIGRATION_DATABASE_URL", services["migrate"]["environment"])
        self.assertNotIn("SUPPORT_ADMIN_TOKEN", services["crm"]["environment"])
        self.assertNotIn("CRM_ADMIN_TOKEN", services["support"]["environment"])
        self.assertNotIn("CIR_ADMIN_URL", services["api"]["environment"])

    def test_production_proxy_hides_private_ingress_and_image_has_no_env_copy(self):
        caddy = (ROOT/"deploy"/"Caddyfile").read_text()
        self.assertIn("/api/v1/events/*", caddy)
        self.assertIn("respond @internal 404", caddy)
        dockerfile = (ROOT/"Dockerfile.production").read_text()
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertNotIn("COPY . ", dockerfile)
        self.assertNotIn("COPY .env", dockerfile)


if __name__ == "__main__":
    unittest.main()
