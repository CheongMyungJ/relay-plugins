"""Relay local dispatcher: opens the next Relay session for watched issues and PRs.

Shipped inside the plugin package as an independent CLI. Skills never call it, and it
never reads Relay's own state; it depends on relay_core for artifact and next-step
reading, the GitHub client and repository identity, and is compatible only with the
relay_core that sits next to it.
"""
