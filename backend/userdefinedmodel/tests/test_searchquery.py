"""Unit tests for the Lucene-like dashboard filter query language."""
from django.test import SimpleTestCase

from userdefinedmodel.searchquery import (
    BoolQuery,
    Document,
    MatchAll,
    Not,
    Phrase,
    Quantified,
    QuerySyntaxError,
    Range,
    Term,
    UnsupportedQueryFeature,
    build_document,
    match,
    parse_query,
    validate_query,
)


def doc(**fields) -> Document:
    return Document(fields={k: [str(v)] for k, v in fields.items()})


def matches(query: str, document: Document) -> bool:
    ast = parse_query(query)
    validate_query(ast)
    return match(ast, document)


class ParseTests(SimpleTestCase):
    def test_empty_query_is_match_all(self):
        self.assertIsInstance(parse_query(""), MatchAll)
        self.assertIsInstance(parse_query("   "), MatchAll)

    def test_bare_term_and_fielded_term(self):
        self.assertEqual(parse_query("hello"), Term("hello"))
        self.assertEqual(parse_query("title:hello"), Term("hello", field="title"))

    def test_boolean_operators(self):
        self.assertEqual(
            parse_query("a AND b"),
            BoolQuery(must=(Term("a"), Term("b"))),
        )
        self.assertEqual(
            parse_query("a OR b"),
            BoolQuery(should=(Term("a"), Term("b"))),
        )
        self.assertEqual(parse_query("NOT a"), Not(Term("a")))
        self.assertEqual(parse_query("!a"), Not(Term("a")))

    def test_implicit_and_between_adjacent_terms(self):
        self.assertEqual(parse_query("a b"), BoolQuery(must=(Term("a"), Term("b"))))

    def test_required_and_prohibited_prefixes(self):
        self.assertEqual(
            parse_query("+a -b"),
            BoolQuery(must=(Term("a"),), must_not=(Term("b"),)),
        )

    def test_and_binds_tighter_than_or(self):
        self.assertEqual(
            parse_query("a AND b OR c"),
            BoolQuery(should=(BoolQuery(must=(Term("a"), Term("b"))), Term("c"))),
        )

    def test_grouping_and_field_distribution(self):
        self.assertEqual(
            parse_query("title:(a OR b)"),
            BoolQuery(should=(Term("a", field="title"), Term("b", field="title"))),
        )

    def test_phrase_and_ranges(self):
        self.assertEqual(parse_query('title:"foo bar"'), Phrase("foo bar", field="title"))
        self.assertEqual(
            parse_query("age:[18 TO 30]"),
            Range("18", "30", True, True, field="age"),
        )
        self.assertEqual(
            parse_query("age:{18 TO 30}"),
            Range("18", "30", False, False, field="age"),
        )
        self.assertEqual(
            parse_query("age:[18 TO *]"),
            Range("18", None, True, True, field="age"),
        )

    def test_wildcards(self):
        self.assertEqual(parse_query("name:an*"), Term("an*", field="name", wildcard=True))
        self.assertEqual(parse_query("na?e"), Term("na?e", wildcard=True))

    def test_hyphen_is_prohibit_only_in_first_position(self):
        self.assertEqual(parse_query("2024-03-15"), Term("2024-03-15"))
        self.assertEqual(parse_query("a -b"), BoolQuery(must=(Term("a"),), must_not=(Term("b"),)))

    def test_field_names_may_contain_hyphens(self):
        self.assertEqual(parse_query("start-date:today"), Term("today", field="start-date"))
        self.assertEqual(
            parse_query("sub-items.first-name:anna"),
            Quantified("any", ("sub-items",), Term("anna", field="first-name")),
        )

    def test_escaped_special_characters(self):
        self.assertEqual(parse_query(r"a\:b"), Term("a:b"))

    def test_unsupported_features_are_kept_in_the_ast(self):
        self.assertEqual(parse_query("roam~"), Term("roam", fuzziness=2.0))
        self.assertEqual(parse_query("roam~1"), Term("roam", fuzziness=1.0))
        self.assertEqual(parse_query("roam^3"), Term("roam", boost=3.0))
        self.assertEqual(parse_query('"a b"~3'), Phrase("a b", proximity=3))

    def test_syntax_errors(self):
        for bad in ["(a", "a AND", 'title:"unterminated', "any(x)"]:
            with self.subTest(bad=bad), self.assertRaises(QuerySyntaxError):
                parse_query(bad)


class QuantifierParseTests(SimpleTestCase):
    def test_quantifiers(self):
        self.assertEqual(
            parse_query("any(participants: status:confirmed)"),
            Quantified("any", ("participants",), Term("confirmed", field="status")),
        )
        self.assertEqual(
            parse_query("all(participants: status:confirmed)"),
            Quantified("all", ("participants",), Term("confirmed", field="status")),
        )
        self.assertEqual(
            parse_query("none(participants: status:rejected)"),
            Quantified("none", ("participants",), Term("rejected", field="status")),
        )

    def test_dotted_path_is_sugar_for_any(self):
        self.assertEqual(
            parse_query("participants.name:anna"),
            Quantified("any", ("participants",), Term("anna", field="name")),
        )

    def test_nested_dotted_path(self):
        self.assertEqual(
            parse_query("a.b.c:x"),
            Quantified("any", ("a",), Quantified("any", ("b",), Term("x", field="c"))),
        )

    def test_quantifier_body_may_be_a_full_expression(self):
        ast = parse_query("any(participants: status:confirmed AND age:[18 TO *])")
        self.assertIsInstance(ast, Quantified)
        self.assertIsInstance(ast.child, BoolQuery)


class UnsupportedFeatureTests(SimpleTestCase):
    def test_fuzzy_is_rejected_with_a_message(self):
        with self.assertRaises(UnsupportedQueryFeature) as ctx:
            validate_query(parse_query("roam~2"))
        self.assertIn("Fuzzy", str(ctx.exception))

    def test_proximity_is_rejected_with_a_message(self):
        with self.assertRaises(UnsupportedQueryFeature) as ctx:
            validate_query(parse_query('"jakarta apache"~10'))
        self.assertIn("Proximity", str(ctx.exception))

    def test_boost_is_rejected_with_a_message(self):
        with self.assertRaises(UnsupportedQueryFeature) as ctx:
            validate_query(parse_query("jakarta^4"))
        self.assertIn("Boost", str(ctx.exception))

    def test_rejected_even_when_nested_in_a_branch_match_would_skip(self):
        with self.assertRaises(UnsupportedQueryFeature):
            validate_query(parse_query("a AND (b OR c~2)"))
        with self.assertRaises(UnsupportedQueryFeature):
            validate_query(parse_query("any(kids: NOT x^2)"))


class MatchTests(SimpleTestCase):
    def setUp(self):
        self.d = doc(title="Hello World", city="Berlin", age=24)

    def test_match_all(self):
        self.assertTrue(matches("", self.d))

    def test_term_matches_any_field_when_unqualified(self):
        self.assertTrue(matches("berlin", self.d))
        self.assertFalse(matches("paris", self.d))

    def test_term_matching_is_case_insensitive_and_token_based(self):
        self.assertTrue(matches("title:hello", self.d))
        self.assertTrue(matches("title:WORLD", self.d))
        self.assertFalse(matches("title:hell", self.d))
        self.assertFalse(matches("city:hello", self.d))

    def test_boolean_combinations(self):
        self.assertTrue(matches("title:hello AND city:berlin", self.d))
        self.assertFalse(matches("title:hello AND city:paris", self.d))
        self.assertTrue(matches("city:paris OR city:berlin", self.d))
        self.assertTrue(matches("NOT city:paris", self.d))
        self.assertTrue(matches("+title:hello -city:paris", self.d))
        self.assertFalse(matches("+title:hello -city:berlin", self.d))

    def test_phrase_requires_consecutive_tokens(self):
        self.assertTrue(matches('title:"hello world"', self.d))
        self.assertFalse(matches('title:"world hello"', self.d))

    def test_term_spanning_a_separator_matches_like_a_phrase(self):
        document = doc(proposal_id="PROP-6", title="Berlin Meetup")
        self.assertTrue(matches("proposal_id:PROP-6", document))
        self.assertTrue(matches('proposal_id:"PROP-6"', document))
        self.assertTrue(matches("proposal_id:prop-6", document))
        self.assertFalse(matches("proposal_id:PROP-7", document))
        self.assertTrue(matches("PROP-6", document))

    def test_wildcard_may_span_a_separator(self):
        document = doc(proposal_id="PROP-6")
        self.assertTrue(matches("proposal_id:PROP-*", document))
        self.assertTrue(matches("proposal_id:*-6", document))
        self.assertFalse(matches("proposal_id:PROJ-*", document))

    def test_wildcards(self):
        self.assertTrue(matches("city:Ber*", self.d))
        self.assertTrue(matches("city:B?rlin", self.d))
        self.assertFalse(matches("city:Ber?", self.d))

    def test_numeric_range(self):
        self.assertTrue(matches("age:[18 TO 30]", self.d))
        self.assertFalse(matches("age:[25 TO 30]", self.d))
        self.assertFalse(matches("age:{24 TO 30}", self.d))
        self.assertTrue(matches("age:[* TO 30]", self.d))

    def test_lexicographic_range(self):
        self.assertTrue(matches("city:[Amsterdam TO Cologne]", self.d))
        self.assertFalse(matches("city:[Cologne TO Dresden]", self.d))


class QuantifierMatchTests(SimpleTestCase):
    def setUp(self):
        self.d = Document(
            fields={"title": ["Trip"]},
            children={
                "participants": [
                    Document(fields={"name": ["Anna"], "status": ["confirmed"]}),
                    Document(fields={"name": ["Bob"], "status": ["pending"]}),
                ],
                "invoices": [],
            },
        )

    def test_any(self):
        self.assertTrue(matches("any(participants: status:confirmed)", self.d))
        self.assertFalse(matches("any(participants: status:rejected)", self.d))

    def test_all(self):
        self.assertFalse(matches("all(participants: status:confirmed)", self.d))
        self.assertTrue(matches("all(participants: name:*)", self.d))

    def test_all_is_vacuously_true_on_an_empty_collection(self):
        self.assertTrue(matches("all(invoices: paid:yes)", self.d))

    def test_none(self):
        self.assertTrue(matches("none(participants: status:rejected)", self.d))
        self.assertFalse(matches("none(participants: status:pending)", self.d))

    def test_none_on_missing_collection(self):
        self.assertTrue(matches("none(nonexistent: x:y)", self.d))

    def test_unqualified_terms_search_submodels_too(self):
        # Whatever `speaker.display-name:admin` finds, a bare `admin` finds.
        self.assertTrue(matches("participants.name:anna", self.d))
        self.assertTrue(matches("anna", self.d))
        self.assertTrue(matches("pending", self.d))
        self.assertTrue(matches('"anna"', self.d))
        self.assertFalse(matches("carol", self.d))

    def test_unqualified_terms_still_match_the_root(self):
        self.assertTrue(matches("trip", self.d))

    def test_a_field_name_stays_scoped_to_its_own_node(self):
        # `name:anna` addresses the root's `name`, which does not exist.
        self.assertFalse(matches("name:anna", self.d))

    def test_dotted_sugar_behaves_like_any(self):
        self.assertTrue(matches("participants.name:anna", self.d))
        self.assertFalse(matches("participants.name:carol", self.d))

    def test_combined_with_top_level_clauses(self):
        self.assertTrue(matches("title:trip AND none(participants: status:rejected)", self.d))
        self.assertFalse(matches("title:trip AND all(participants: status:confirmed)", self.d))

    def test_conditions_apply_per_child_not_across_children(self):
        # Anna is confirmed and Bob is pending, but no single participant is both.
        self.assertFalse(matches("any(participants: name:bob AND status:confirmed)", self.d))
        self.assertTrue(matches("any(participants: name:anna AND status:confirmed)", self.d))


class DateTests(SimpleTestCase):
    def setUp(self):
        self.d = doc(starts_at="2024-03-15 18:00", city="Berlin", age=24)

    def test_range_over_iso_dates(self):
        self.assertTrue(matches("starts_at:[2024-03-01 TO 2024-03-31]", self.d))
        self.assertFalse(matches("starts_at:[2024-04-01 TO 2024-04-30]", self.d))

    def test_range_bound_may_be_written_in_another_format(self):
        self.assertTrue(matches("starts_at:[01.03.2024 TO 31.03.2024]", self.d))
        self.assertTrue(matches('starts_at:["March 1 2024" TO "March 31 2024"]', self.d))

    def test_open_ended_date_range(self):
        self.assertTrue(matches("starts_at:[2024-01-01 TO *]", self.d))
        self.assertFalse(matches("starts_at:[* TO 2024-01-01]", self.d))

    def test_time_of_day_is_compared(self):
        self.assertFalse(matches('starts_at:["2024-03-15 19:00" TO *]', self.d))
        self.assertTrue(matches('starts_at:["2024-03-15 17:00" TO *]', self.d))

    def test_date_equality_across_formats(self):
        document = doc(starts_at="15.03.2024")
        self.assertTrue(matches("starts_at:2024-03-15", document))
        self.assertFalse(matches("starts_at:2024-03-16", document))

    def test_bare_numbers_stay_numeric(self):
        # dateparser reads "24" as a day of month; ranges must not follow it.
        self.assertTrue(matches("age:[18 TO 30]", self.d))
        self.assertFalse(matches("age:[25 TO 30]", self.d))

    def test_non_dates_fall_back_to_text_comparison(self):
        self.assertTrue(matches("city:[Amsterdam TO Cologne]", self.d))


class BuildDocumentTests(SimpleTestCase):
    def test_builds_fields_children_and_dashboard_columns(self):
        document = build_document({
            "id": "11111111-1111-1111-1111-111111111111",
            "field_values": [
                {"field_slug": "title", "value": "Hello", "language": "en"},
                {"field_slug": "title", "value": "Hallo", "language": "de"},
                {"field_slug": "count", "value": 3, "language": ""},
                {"field_slug": "empty", "value": None, "language": ""},
            ],
            "overflow_data": {"legacy": "kept"},
            "dashboard_columns": [{"key": "seats", "label": "Seats", "renderer": "text", "value": 12}],
            "children": {"participants": [{"field_values": [{"field_slug": "name", "value": "Anna"}]}]},
        })
        self.assertEqual(document.fields["title"], ["Hello", "Hallo"])
        self.assertEqual(document.fields["count"], ["3"])
        self.assertEqual(document.fields["empty"], [])
        self.assertEqual(document.fields["legacy"], ["kept"])
        self.assertEqual(document.fields["seats"], ["12"])
        self.assertEqual(document.children["participants"][0].fields["name"], ["Anna"])
        self.assertTrue(matches("title:hallo", document))
        self.assertTrue(matches("participants.name:anna", document))

    def test_slug_id_fields_are_searchable_by_their_displayed_form(self):
        raw = {"field_values": [{"field_slug": "proposal-id", "value": 6}]}
        document = build_document(raw, {"proposal-id": "PROP"})
        self.assertTrue(matches("proposal-id:PROP-6", document))
        self.assertTrue(matches('proposal-id:"PROP-6"', document))
        self.assertTrue(matches("proposal-id:prop-6", document))
        self.assertTrue(matches("PROP-6", document))
        self.assertTrue(matches("proposal-id:6", document))
        self.assertFalse(matches("proposal-id:PROP-7", document))
        # Without the prefix map only the stored number is searchable.
        self.assertFalse(matches("proposal-id:PROP-6", build_document(raw)))

    def test_slug_id_prefixes_apply_to_submodels_too(self):
        document = build_document(
            {"children": {"items": [{"field_values": [{"field_slug": "proposal-id", "value": 6}]}]}},
            {"proposal-id": "PROP"},
        )
        self.assertTrue(matches("any(items: proposal-id:PROP-6)", document))

    def test_localized_dict_values_are_searchable_in_every_language(self):
        document = build_document({
            "field_values": [{"field_slug": "title", "value": {"en": "Bear", "de": "Bär"}}],
        })
        self.assertTrue(matches("title:bear", document))
        self.assertTrue(matches("title:bär", document))
