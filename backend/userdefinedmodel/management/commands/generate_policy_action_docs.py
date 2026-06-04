"""
Management command that auto-generates Markdown documentation for all registered
policy action types from their Pydantic schemas and the handler registry.

Usage::

    uv run python manage.py generate_policy_action_docs
    uv run python manage.py generate_policy_action_docs --output docs/policy_actions.md
"""
import io

from django.core.management.base import BaseCommand


def _python_type_name(annotation) -> str:
    """Return a readable type name for a Pydantic field annotation."""
    import typing

    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())

    if origin is typing.Union:
        inner = [a for a in args if a is not type(None)]
        names = [_python_type_name(a) for a in inner]
        return " | ".join(names) + (" | None" if type(None) in args else "")
    if origin is list:
        return f"list[{_python_type_name(args[0])}]" if args else "list"
    if origin is dict:
        k = _python_type_name(args[0]) if args else "any"
        v = _python_type_name(args[1]) if len(args) > 1 else "any"
        return f"dict[{k}, {v}]"
    if origin is typing.Literal:
        return " | ".join(repr(a) for a in args)
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    raw = str(annotation)
    for prefix in ("typing.", "builtins.", "<class '", "'>"):
        raw = raw.replace(prefix, "")
    return raw.strip("'\"")


def _build_example(schema_cls) -> dict:
    """Build a minimal example JSON dict from the schema's field defaults/literals."""
    import typing
    from pydantic_core import PydanticUndefinedType

    example = {}
    for name, field_info in schema_cls.model_fields.items():
        ann = field_info.annotation
        # Unwrap Optional
        origin = getattr(ann, "__origin__", None)
        args = getattr(ann, "__args__", ())
        if origin is typing.Union and type(None) in args:
            ann = next(a for a in args if a is not type(None))

        # Prefer literal values as examples
        lit_origin = getattr(ann, "__origin__", None)
        if lit_origin is typing.Literal:
            example[name] = ann.__args__[0]
        elif (
            field_info.default is not None
            and not isinstance(field_info.default, PydanticUndefinedType)
            and field_info.default is not ...
        ):
            example[name] = field_info.default
        elif field_info.default_factory is not None:  # type: ignore[misc]
            example[name] = field_info.default_factory()
        elif ann is str or (hasattr(ann, "__name__") and ann.__name__ == "str"):
            example[name] = f"<{name}>"
        elif ann is bool or (hasattr(ann, "__name__") and ann.__name__ == "bool"):
            example[name] = False
        elif ann is int or (hasattr(ann, "__name__") and ann.__name__ == "int"):
            example[name] = 0
        else:
            example[name] = None
    return example


class Command(BaseCommand):
    help = "Generate Markdown documentation for all registered policy action types"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            "-o",
            dest="output",
            default=None,
            help="Write output to this file instead of stdout",
        )

    def handle(self, *args, **options):
        import json

        from userdefinedmodel.actions import _action_registry

        buf = io.StringIO()
        w = buf.write

        w("# Policy Action Types\n\n")
        w(
            "Actions are declared by Rego policies as structured JSON objects in the "
            "`data.udm.actions` rule output.  Each object must include a `type` "
            "discriminator and a `phase` (`\"pre\"` or `\"post\"`).  The engine "
            "dispatches each action to its registered handler after validating the "
            "object against the schema shown below.\n\n"
        )
        w("---\n\n")

        if not _action_registry:
            w("*No action types are currently registered.*\n")
        else:
            for type_name, (schema_cls, _handler) in sorted(_action_registry.items()):
                doc = (schema_cls.__doc__ or "").strip()
                w(f"## `{type_name}`\n\n")
                if doc:
                    w(f"{doc}\n\n")

                # Field table
                w("| Field | Type | Required | Description |\n")
                w("|---|---|---|---|\n")
                for fname, finfo in schema_cls.model_fields.items():
                    ann = finfo.annotation
                    type_str = _python_type_name(ann)
                    required = "yes" if finfo.is_required() else "no"
                    desc = finfo.description or ""
                    w(f"| `{fname}` | `{type_str}` | {required} | {desc} |\n")
                w("\n")

                # Example
                example = _build_example(schema_cls)
                w("**Example Rego output:**\n\n")
                w("```json\n")
                w(json.dumps(example, indent=2))
                w("\n```\n\n")

        output = buf.getvalue()
        dest = options.get("output")
        if dest:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(output)
            self.stdout.write(f"Written to {dest}")
        else:
            self.stdout.write(output)
