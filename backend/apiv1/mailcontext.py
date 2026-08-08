"""Plain-JSON context builders for the apiv1 mail templates.

Mail templates live in the DB (``MailTemplate``) and render in a sandbox that
sees JSON only — they cannot traverse ORM relations. Every value a template
needs must therefore be assembled here.

Keep the keys in sync with ``documentation/configuration/templates/*.j2``.
"""
from django.conf import settings


def _owner_name(user) -> str:
    if not user:
        return ""
    return user.get_full_name() or user.username or ""


def proposal_url(proposal) -> str:
    return f"{settings.FRONTEND_BASE_URL}/proposal-editor/{proposal.pk}"


def event_url(proposal, event) -> str:
    return f"{settings.FRONTEND_BASE_URL}/proposal/{proposal.pk}/event/{event.pk}"


def proposal_dict(proposal) -> dict:
    call = getattr(proposal, "call", None)
    return {
        "id": str(proposal.pk),
        "title": proposal.title,
        "call_title": call.title if call else "",
        "owner_name": _owner_name(getattr(proposal, "owner", None)),
        "moderation_comment": getattr(proposal, "moderation_comment", "") or "",
        "url": proposal_url(proposal),
    }


def review_dict(review) -> dict:
    reviewer = getattr(review, "reviewer", None)
    return {
        "comment": review.comment or "",
        "status": review.status,
        "reviewer_name": reviewer.username if reviewer else "",
        "reviewer_is_system": bool(review.reviewer_is_system),
    }


def proposal_context(proposal, reviews=None) -> dict:
    ctx = {"proposal": proposal_dict(proposal)}
    if reviews is not None:
        ctx["reviews"] = [review_dict(r) for r in reviews]
    return ctx


def review_context(proposal, *, review=None, reviewer=None) -> dict:
    ctx = {"proposal": proposal_dict(proposal)}
    if review is not None:
        ctx["review"] = review_dict(review)
    if reviewer is not None:
        ctx["reviewer"] = {"username": reviewer.username or ""}
    return ctx


def event_context(event) -> dict:
    """Context for an Event. ``proposal`` falls back to the event's own name so
    the templates' ``proposal.title`` never renders empty."""
    proposal = getattr(event, "proposal", None)
    if proposal is not None:
        proposal_part = proposal_dict(proposal)
        url = event_url(proposal, event)
    else:
        proposal_part = {"title": event.name, "call_title": "", "owner_name": "",
                         "moderation_comment": "", "url": ""}
        url = ""
    return {
        "proposal": proposal_part,
        "event": {
            "id": str(event.pk),
            "name": event.name,
            # ISO strings; templates apply | timezone("Europe/Berlin") | isoformat()
            "start_time": event.start_time.isoformat() if event.start_time else None,
            "end_time": event.end_time.isoformat() if event.end_time else None,
            "url": url,
        },
    }
