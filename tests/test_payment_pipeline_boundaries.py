import ast, pathlib, unittest
ROOT=pathlib.Path(__file__).parents[1]
class PaymentBoundaries(unittest.TestCase):
 def test_webhook_handler_only_persists_inbox(self):
  body=(ROOT/'bot/handlers/webhook.py').read_text(); fn=body[body.index('async def yookassa_webhook_handler'):body.index('async def healthcheck_handler')]
  for forbidden in ('handle_successful_payment','extend_subscription','ReferralService','ProfileDeletionService','handle_yookassa_callback'): self.assertNotIn(forbidden,fn)
 def test_paid_at_never_cleared_by_manual_review(self):
  for root in ('bot','database','services','utils'):
   for path in (ROOT/root).rglob('*.py'):
    tree=ast.parse(path.read_text())
    for node in ast.walk(tree):
     if isinstance(node,(ast.Assign,ast.AnnAssign)):
      targets=node.targets if isinstance(node,ast.Assign) else [node.target]; value=node.value
      for target in targets:
       if isinstance(target,ast.Attribute) and target.attr=='paid_at' and isinstance(value,ast.Constant) and value.value is None: self.fail(str(path))
 def test_all_paid_at_clear_forms_are_forbidden(self):
  for root in ('bot','database','services','utils'):
   for path in (ROOT/root).rglob('*.py'):
    tree=ast.parse(path.read_text())
    for node in ast.walk(tree):
     if isinstance(node,ast.Call):
      if isinstance(node.func,ast.Name) and node.func.id=='setattr' and len(node.args)>=3 and isinstance(node.args[1],ast.Constant) and node.args[1].value=='paid_at' and isinstance(node.args[2],ast.Constant) and node.args[2].value is None:self.fail(str(path))
      if isinstance(node.func,ast.Attribute) and node.func.attr=='values' and any(k.arg=='paid_at' and isinstance(k.value,ast.Constant) and k.value.value is None for k in node.keywords):self.fail(str(path))
      if isinstance(node.func,ast.Attribute) and node.func.attr=='update' and node.args and isinstance(node.args[0],ast.Dict):
       for key,value in zip(node.args[0].keys,node.args[0].values):
        if isinstance(key,ast.Constant) and key.value=='paid_at' and isinstance(value,ast.Constant) and value.value is None:self.fail(str(path))
 def test_payment_context_has_no_direct_legacy_effects(self):
  allowed=ROOT/'services/payment_fulfillment.py'
  for path in list((ROOT/'services/payment_service').rglob('*.py'))+[ROOT/'services/workers/payments.py',ROOT/'bot/handlers/webhook.py']:
   body=path.read_text()
   for forbidden in ('SubscriptionService.extend_subscription','ReferralService.process_bonus','PaymentService.handle_successful_payment('): self.assertNotIn(forbidden,body,str(path))

 def test_no_payment_side_effects_outside_fulfillment(self):
  for path in [ROOT/'services/payment_service/service.py',ROOT/'services/workers/webhook_inbox.py',ROOT/'services/workers/payments.py']:
   body=path.read_text()
   for forbidden in ('subscription_end =','referral_days =','ProfileDeletionService','extend_subscription('): self.assertNotIn(forbidden,body,str(path))
 def test_repeatable_reconcile_and_atomic_grant_are_present(self):
  provider=(ROOT/'services/payment_provider_operations.py').read_text()
  self.assertIn('ensure_reconcile_payment_operation',provider)
  self.assertIn('uuid.uuid4().hex',provider)
  self.assertIn('payment-grant:',provider)
  self.assertIn('on_conflict_do_nothing',provider)

 def test_create_calls_require_idempotency_key(self):
  for path in (ROOT/'services').rglob('*.py'):
   tree=ast.parse(path.read_text())
   for node in ast.walk(tree):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='create_payment_result': self.assertIn('idempotency_key',{k.arg for k in node.keywords})
