import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LIB = SCRIPTS / "lib"
OPS = SCRIPTS / "ops"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class InstallerPlanCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = read(SCRIPTS / "install_safe.sh")
        cls.dispatch = read(LIB / "install_safe_dispatch.sh")
        cls.activation = read(LIB / "install_safe_activation_policy.sh")
        cls.tls = read(LIB / "install_safe_tls_policy.sh")
        cls.postgres = read(LIB / "install_safe_postgres_ownership.sh")
        cls.proxy = read(LIB / "install_safe_proxy_mode.sh")
        cls.platform_support = read(LIB / "install_safe_platform_support.sh")
        cls.package_policy = read(LIB / "install_safe_package_policy.sh")
        cls.release_contract = read(LIB / "install_safe_release_contract.sh")
        cls.control = read(LIB / "control_plane_completion.sh")
        cls.uninstall_loader = read(SCRIPTS / "uninstall_foundation.sh")
        cls.uninstall_ownership = read(LIB / "uninstall_safe_ownership.sh")
        cls.support = read(OPS / "support_bundle.sh")
        cls.repair = read(OPS / "repair.sh")

    def test_all_safety_policy_modules_are_reachable(self):
        ordered = [
            "install_safe_platform.sh",
            "install_safe_platform_support.sh",
            "install_safe_release_contract.sh",
            "install_safe_lock_policy.sh",
            "install_safe_legacy.sh",
            "install_safe_redis_transition.sh",
            "install_safe_runtime.sh",
            "install_safe_tls_policy.sh",
            "install_safe_postgres_ownership.sh",
            "install_safe_proxy_mode.sh",
            "install_safe_package_policy.sh",
            "install_safe_activation_policy.sh",
            "install_safe_failure_injection.sh",
            "install_safe_dispatch.sh",
        ]
        positions = [self.loader.index(name) for name in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("run_direct_deploy_state_gate", self.loader)
        for module in ordered:
            self.assertIn(module, self.release_contract)

    def test_platform_policy_keeps_ubuntu_2404_primary_but_not_mandatory(self):
        self.assertIn("ubuntu|debian", self.platform_support)
        self.assertIn("Ubuntu 24.04 является primary CI target", self.platform_support)
        self.assertIn("detected_platform", self.platform_support)
        self.assertIn("platform_support", self.platform_support)
        self.assertNotIn("поддерживается только Ubuntu 24.04", self.platform_support)

    def test_package_policy_is_shared_host_safe(self):
        for marker in (
            "package_is_installed",
            "--no-install-recommends",
            "packages+=(nginx certbot)",
            'if [[ "$PROXY_MODE" == managed ]]',
            "capture_unit_state redis-server.service",
            "restore_unit_state redis-server.service",
            "сразу возвращается",
        ):
            self.assertIn(marker, self.package_policy)
        self.assertNotIn("apt-get upgrade", self.package_policy)
        self.assertNotIn("ufw ", self.package_policy)

    def test_dedicated_redis_is_inside_transactional_activation(self):
        self.assertIn("foundation_setup_dedicated_redis", self.activation)
        self.assertNotIn("foundation_setup_dedicated_redis", self.dispatch)
        self.assertIn("ACTIVATION_COMMAND=(activate_release_bundle)", self.dispatch)
        self.assertIn("run_application_transaction", self.dispatch)

    def test_first_install_has_failure_injection_and_automatic_rollback(self):
        for point in (
            "after-journal",
            "after-packages",
            "after-manifest",
            "after-service-user",
            "after-postgresql",
            "before-application-transaction",
            "after-application-transaction",
        ):
            self.assertIn(f"installer_failpoint {point}", self.dispatch)
        for point in (
            "after-dedicated-redis",
            "after-operational-tooling",
            "after-proxy-activation",
            "after-systemd",
            "after-cli",
        ):
            self.assertIn(f"installer_failpoint {point}", self.activation)
        self.assertIn("rollback_empty_pre_manifest_journal", self.dispatch)
        self.assertIn("automatic_initial_rollback", self.dispatch)
        self.assertIn("--incomplete-install", self.dispatch)

    def test_managed_nginx_never_starts_global_service(self):
        self.assertNotIn("enable --now nginx", self.tls)
        self.assertNotIn("systemctl start nginx", self.tls)
        self.assertIn("systemctl is-active --quiet nginx", self.tls)
        self.assertIn("installer не включает и не запускает", self.tls)
        self.assertIn("CERTIFICATE_CONFLICT", self.tls)

    def test_postgres_objects_are_bound_to_installation_id(self):
        self.assertIn("COMMENT ON DATABASE", self.postgres)
        self.assertIn("COMMENT ON ROLE", self.postgres)
        self.assertIn("installation-id=%s", self.postgres)
        self.assertIn("postgres_assert_ownership_comments", self.postgres)
        self.assertIn("database ownership COMMENT", self.uninstall_ownership)
        self.assertIn("role ownership COMMENT", self.uninstall_ownership)

    def test_external_proxy_mode_is_non_mutating_for_global_proxy(self):
        self.assertIn("PROXY_MODE", self.proxy)
        self.assertIn("external-proxy.nginx.conf", self.proxy)
        self.assertIn("Application binds only to loopback", self.proxy)
        self.assertIn("OPERATIONAL_NGINX=false", self.proxy)
        self.assertIn("choose_internal_webhook_port", self.proxy)
        self.assertNotIn("systemctl reload nginx", self.proxy)
        self.assertIn("packages+=(nginx certbot)", self.package_policy)

    def test_operator_control_plane_has_required_workflows(self):
        for command in (
            "doctor --json",
            "repair --check|--apply",
            "support-bundle",
            "proxy-config",
            "deploy --external-proxy",
        ):
            self.assertIn(command, self.control)
        self.assertIn("read_install_state", self.control)
        self.assertIn("menu_blocked", self.control)
        self.assertIn("Mutating actions are hidden", self.control)

    def test_support_bundle_is_external_and_secret_safe(self):
        self.assertIn("OUTPUT_DIR=/root/just1kbot-support-bundles", self.support)
        self.assertNotIn("OUTPUT_DIR=/var/lib/just1kbot", self.support)
        self.assertIn("doctor_complete.sh", self.support)
        self.assertIn("BOT_TOKEN", self.support)
        self.assertIn("AGE-SECRET-KEY", self.support)
        self.assertNotIn('cp -- /opt/just1kbot/.env', self.support)
        self.assertNotIn("pg_dump", self.support)

    def test_repair_is_manifest_bounded(self):
        self.assertIn("foundation_manifest_require", self.repair)
        self.assertIn("Repair refused: ownership proof missing", self.repair)
        self.assertNotIn("setup_nginx", self.repair)
        self.assertNotIn("COMMENT ON", self.repair)
        self.assertNotIn("ufw ", self.repair)

    def test_uninstall_completion_is_loaded_and_foreign_proxy_is_preserved(self):
        self.assertIn("uninstall_safe_ownership.sh", self.uninstall_loader)
        self.assertIn("owned_nginx_site", self.uninstall_ownership)
        self.assertIn("owned_nginx_enabled", self.uninstall_ownership)
        self.assertIn("owned_certificate", self.uninstall_ownership)
        self.assertIn("verify_postgres_absent", self.uninstall_ownership)

    def test_amnezia_standalone_script_is_not_control_plane_reachable(self):
        paths = [
            ROOT / "deploy.sh",
            LIB / "control_plane.sh",
            LIB / "control_plane_completion.sh",
            LIB / "control_plane_final.sh",
            SCRIPTS / "install_safe.sh",
            SCRIPTS / "uninstall_foundation.sh",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertNotIn("run_script setup-amnezia-api.sh", read(path))
                self.assertNotIn("bash setup-amnezia-api.sh", read(path))

    def test_failure_injection_hook_runtime(self):
        hook = LIB / "install_safe_failure_injection.sh"
        command = f"""
set -Eeuo pipefail
foundation_fail() {{ printf '%s|%s|%s|%s\\n' "$1" "$2" "$3" "$4"; return 17; }}
INSTALL_SAFE_FAILURE_INJECTION_SOURCE_ONLY=1
source {str(hook)!r}
JUST1KBOT_FAILPOINT=after-manifest
set +e
installer_failpoint after-manifest
rc=$?
set -e
printf 'rc=%s\\n' "$rc"
"""
        result = subprocess.run(
            ["bash", "-c", command],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INJECTED_FAILURE", result.stdout)
        self.assertIn("failpoint=after-manifest", result.stdout)
        self.assertIn("rc=17", result.stdout)

    def test_every_new_shell_module_parses(self):
        modules = [
            LIB / "control_plane_completion.sh",
            LIB / "control_plane_final.sh",
            LIB / "install_safe_platform_support.sh",
            LIB / "install_safe_package_policy.sh",
            LIB / "install_safe_release_contract.sh",
            LIB / "install_safe_activation_policy.sh",
            LIB / "install_safe_failure_injection.sh",
            LIB / "install_safe_postgres_ownership.sh",
            LIB / "install_safe_proxy_mode.sh",
            LIB / "install_safe_tls_policy.sh",
            LIB / "uninstall_safe_ownership.sh",
            OPS / "doctor_complete.sh",
            OPS / "doctor_json.sh",
            OPS / "repair.sh",
            OPS / "repair_complete.sh",
            OPS / "support_bundle.sh",
        ]
        for module in modules:
            with self.subTest(module=module):
                result = subprocess.run(
                    ["bash", "-n", str(module)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
