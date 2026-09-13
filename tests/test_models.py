"""The models, the schema generated from them, and whether they agree.

That last one is the point. A published schema that accepts less than its own
models do is worse than no schema, because a consumer trusts it — and the gap is
easy to open, since a Pydantic validator never appears in
`model_json_schema()`. `test_schema_and_models_agree` is the guard.
"""

import json

import jsonschema
import pytest

from bibframe_json import EDTF, Instance, Ref, Text, Work, schema

DIALECT = schema("dialect")


@pytest.fixture(scope="module")
def dialect():
    return jsonschema.Draft202012Validator(DIALECT)


# Records covering every shape the dialect tolerates. Used twice over: once for
# the models, once for the schema, so the two are held to the same standard.
ACCEPTED = {
    "plain literals": {
        "@id": "https://x/1",
        "@type": ["Work"],
        "title": [{"@type": ["Title"], "mainTitle": ["a title"]}],
    },
    "value object with a language": {
        "@id": "https://x/1",
        "@type": ["Work"],
        "title": [
            {
                "@type": ["Title"],
                "mainTitle": [{"@value": "Труды", "@language": "ru-cyrl"}],
            }
        ],
    },
    "value object with a datatype": {
        "@id": "https://x/1",
        "@type": ["Instance"],
        "provisionActivity": [
            {"@type": ["Publication"], "date": [{"@value": "199X", "@type": EDTF}]}
        ],
    },
    "bare URI reference": {
        "@id": "https://x/1",
        "@type": ["Work"],
        "instanceOf": ["https://x/2"],
    },
    "scalar @type, as bf:extent really carries it": {
        "@id": "https://x/1",
        "@type": ["Instance"],
        "extent": [{"@type": "Extent", "rdfs:label": ["ix, 319 pages"]}],
    },
    "a node with two types": {
        "@id": "https://x/1",
        "@type": ["Instance"],
        "note": [
            {
                "@type": ["Note", "http://id.loc.gov/vocabulary/mnotetype/biblio"],
                "rdfs:label": ["Includes index."],
            }
        ],
    },
    "an unmodelled property": {
        "@id": "https://x/1",
        "@type": ["Work"],
        "somethingElse": ["kept"],
    },
    "types the ontology would reject, which is not this schema's job": {
        "@id": "https://x/1",
        "@type": ["Work"],
        "title": [{"@type": ["Agent"], "mainTitle": ["x"]}],
    },
}

REJECTED = {
    "a value object with both tags": {
        "@id": "https://x/1",
        "@type": ["Work"],
        "title": [
            {
                "@type": ["Title"],
                "mainTitle": [
                    {"@value": "x", "@type": "xsd:string", "@language": "en"}
                ],
            }
        ],
    },
    "a blank node keeping its @id": {
        "@id": "https://x/1",
        "@type": ["Work"],
        "subject": [{"@id": "_:b0"}],
    },
}


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_schema_accepts(dialect, name):
    assert list(dialect.iter_errors(ACCEPTED[name])) == []


@pytest.mark.parametrize("name", sorted(REJECTED))
def test_schema_rejects(dialect, name):
    assert list(dialect.iter_errors(REJECTED[name])) != []


@pytest.mark.parametrize("name", sorted(ACCEPTED))
def test_schema_and_models_agree(dialect, name):
    """Whatever the schema accepts, the models parse.

    The gap this guards opened twice while being written. Both times the schema
    was stricter than the models, because a `mode="before"` validator is
    invisible to `model_json_schema()`: the bare-string literal, the bare-URI
    reference, and the scalar @type are all coercions the models do and the
    generated schema knew nothing about. The scalar @type alone rejected 60 of
    300 real records.
    """
    record = ACCEPTED[name]
    assert list(dialect.iter_errors(record)) == [], "schema accepts"
    model = Instance if "Instance" in record["@type"] else Work
    model.model_validate(record)


# --- what the models are for -------------------------------------------------


def test_a_literal_keeps_its_language():
    """The romanised and vernacular forms of one title stay distinguishable.

    `bluecore_api` currently joins them with a comma, so a record with both
    renders as though it had two different titles.
    """
    work = Work.model_validate(
        {
            "@id": "https://x/1",
            "@type": ["Work"],
            "title": [
                {"@type": ["Title"], "mainTitle": ["Trudy Instituta"]},
                {
                    "@type": ["Title"],
                    "mainTitle": [{"@value": "Труды", "@language": "ru-cyrl"}],
                },
            ],
        }
    )
    assert [str(t) for t in work.titles_in(None)] == ["Trudy Instituta"]
    assert [str(t) for t in work.titles_in("ru-cyrl")] == ["Труды"]


def test_a_literal_keeps_its_datatype():
    """EDTF encodes uncertainty, so reading it as a date would be wrong."""
    approximate = Text.model_validate({"@value": "199X", "@type": EDTF})
    exact = Text.model_validate("1994-03-01")
    assert approximate.approximate and not exact.approximate
    assert str(approximate) == "199X", "and still behaves as its text"


def test_the_models_parse_rather_than_judge():
    """A record breaking the shape rules still loads, deliberately.

    Required by JSON-LD and never seen in the data -- 703 value objects with a
    datatype, 68 with a language, none with both -- but the rule lives in
    schema/dialect.json rather than here. Enforcing a few rules in the models
    and the rest in the schema would make load() unpredictable, and would put
    each rule in two places since a validator never reaches
    model_json_schema().

    validate() is what reports it, and test_validate.py asserts that it does.
    """
    both = {"@value": "x", "@type": "xsd:string", "@language": "en"}
    text = Text.model_validate(both)
    assert text.datatype and text.language, "parsed, not judged"

    blank = Work.model_validate(
        {"@id": "https://x/1", "@type": ["Work"], "subject": [{"@id": "_:b0"}]}
    )
    assert blank.subject[0].uri == "_:b0", "likewise"


def test_main_title_prefers_a_title_over_a_variant():
    """What a template would otherwise write as work.title[0].main[0], with a
    check at every step, and every consumer would write again."""
    work = Work.model_validate(
        {
            "@id": "https://x/1",
            "@type": ["Work"],
            "title": [
                {"@type": ["VariantTitle"], "mainTitle": ["Proceedings"]},
                {"@type": ["Title"], "mainTitle": ["Trudy Instituta"]},
            ],
        }
    )
    assert str(work.main_title) == "Trudy Instituta"


def test_unmodelled_properties_survive():
    """BIBFRAME has 226 properties and the data uses 136; modelling a dozen and
    rejecting the rest would make these useless for anything else."""
    work = Work.model_validate(
        {
            "@id": "https://x/1",
            "@type": ["Work"],
            "bflc:aap": ["Prokhorov, A. M."],
        }
    )
    assert work.get("bflc:aap") == ["Prokhorov, A. M."]
    assert work.get("neverSeen") == []


def test_primary_contribution_is_found_by_its_extra_type():
    work = Work.model_validate(
        {
            "@id": "https://x/1",
            "@type": ["Work"],
            "contribution": [
                {"@type": ["Contribution"], "agent": ["https://x/a"]},
                {
                    "@type": ["Contribution", "PrimaryContribution"],
                    "agent": ["https://x/b"],
                },
            ],
        }
    )
    assert len(work.primary_contributions) == 1
    assert work.primary_contributions[0].agent[0].uri == "https://x/b"


def test_a_reference_renders_as_its_label_when_it_has_one():
    assert str(Ref.model_validate("https://x/1")) == "https://x/1"
    assert (
        str(Ref.model_validate({"@id": "https://x/1", "rdfs:label": ["Prokhorov"]}))
        == "Prokhorov"
    )


# --- the schema the models produce -------------------------------------------


def test_the_generated_schema_has_no_oneOf():
    """The payoff of pinning cardinality in the context first.

    With every property a list, the models are list[X] rather than
    X | list[X] | None, so there are no unions for the schema to spell out. The
    IIIF v3 schema has 38 oneOf and ships a 414-line error processor whose only
    job is working out which branch an error came from.
    """
    text = json.dumps(DIALECT)
    assert '"oneOf"' not in text
    assert '"allOf"' not in text


def test_the_hand_written_rules_are_present():
    """Guard against a regeneration quietly dropping them.

    These two cannot come from the models: a Pydantic validator never appears in
    model_json_schema(), so they are written in JSON Schema in generate/dialect.py
    and merged. If someone regenerates without that step the schema still looks
    fine and silently stops checking.
    """
    text = json.dumps(DIALECT)
    assert '"not"' in text, "the exclusions survived generation"
    assert "^_:" in text, "the blank node rule survived"
    assert DIALECT["$defs"]["Text"]["anyOf"][0]["type"] == [
        "string",
        "number",
        "boolean",
    ], "a literal may be a bare scalar"
