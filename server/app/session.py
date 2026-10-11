"""Session errors shared by every repository."""


class InvalidSession(ValueError):
    """The bearer token is not a signed-in account. Any other failure is an outage, not a sign-out."""
