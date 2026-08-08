"""Lucene-like filter query language for the UDM dashboard.

Two stages, deliberately separated:

* :func:`parse_query` turns query text into an AST. The grammar accepts the
  *whole* Lucene surface syntax we care about — including fuzzy (``~``),
  boosting (``^``) and proximity (``"a b"~3``) — so those stay visible in the
  AST and a later implementation only has to teach the evaluator about them.
* :func:`match` evaluates an AST against a document. Features that are parsed
  but not implemented raise :class:`UnsupportedQueryFeature` with a message
  naming the feature; the API turns that into a 400 for the user.

Beyond Lucene, collections (submodels) are searchable with quantifiers::

    any(participants: status:confirmed AND age:[18 TO *])
    all(participants: status:confirmed)
    none(participants: status:rejected)

``participants.status:confirmed`` is sugar for the ``any(...)`` form, one
``any`` per path segment.

Values are stored as strings, so terms and range bounds are compared as
numbers, then as datetimes (via ``dateparser``, so ``15.03.2024``, ``March 15
2024`` and ``2024-03-15`` are the same instant), then as text::

    starts_at:[2024-03-01 TO 2024-03-31]
    starts_at:["2024-03-15 18:00" TO *]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable, Sequence

import dateparser
import pyparsing as pp

__all__ = [
    "QuerySyntaxError",
    "UnsupportedQueryFeature",
    "parse_query",
    "validate_query",
    "match",
    "Document",
    "build_document",
]


class QuerySyntaxError(ValueError):
    """The query text could not be parsed."""


class UnsupportedQueryFeature(ValueError):
    """The query parsed, but uses a feature the evaluator does not implement."""


# ─── AST ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Term:
    """A single term. ``field`` None means "any field of the document"."""
    value: str
    field: str | None = None
    wildcard: bool = False
    #: Fuzzy edit distance from ``term~`` / ``term~2``. Parsed, not evaluated.
    fuzziness: float | None = None
    #: Boost factor from ``term^2``. Parsed, not evaluated.
    boost: float | None = None


@dataclass(frozen=True)
class Phrase:
    """A quoted phrase; matches consecutive tokens."""
    value: str
    field: str | None = None
    #: Proximity slop from ``"a b"~3``. Parsed, not evaluated.
    proximity: int | None = None
    boost: float | None = None


@dataclass(frozen=True)
class Range:
    lower: str | None  # None = unbounded (``*``)
    upper: str | None
    include_lower: bool
    include_upper: bool
    field: str | None = None
    boost: float | None = None


@dataclass(frozen=True)
class Not:
    child: Any


@dataclass(frozen=True)
class BoolQuery:
    """``must``/``should``/``must_not`` in Lucene's occur sense.

    A clause list built from ``AND``/``OR``/``+``/``-``. ``should`` clauses are
    OR-ed; if there is at least one ``must`` clause the ``should`` clauses are
    optional (pure Lucene semantics).
    """
    must: tuple = ()
    should: tuple = ()
    must_not: tuple = ()


@dataclass(frozen=True)
class Quantified:
    """``any``/``all``/``none`` over a submodel collection."""
    quantifier: str  # "any" | "all" | "none"
    path: tuple[str, ...]
    child: Any


@dataclass(frozen=True)
class MatchAll:
    pass


# ─── Grammar ──────────────────────────────────────────────────────────────────

def _number(tokens: pp.ParseResults) -> float:
    return float(tokens[0])


def _build_grammar() -> pp.ParserElement:
    pp.ParserElement.enablePackrat()

    LPAR, RPAR, LBRACK, RBRACK, LBRACE, RBRACE = map(pp.Suppress, "()[]{}")
    COLON = pp.Suppress(":")
    TO = pp.Keyword("TO").suppress()

    and_kw = pp.Keyword("AND") | pp.Keyword("&&")
    or_kw = pp.Keyword("OR") | pp.Keyword("||")
    not_kw = pp.Keyword("NOT") | pp.Literal("!")
    any_kw, all_kw, none_kw = (pp.CaselessKeyword(k) for k in ("any", "all", "none"))
    keyword = and_kw | or_kw | pp.Keyword("NOT") | TO

    number = pp.Regex(r"[+-]?\d+(\.\d+)?").set_parse_action(_number)

    # Unquoted term: Lucene's special characters must be escaped with a
    # backslash. `-` is special only in first position (the prohibit prefix) so
    # that dates like 2024-03-15 need no escaping.
    term_text = pp.Regex(r'(?:[^\s\\+\-!():\^\[\]"{}~*?&|/]|\\.)'
                         r'(?:[^\s\\+!():\^\[\]"{}~*?&|/]|\\.)*')
    wildcard_text = pp.Regex(r'(?:(?:[^\s\\+\-!():\^\[\]"{}~&|/]|\\.)'
                             r'(?:[^\s\\+!():\^\[\]"{}~&|/]|\\.)*)?[*?]'
                             r'(?:[^\s\\+!():\^\[\]"{}~&|/]|\\.)*')
    quoted_text = pp.QuotedString('"', esc_char="\\")

    fuzzy = pp.Suppress("~") + pp.Optional(number, default=2.0)
    boost = pp.Suppress("^") + number
    proximity = pp.Suppress("~") + number

    # Slugs may contain `-`; it is never ambiguous here because a field name is
    # only recognized when followed by `:`.
    _name = r"[A-Za-z_][A-Za-z0-9_-]*"
    field_name = pp.Regex(rf"{_name}(?:\.{_name})*")
    field_prefix = ~keyword + field_name("field") + COLON

    range_endpoint = pp.Literal("*") | quoted_text | pp.Regex(r"[^\s\]\}]+")
    incl_range = pp.Group(LBRACK + range_endpoint + TO + range_endpoint + RBRACK)
    excl_range = pp.Group(LBRACE + range_endpoint + TO + range_endpoint + RBRACE)

    expression = pp.Forward()

    def _unescape(text: str) -> str:
        return re.sub(r"\\(.)", r"\1", text)

    def _endpoint(raw: str) -> str | None:
        return None if raw == "*" else _unescape(raw)

    @incl_range.add_parse_action
    def _mk_incl(tokens):
        lo, hi = tokens[0]
        return Range(_endpoint(lo), _endpoint(hi), True, True)

    @excl_range.add_parse_action
    def _mk_excl(tokens):
        lo, hi = tokens[0]
        return Range(_endpoint(lo), _endpoint(hi), False, False)

    phrase = pp.Group(quoted_text + pp.Optional(proximity, default=None))
    phrase.add_parse_action(lambda t: Phrase(t[0][0], proximity=(int(t[0][1]) if t[0][1] is not None else None)))

    wildcard = wildcard_text.copy().add_parse_action(lambda t: Term(_unescape(t[0]), wildcard=True))
    plain_term = (~keyword + term_text).add_parse_action(
        lambda t: Term(_unescape(t[0]))
    ) + pp.Optional(fuzzy, default=None)

    @plain_term.add_parse_action
    def _mk_fuzzy(tokens):
        term, fuzziness = tokens[0], tokens[1]
        return term if fuzziness is None else Term(term.value, fuzziness=fuzziness)

    quantifier_kw = (any_kw | all_kw | none_kw)("quant")
    quantified = pp.Group(quantifier_kw + LPAR + field_name("path") + COLON + expression("body") + RPAR)

    @quantified.add_parse_action
    def _mk_quantified(tokens):
        group = tokens[0]
        return Quantified(str(group["quant"]).lower(), tuple(str(group["path"]).split(".")), group["body"])

    # `any(`/`all(`/`none(` that does not form a valid quantifier is a mistake,
    # not a term followed by a group — say so instead of silently misreading it.
    def _bad_quantifier(s, loc, tokens):
        raise pp.ParseFatalException(
            s, loc, f"expected {tokens[0]}(<collection>: <query>)"
        )

    malformed_quantifier = (quantifier_kw + pp.FollowedBy("(")).copy()
    malformed_quantifier.add_parse_action(_bad_quantifier)

    atom = quantified | malformed_quantifier | incl_range | excl_range | phrase | wildcard | plain_term
    grouped = pp.Group(LPAR + expression + RPAR).add_parse_action(lambda t: t[0][0])

    fielded = pp.Group(field_prefix + (grouped | atom))

    @fielded.add_parse_action
    def _mk_fielded(tokens):
        group = tokens[0]
        return _apply_field(str(group["field"]), group[1])

    clause = (fielded | grouped | atom) + pp.Optional(boost, default=None)

    @clause.add_parse_action
    def _mk_boost(tokens):
        node, factor = tokens[0], tokens[1]
        return node if factor is None else _apply_boost(node, factor)

    unary = pp.Forward()
    negated = (not_kw.suppress() + unary).add_parse_action(lambda t: Not(t[0]))
    required = (pp.Literal("+").suppress() + unary).add_parse_action(lambda t: ("+", t[0]))
    prohibited = (pp.Literal("-").suppress() + unary).add_parse_action(lambda t: ("-", t[0]))
    unary <<= negated | required | prohibited | clause

    and_expr = (unary + pp.ZeroOrMore(pp.Optional(and_kw).suppress() + ~or_kw + unary))
    and_expr.add_parse_action(lambda t: _mk_bool(list(t), conjunctive=True) if len(t) > 1 else t[0])

    or_expr = (and_expr + pp.ZeroOrMore(or_kw.suppress() + and_expr))
    or_expr.add_parse_action(lambda t: _mk_bool(list(t), conjunctive=False) if len(t) > 1 else t[0])

    expression <<= or_expr
    return expression


def _mk_bool(nodes: list, *, conjunctive: bool) -> BoolQuery:
    """Fold a flat operand list into a BoolQuery.

    ``+``/``-`` prefixes (parsed as tuples) always win over the surrounding
    operator, matching Lucene: ``a OR +b`` requires ``b``.
    """
    must, should, must_not = [], [], []
    for node in nodes:
        if isinstance(node, tuple):
            occur, inner = node
            (must if occur == "+" else must_not).append(inner)
        elif conjunctive:
            must.append(node)
        else:
            should.append(node)
    return BoolQuery(tuple(must), tuple(should), tuple(must_not))


def _apply_field(name: str, node: Any) -> Any:
    """Attach a field name, expanding dotted paths into nested ``any(...)``."""
    head, _, rest = name.partition(".")
    if rest:
        return Quantified("any", (head,), _apply_field(rest, node))
    return _set_field(name, node)


def _set_field(name: str, node: Any) -> Any:
    if isinstance(node, (Term, Phrase, Range)):
        return type(node)(**{**node.__dict__, "field": name})
    if isinstance(node, Not):
        return Not(_set_field(name, node.child))
    if isinstance(node, BoolQuery):
        return BoolQuery(
            tuple(_set_field(name, c) for c in node.must),
            tuple(_set_field(name, c) for c in node.should),
            tuple(_set_field(name, c) for c in node.must_not),
        )
    if isinstance(node, Quantified):
        return Quantified(node.quantifier, (name, *node.path), node.child)
    return node


def _apply_boost(node: Any, factor: float) -> Any:
    if isinstance(node, (Term, Phrase, Range)):
        return type(node)(**{**node.__dict__, "boost": factor})
    # A boost on a group applies to the whole group; keep it on the first leaf
    # so the evaluator still reports the unsupported feature.
    if isinstance(node, BoolQuery):
        clauses = node.must or node.should or node.must_not
        if clauses:
            boosted = _apply_boost(clauses[0], factor)
            if node.must:
                return BoolQuery((boosted, *node.must[1:]), node.should, node.must_not)
            if node.should:
                return BoolQuery(node.must, (boosted, *node.should[1:]), node.must_not)
            return BoolQuery(node.must, node.should, (boosted, *node.must_not[1:]))
    return node


_GRAMMAR = _build_grammar()


def parse_query(text: str) -> Any:
    """Parse query text into an AST. Empty input yields :class:`MatchAll`."""
    if not text or not text.strip():
        return MatchAll()
    try:
        result = _GRAMMAR.parse_string(text, parse_all=True)
    except pp.ParseBaseException as exc:
        raise QuerySyntaxError(f"Invalid query at column {exc.column}: {exc.msg}") from exc
    return result[0]


# ─── Documents ────────────────────────────────────────────────────────────────

@dataclass
class Document:
    """A searchable view of an entity node."""
    fields: dict[str, list[str]] = dc_field(default_factory=dict)
    children: dict[str, list["Document"]] = dc_field(default_factory=dict)

    def values(self, name: str | None) -> list[str]:
        """Values to match against. A named field is looked up on this node
        only; an unqualified term searches the node AND everything below it, so
        a bare `admin` finds whatever `speaker.display-name:admin` finds."""
        if name is not None:
            return self.fields.get(name, [])
        return [v for node in self.walk() for values in node.fields.values() for v in values]

    def walk(self) -> "list[Document]":
        """This node and every descendant, depth-first."""
        found = [self]
        for kids in self.children.values():
            for kid in kids:
                found.extend(kid.walk())
        return found


def _stringify(value: Any) -> list[str]:
    if value is None or isinstance(value, bool):
        return [] if value is None else [str(value).lower()]
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _stringify(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _stringify(v)]
    return [str(value)]


def build_document(node: Any, slug_id_prefixes: dict[str, str] | None = None) -> Document:
    """Build a :class:`Document` from a serialized entity node (``EntityOut``
    or the plain dicts nested under ``children``).

    Only what the caller passes in is searchable — feeding it the
    policy-redacted serialization keeps hidden fields unmatchable.

    ``slug_id_prefixes`` maps a ``slug_id`` field's slug to its display prefix.
    Those fields store a bare number but are shown as ``PROP-6``, so both forms
    are indexed and users can search for what they see.
    """
    data = node if isinstance(node, dict) else node.dict()
    prefixes = slug_id_prefixes or {}
    doc = Document()
    for fv in data.get("field_values") or []:
        fv = fv if isinstance(fv, dict) else fv.dict()
        slug = fv["field_slug"]
        values = _stringify(fv.get("value"))
        prefix = prefixes.get(slug)
        if prefix:
            values = [*values, *(f"{prefix}-{v}" for v in values)]
        doc.fields.setdefault(slug, []).extend(values)
    for key, value in (data.get("overflow_data") or {}).items():
        doc.fields.setdefault(key, []).extend(_stringify(value))
    for col in data.get("dashboard_columns") or []:
        col = col if isinstance(col, dict) else col.dict()
        doc.fields.setdefault(col["key"], []).extend(_stringify(col.get("value")))
    if data.get("id") is not None:
        doc.fields.setdefault("id", []).append(str(data["id"]))
    for slug, kids in (data.get("children") or {}).items():
        doc.children[slug] = [build_document(kid, slug_id_prefixes) for kid in kids]
    return doc


# ─── Evaluation ───────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _reject_unsupported(node: Term | Phrase | Range) -> None:
    if getattr(node, "fuzziness", None) is not None:
        raise UnsupportedQueryFeature(
            f"Fuzzy search (~) is not supported yet: '{node.value}~'"
        )
    if getattr(node, "proximity", None) is not None:
        raise UnsupportedQueryFeature(
            f'Proximity search (~) is not supported yet: "{node.value}"~{node.proximity}'
        )
    if node.boost is not None:
        raise UnsupportedQueryFeature("Boosting (^) is not supported yet")


def _wildcard_re(pattern: str) -> re.Pattern[str]:
    out = []
    for char in pattern:
        if char == "*":
            out.append(".*")
        elif char == "?":
            out.append(".")
        else:
            out.append(re.escape(char))
    return re.compile(f"^{''.join(out)}$", re.IGNORECASE)


def _as_number(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None


# A bare number is a number, never a day-of-month: dateparser reads "24" as the
# 24th of the current month, which would make `age:[18 TO 30]` behave wildly.
_DATEISH_RE = re.compile(r"(?=.*\d)(?=.*[^\W\d_]|.*[-/.:,])", re.UNICODE)


@lru_cache(maxsize=8192)
def _as_datetime(text: str) -> datetime | None:
    """Parse a free-text date/datetime, or None if it isn't one.

    Field values are plain strings ("15.03.2024", "March 15 2024, 18:00"), so
    both sides of a comparison go through dateparser and are compared as
    datetimes. Naive results only — mixing tz-aware and naive is not orderable.
    """
    text = text.strip()
    if not text or not _DATEISH_RE.match(text):
        return None
    parsed = dateparser.parse(text, settings={"RETURN_AS_TIMEZONE_AWARE": False})
    return parsed.replace(tzinfo=None) if parsed is not None else None


def _typed_keys(text: str) -> dict[str, Any]:
    """All orderable interpretations of a string, by kind."""
    keys: dict[str, Any] = {"text": text.strip().lower()}
    number = _as_number(text)
    if number is not None:
        keys["number"] = number
    else:
        moment = _as_datetime(text)
        if moment is not None:
            keys["datetime"] = moment
    return keys


def _match_term(node: Term, doc: Document) -> bool:
    _reject_unsupported(node)
    values = doc.values(node.field)
    if node.wildcard:
        pattern = _wildcard_re(node.value)
        # Also try the raw value so patterns spanning a separator work
        # ("PROP-*" against "PROP-6", whose tokens are "prop" and "6").
        return any(
            pattern.match(value.strip()) or any(pattern.match(tok) for tok in _tokens(value))
            for value in values
        )
    # An unquoted term may still tokenize into several tokens ("PROP-6"); then
    # it behaves like a phrase, as with Lucene's standard analyzer.
    needle = _tokens(node.value)
    if len(needle) > 1:
        if _match_tokens(needle, values):
            return True
    elif any(node.value.lower() in _tokens(value) for value in values):
        return True
    # Date equality: `starts_at:2024-03-15` also matches "15. März 2024".
    wanted = _as_datetime(node.value)
    if wanted is None:
        return False
    return any(_as_datetime(value) == wanted for value in values)


def _match_tokens(needle: list[str], values: Iterable[str]) -> bool:
    """True if ``needle`` appears as a consecutive token run in any value."""
    if not needle:
        return False
    for value in values:
        haystack = _tokens(value)
        for i in range(len(haystack) - len(needle) + 1):
            if haystack[i:i + len(needle)] == needle:
                return True
    return False


def _match_phrase(node: Phrase, doc: Document) -> bool:
    _reject_unsupported(node)
    return _match_tokens(_tokens(node.value), doc.values(node.field))


#: Comparison kinds, most specific first. A range uses the first kind that both
#: its bounds and the candidate value can be read as, so "[2024-01-01 TO
#: 2024-12-31]" compares datetimes while "[a TO m]" compares text.
_KINDS = ("number", "datetime", "text")


def _match_range(node: Range, doc: Document) -> bool:
    _reject_unsupported(node)
    lower = _typed_keys(node.lower) if node.lower is not None else None
    upper = _typed_keys(node.upper) if node.upper is not None else None
    for value in doc.values(node.field):
        keys = _typed_keys(value)
        for kind in _KINDS:
            if kind not in keys:
                continue
            if lower is not None and kind not in lower:
                continue
            if upper is not None and kind not in upper:
                continue
            key = keys[kind]
            if lower is not None and (key < lower[kind] if node.include_lower else key <= lower[kind]):
                break
            if upper is not None and (key > upper[kind] if node.include_upper else key >= upper[kind]):
                break
            return True
    return False


def _collect(docs: Iterable[Document], path: Sequence[str]) -> list[Document]:
    current = list(docs)
    for segment in path:
        current = [kid for doc in current for kid in doc.children.get(segment, [])]
    return current


def _match_quantified(node: Quantified, doc: Document) -> bool:
    items = _collect([doc], node.path)
    if node.quantifier == "any":
        return any(match(node.child, item) for item in items)
    if node.quantifier == "all":
        # Vacuously true on an empty collection, as in first-order logic.
        return all(match(node.child, item) for item in items)
    return not any(match(node.child, item) for item in items)


def validate_query(node: Any) -> None:
    """Walk the whole AST and raise for parsed-but-unimplemented features.

    :func:`match` only reaches the leaves it needs (and only if there is a
    document at all), so unsupported features are reported up front instead.
    """
    if isinstance(node, (Term, Phrase, Range)):
        _reject_unsupported(node)
    elif isinstance(node, Not):
        validate_query(node.child)
    elif isinstance(node, Quantified):
        validate_query(node.child)
    elif isinstance(node, BoolQuery):
        for clause in (*node.must, *node.should, *node.must_not):
            validate_query(clause)


def match(node: Any, doc: Document) -> bool:
    """Evaluate an AST node against a document.

    Raises :class:`UnsupportedQueryFeature` for parsed-but-unimplemented
    features (fuzzy, proximity, boosting).
    """
    if isinstance(node, MatchAll):
        return True
    if isinstance(node, Term):
        return _match_term(node, doc)
    if isinstance(node, Phrase):
        return _match_phrase(node, doc)
    if isinstance(node, Range):
        return _match_range(node, doc)
    if isinstance(node, Not):
        return not match(node.child, doc)
    if isinstance(node, Quantified):
        return _match_quantified(node, doc)
    if isinstance(node, BoolQuery):
        if any(match(c, doc) for c in node.must_not):
            return False
        if not all(match(c, doc) for c in node.must):
            return False
        if node.should and not node.must:
            return any(match(c, doc) for c in node.should)
        return True
    raise QuerySyntaxError(f"Unsupported query node: {node!r}")
