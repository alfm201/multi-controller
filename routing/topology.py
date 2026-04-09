"""Connectivity policy for nodes in the same configured group."""


def should_connect(self_roles, peer_roles) -> bool:
    """All configured peers are part of the same group and should connect."""
    return True
