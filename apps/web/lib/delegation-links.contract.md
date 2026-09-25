# Delegation control link contract

Research proposal handoffs use `delegationControlHref(delegationId)` so navigation carries the exact durable delegation identity into the AI control center.

The control center reads the `delegation` query parameter only for consumer focus/scroll behavior. It does not use URL state to approve, start, stop, authorize, rebudget, or otherwise mutate delegation authority.
