"""The claims this repo makes, as tests, so they can be checked rather than read.

Everything here runs against the committed generated files and needs no corpus,
no database and no network. `uv run pytest`.

The corpus measurements in the README cannot be tested this way — they need the
Blue Core ingest archive — so they are reported with their sample size and the
command to reproduce them instead.
"""

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "schema" / "ontology.json").read_text())
CONTEXT = json.loads((ROOT / "context" / "bibframe.jsonld").read_text())["@context"]

BF = "http://id.loc.gov/ontologies/bibframe/"


@pytest.fixture(scope="module")
def validator():
    return jsonschema.Draft202012Validator(SCHEMA)


# --- the generated schema is a valid schema ---------------------------------


def test_schema_is_valid():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_schema_records_its_provenance():
    """A generated file should say what generated it and from what.

    Regenerating against a new BIBFRAME is then a reviewable diff rather than a
    surprise, which is the whole reason for generating instead of hand-writing.
    """
    assert SCHEMA["x-bibframe-version"] == "3.0.1"
    assert "GENERATED" in SCHEMA["$comment"]
    assert "x-generated" not in SCHEMA, (
        "no timestamp: the output has to be byte-identical on every run, or CI "
        "cannot check that the committed artifacts match the generator"
    )


def test_the_four_exclusions_are_recorded():
    """OVERRIDES skips four constraints, and says so in the output.

    Each is a property where every real use violates the flipped constraint,
    which makes the ontology the likelier culprit than the data. Recorded rather
    than silently dropped so the judgement can be disagreed with.
    """
    skipped = SCHEMA["x-skipped"]
    assert set(skipped) == {
        "domain relief",
        "domain mediumComponent",
        "domain ensembleSize",
        "range mediumOfPerformance",
    }
    assert all(reason for reason in skipped.values()), "each has a reason"


# --- what the flipped constraints do ----------------------------------------


@pytest.mark.parametrize(
    ("name", "document", "flagged"),
    [
        (
            "a title typed Title is fine",
            {
                "@id": "http://x/1",
                "@type": ["Instance"],
                "title": [{"@type": ["Title"], "mainTitle": ["x"]}],
            },
            False,
        ),
        (
            "a subclass is fine too",
            {
                "@id": "http://x/1",
                "@type": ["Instance"],
                "title": [{"@type": ["VariantTitle"], "mainTitle": ["x"]}],
            },
            False,
        ),
        (
            "a title typed Agent is not",
            {
                "@id": "http://x/1",
                "@type": ["Instance"],
                "title": [{"@type": ["Agent"], "mainTitle": ["x"]}],
            },
            True,
        ),
        (
            "nested three deep is still reached",
            {
                "@id": "http://x/1",
                "@type": ["Work"],
                "contribution": [
                    {
                        "@type": ["Contribution"],
                        "agent": [{"@type": ["Agent"], "role": [{"@type": ["Title"]}]}],
                    }
                ],
            },
            True,
        ),
        (
            "an untyped reference is unknown, not wrong",
            {
                "@id": "http://x/1",
                "@type": ["Instance"],
                "title": [{"@id": "http://y/2"}],
            },
            False,
        ),
        (
            "a literal range pointing at a resource is wrong",
            {
                "@id": "http://x/1",
                "@type": ["Title"],
                "mainTitle": [{"@id": "http://y/2"}],
            },
            True,
        ),
        (
            "mainTitle on an Instance breaks its domain",
            {"@id": "http://x/1", "@type": ["Instance"], "mainTitle": ["x"]},
            True,
        ),
        (
            "a scalar @type is still accepted",
            {
                "@id": "http://x/1",
                "@type": "Instance",
                "title": [{"@type": "Title", "mainTitle": ["x"]}],
            },
            False,
        ),
        (
            "a value object with a datatype is fine",
            {
                "@id": "http://x/1",
                "@type": ["Instance"],
                "provisionActivity": [
                    {
                        "@type": ["Publication"],
                        "date": [
                            {
                                "@value": "199X",
                                "@type": "http://id.loc.gov/datatypes/edtf",
                            }
                        ],
                    }
                ],
            },
            False,
        ),
    ],
)
def test_constraint_behaviour(validator, name, document, flagged):
    """The flip, case by case.

    Two of these guard against the schema reporting nothing at all, which it did
    twice while being written: once with no recursion, and once with recursion
    reaching only the properties that have a declared range. A schema that
    silently checks nothing scores a perfect 100% and looks like success.
    """
    errors = list(validator.iter_errors(document))
    assert bool(errors) == flagged, f"{name}: {errors[:1]}"


# --- the generated context --------------------------------------------------


def test_every_bibframe_property_is_a_set():
    """@container: @set on everything, which is what pins cardinality.

    Safe to declare for all of them because BIBFRAME contains zero
    owl:FunctionalProperty declarations and zero cardinality restrictions, so
    nothing in it is single-valued.
    """
    terms = {
        key: value
        for key, value in CONTEXT.items()
        if isinstance(value, dict) and not key.startswith("@")
    }
    assert len(terms) > 200
    missing = [key for key, value in terms.items() if value.get("@container") != "@set"]
    # only the rdf:List valued ones, which cannot take a set container
    assert missing == ["mads:componentList", "mads:elementList"], missing


def test_only_always_reference_properties_are_coerced_to_id():
    """@type: @id writes a reference as a bare URI string.

    Applied only where a property is *always* a bare reference. On one that
    sometimes embeds a node it would produce a mix of strings and objects, which
    is the same inconsistency in different clothing.

    hasInstance is deliberately absent despite cbd-01.md listing it among the
    reference-only properties: measured over 200 records it is a string 262
    times, a node with a URI 61 times, and a node with no URI 39 times.
    """
    coerced = {
        key
        for key, value in CONTEXT.items()
        if isinstance(value, dict) and value.get("@type") == "@id"
    }
    assert coerced == {
        "instanceOf",
        "itemOf",
        "hasItem",
        "electronicLocator",
        "generationProcess",
        "descriptionLevel",
    }
    assert "hasInstance" not in coerced
    assert "dcterms:isPartOf" not in coerced


def test_list_valued_properties_get_no_container():
    """mads:componentList and elementList are left undeclared, on purpose.

    @container: @set cannot hold a list object, so the processor skips the term
    and the property comes out as a compact IRI with no container -- which looks
    exactly like the declaration failing. @container: ["@list", "@set"] says what
    is meant and pyld cannot expand it. And @container: @list *loses data*: a
    property holding two lists compacts to one, 6 triples becoming 3, silently.
    Real records do hold two, so that option is out.
    """
    for name in ("mads:componentList", "mads:elementList"):
        assert "@container" not in CONTEXT[name]
        assert CONTEXT[name]["@id"].startswith("http://www.loc.gov/mads/")


def test_foreign_terms_are_declared_with_explicit_ids():
    """Properties from outside BIBFRAME need naming, since the ontology has none.

    A term whose key contains a colon still needs an explicit @id: without one
    the key is read as a compact IRI rather than a term definition, no container
    applies, and the property keeps its full URI in the output.
    """
    foreign = {
        key: value
        for key, value in CONTEXT.items()
        if ":" in key and isinstance(value, dict)
    }
    assert len(foreign) >= 20
    assert all("@id" in value for value in foreign.values())
    assert CONTEXT["rdfs:label"]["@id"] == "http://www.w3.org/2000/01/rdf-schema#label"


def test_context_declares_json_ld_1_1():
    """@version 1.1, because the container and term forms here depend on it."""
    assert CONTEXT["@version"] == 1.1
    assert CONTEXT["@vocab"] == BF
