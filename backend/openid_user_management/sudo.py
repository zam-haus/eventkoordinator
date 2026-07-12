"""
Session-scoped sudo mode for superusers.

A superuser can toggle sudo mode on their session; the flag is annotated onto
``request.user`` by :class:`SudoModeMiddleware` so downstream consumers (in
particular the UDM Rego policy engine, which serializes the requesting user
into ``input.user.sudo``) can read it without access to the request.
"""

SUDO_SESSION_KEY = "sudo_mode"


def is_sudo_active(request) -> bool:
    """True iff the requesting user is a superuser AND enabled sudo mode on
    this session. Non-superusers can never be in sudo mode, even if the
    session flag is somehow set."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_superuser:
        return False
    return bool(request.session.get(SUDO_SESSION_KEY, False))


class SudoModeMiddleware:
    """Annotates ``request.user.sudo_mode`` from the session flag.

    Must run after AuthenticationMiddleware. The attribute defaults to absent
    (treated as False via getattr) for users not coming from a request, e.g.
    users serialized into policy lookup maps.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if is_sudo_active(request):
            request.user.sudo_mode = True
        return self.get_response(request)
