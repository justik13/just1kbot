"""Payment gateway adapters.

Each gateway lives in its own submodule under ``integrations.payment_gateways``.
Callers import the concrete module directly, e.g.::

    from integrations.payment_gateways.factory import get_payment_gateway
"""
