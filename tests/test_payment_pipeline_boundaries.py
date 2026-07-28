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
 def test_create_calls_require_idempotency_key(self):
  for path in (ROOT/'services').rglob('*.py'):
   tree=ast.parse(path.read_text())
   for node in ast.walk(tree):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='create_payment_result': self.assertIn('idempotency_key',{k.arg for k in node.keywords})
