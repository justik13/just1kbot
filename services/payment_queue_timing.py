"""Shared timing policy for durable payment queue recovery and monitoring."""

PROVIDER_LEASE_SECONDS = 120
REFUND_LEASE_SECONDS = 120
FULFILLMENT_LEASE_SECONDS = 120
WEBHOOK_LEASE_SECONDS = 120

# A runnable row may briefly remain visible while a worker is polling it.
BACKLOG_GRACE_SECONDS = 60
# Avoid racing the recovery transaction at the exact lease boundary.
HEALTH_LEASE_GRACE_SECONDS = 15
