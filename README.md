# bibframe-json

[![Test](https://github.com/edsu/bibframe-json/actions/workflows/test.yml/badge.svg)](https://github.com/edsu/bibframe-json/actions/workflows/test.yml)

*bibframe-json* provides a predictable, opinionated JSON shape for [BIBFRAME]
data. The goal is to make BIBFRAME data more accessible to people who want to
parse it as JSON without needing an RDF processing library, and knowledge of
the RDF data model. But perhaps it will also function as a gateway for people
who want to take a step beyond the JSON to learn more.

BIBFRAME is large and loosely constrained, and there are many ways to write it as
RDF and JSON-LD. bibframe-json describes one shape, documents what that shape guarantees, and gives
you models for reading it without walking dictionaries by hand. The shape is the
framed JSON-LD Blue Core stores per resource: a Work, Instance, Hub or Item.

bibframe-json follows the [LOUD](https://linked.art/loud/) principles, and the
`@container: @set` rule from
[Linked Art](https://linked.art/api/1.0/json-ld/) in particular: if a property
can ever have more than one value, it always has an array. That single decision
is what makes everything else here possible.

## What the shape guarantees

- every property is an array, even with one value
- references are plain URI strings, not `{"@id": ...}` wrappers
- no blank node carries an `@id`
- a value object has `@value` and at most one of `@type` or `@language`
- `@type` is a list of classes on a node, and a datatype string on a value
  object — tell them apart by whether `@value` is present

## The JSON

Here's what an abridged JSON Instance looks like:

```json
{
  "@id": "http://id.loc.gov/resources/instances/23867197",
  "@type": ["Instance"],
  "instanceOf": ["http://id.loc.gov/resources/works/23867197"],
  "title": [
    {
      "@type": ["Title"],
      "mainTitle": ["Minority voices from the academic superstructure"]
    }
  ],
  "identifiedBy": [
    { "@type": ["Lccn"], "rdf:value": ["  2024038899"] },
    {
      "@type": ["Isbn"],
      "qualifier": ["hardcover"],
      "rdf:value": ["9781668499092"]
    }
  ],
  "provisionActivity": [
    {
      "@type": ["ProvisionActivity", "Publication"],
      "bflc:simplePlace": ["Hershey, PA"],
      "bflc:simpleDate": ["[2025]"]
    }
  ]
}
```

Note `instanceOf` has a bare URI, because the context declares it `@type: @id`.
Six properties are written that way: `instanceOf`, `itemOf`, `hasItem`,
`electronicLocator`, `generationProcess`, `descriptionLevel`.

A literal keeps its language or its datatype when it has one:

```json
"title": [
  { 
    "@type": ["Title"],
    "mainTitle": ["Trudy Instituta Obshchcei Fiziki"]
  },
  {
    "@type": ["Title"],
    "mainTitle": [
      {
        "@value": "Труды Института Общц̳еи Физики",
        "@language": "ru-cyrl"
      }
    ]
  }
],
"date": [
  {
    "@value": "199X",
    "@type": "http://id.loc.gov/datatypes/edtf"
  }
]
```

Those two titles are one title in two scripts, and the language tag
is the only thing telling them apart. And `date` carries three datatypes in real
records — `xsd:date`, `xsd:dateTime` and EDTF — where EDTF encodes uncertainty,
so `199X` is not a date that can be parsed as one.

## From Python

To simplify usage of the data from Python, Pydantic models are included that
provide helper properties for accessing the data without needing to hunt and
peck in the JSON.

`load()` takes a `dict` of parsed JSON and returns the model for whatever the
record says it is:

```python
import json

import bibframe_json

record = json.load(open("instance.json"))
instance = bibframe_json.load(record)                            # a Work, Instance, Hub or Item

instance.main_title                  # "Minority voices from the academic superstructure"
instance.instance_of[0]              # "http://id.loc.gov/resources/works/23867197"

[(i.kind, str(i.value[0]).strip())   # [("Lccn", "2024038899"),
 for i in instance.identified_by]    #  ("Isbn", "9781668499092")]

instance.provision_activity[0].simple_place[0]     # "Hershey, PA"
```

Literals behave as text but remember what they are:

```python
title = instance.main_title
str(title)            # the text, and what {{ title }} renders in a template
title.language        # "ru-cyrl", or None

work.titles_in(None)         # the romanised forms
work.titles_in("ru-cyrl")    # the vernacular ones

date = instance.provision_activity[0].date[0]
date.approximate      # True for EDTF: 199X, 1970?, intervals
```

The models themselves are open. BIBFRAME has 226 properties and real records
use 136, so an unmodeled one is kept and reachable rather than rejected:

```python
work.get("bflc:aap")     # ["Prokhorov, A. M."]
work.get("neverSeen")    # []
```

`Work`, `Instance`, `Hub` and `Item` share a `Resource` base, so one set of
template partials serves all four.

If you want a particular type rather than whatever the record claims, the models
take a dict or JSON text directly:

```python
Instance.model_validate(record)          # a dict
Instance.model_validate_json(text)       # JSON text, no json.loads needed
```

## Validating

`load()` **parses**; `validate()` **judges**. They are not the same, and the
difference is worth keeping in mind: parsing checks field types and quietly
accepts the rest, since of the 136 properties in real records only about a dozen
have fields. A record can load perfectly and still be malformed.

```python
for finding in bibframe_json.validate(record):
    print(finding)

# [dialect] subject/0: a blank node must not carry an @id
# [ontology] the record: mainTitle does not belong on ['Work'] according to its rdfs:domain
```

Each `Finding` has a `layer`, a `path` and a `message`, and `is_error` is true
for the dialect layer. Either layer can be asked for on its own —
`validate(record, ontology=False)` is the useful gate in a pipeline, since those
are the guarantees a consumer depends on.

The two schemas ship with the package and answer different questions. Both are
plain JSON Schema, usable from any language:

```python
from bibframe_json import context, schema

schema("dialect")     # the shape
schema("ontology")    # BIBFRAME's domains and ranges, as constraints
context()             # the JSON-LD context that produces the shape
```

The dialect schema is structural: arrays, references, value objects, blank
nodes. It says nothing about which BIBFRAME types may appear where, so a record
can satisfy it and still put an `Agent` where a `Title` belongs.

The ontology schema is that second question, generated from the
`rdfs:domain` and `rdfs:range` statements in BIBFRAME's vocabulary. Those are
*inference rules* under OWL. So asserting that `bf:title` has domain `bf:Work`
does not make a non-Work invalid, it infers the subject is a Work. We read them
as closed-world constraints instead, the way SHACL does, and emits the result
as JSON Schema.

Its findings are warnings rather than errors. Four constraints are excluded
outright, listed in `OVERRIDES` with reasons, because every real use violates
them and the ontology is the likelier culprit:

| Property | Ontology says | Every real use |
| --- | --- | --- |
| `bf:relief` | domain `bf:Instance` | `bf:Cartographic`, a Work |
| `bf:mediumComponent` | domain `bf:Work` | `bf:Ensemble` |
| `bf:ensembleSize` | domain `bf:Work` | `bf:Ensemble` |
| `bf:mediumOfPerformance` | range `bf:MediumOfPerformance` | `mads:Medium` |

The flip also has most purchase over a full BIBFRAME graph rather than a stored
resource: 99.8% of assertions conform in a CBD from id.loc.gov, while in a stored
per-resource record only 39% of node values carry a `@type` a range can check —
Blue Core keeps a referenced resource's description in its own row.

## What is generated, and what is not

```
bibframe_json/context/bibframe.jsonld  generated   249 terms: @container: @set, @type: @id
bibframe_json/schema/ontology.json     generated   150 range + 110 domain constraints
bibframe_json/schema/dialect.json      generated   from the models, plus three hand-written rules
bibframe_json/models.py                written     Pydantic models and their helpers
generate/bibframe.rdf                  vendored    BIBFRAME 3.0.1, issued 2025-12-03
```

```
uv run python generate/from_ontology.py    # context + ontology schema
uv run python generate/dialect.py          # dialect schema
uv run pytest                              # 44 tests, no corpus or network
```

Enumerating 249 `@container` declarations is mechanical, so it is generated;
deciding which properties a template needs is editorial, so the models are
written by hand. `rdflib` is a dev dependency — the ontology is read at build
time and nothing at runtime parses RDF.

Three rules in `schema/dialect.json` are hand-written rather than emitted by
Pydantic, because a Pydantic validator never appears in `model_json_schema()`:
that a literal may be a bare string, that a reference may be a bare URI, and that
`@type` may be a string. Without them the schema rejects what its own models
accept.

## Status

This is meant to be updated both as BIBFRAME changes and as needs for making
the data more accessible from Python change. Please send issues and PRs when
things are needed. The project is being built primarily for use cases in the
[Blue Core] project, but the overarching goal is to make BIBFRAME more
accessible as JSON.

[BIBFRAME]: https://bibframe.org
[Blue Core]: https://bluecore.info/
