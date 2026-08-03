import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class PaymentBoundaries(unittest.TestCase):
    def test_webhook_handler_only_persists_inbox(self):
        body = (ROOT / "bot/handlers/webhook.py").read_text()
        fn = body[
            body.index("async def yookassa_webhook_handler") : body.index(
                "async def healthcheck_handler"
            )
        ]
        for forbidden in (
            "handle_successful_payment",
            "extend_subscription",
            "ReferralService",
            "ProfileDeletionService",
            "handle_yookassa_callback",
        ):
            self.assertNotIn(forbidden, fn)

    def test_paid_at_never_cleared_by_manual_review(self):
        for root in ("bot", "database", "services", "utils"):
            for path in (ROOT / root).rglob("*.py"):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        value = node.value
                        for target in targets:
                            if (
                                isinstance(target, ast.Attribute)
                                and target.attr == "paid_at"
                                and isinstance(value, ast.Constant)
                                and value.value is None
                            ):
                                self.fail(str(path))

    def test_all_paid_at_clear_forms_are_forbidden(self):
        for root in ("bot", "database", "services", "utils"):
            for path in (ROOT / root).rglob("*.py"):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if (
                            isinstance(node.func, ast.Name)
                            and node.func.id == "setattr"
                            and len(node.args) >= 3
                            and isinstance(node.args[1], ast.Constant)
                            and node.args[1].value == "paid_at"
                            and isinstance(node.args[2], ast.Constant)
                            and node.args[2].value is None
                        ):
                            self.fail(str(path))
                        if (
                            isinstance(node.func, ast.Attribute)
                            and node.func.attr == "values"
                            and any(
                                k.arg == "paid_at"
                                and isinstance(k.value, ast.Constant)
                                and k.value.value is None
                                for k in node.keywords
                            )
                        ):
                            self.fail(str(path))
                        if (
                            isinstance(node.func, ast.Attribute)
                            and node.func.attr == "update"
                            and node.args
                            and isinstance(node.args[0], ast.Dict)
                        ):
                            for key, value in zip(
                                node.args[0].keys, node.args[0].values, strict=False
                            ):
                                if (
                                    isinstance(key, ast.Constant)
                                    and key.value == "paid_at"
                                    and isinstance(value, ast.Constant)
                                    and value.value is None
                                ):
                                    self.fail(str(path))

    def test_topup_pipeline_has_no_subscription_side_effects(self):
        for path in [
            ROOT / "services/account_topup.py",
            ROOT / "services/payment_provider_operations.py",
            ROOT / "services/workers/webhook_inbox.py",
            ROOT / "bot/handlers/webhook.py",
        ]:
            body = path.read_text()
            for forbidden in (
                "SubscriptionService.extend_subscription",
                "ReferralService.process_bonus",
                "ProfileDeletionService",
                "payment_grant",
            ):
                self.assertNotIn(forbidden, body, str(path))

    def test_repeatable_reconcile_and_idempotent_credit_are_present(self):
        provider = (ROOT / "services/payment_provider_operations.py").read_text()
        models = (ROOT / "database/models.py").read_text()
        topup = (ROOT / "services/account_topup.py").read_text()
        self.assertIn("ensure_reconcile_payment_operation", provider)
        self.assertIn("uuid.uuid4().hex", provider)
        self.assertIn("uq_account_ledger_payment_credit", models)
        self.assertIn("credit_succeeded_topup", topup)

    def test_create_calls_require_idempotency_key(self):
        for path in (ROOT / "services").rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "create_payment_result"
                ):
                    self.assertIn("idempotency_key", {k.arg for k in node.keywords})
