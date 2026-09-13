"""Validate a record against the shipped JSON Schemas.

Two different things are easy to confuse, and the names here try to keep them
apart.

`load()` **parses**. It turns a dict into a Work, Instance, Hub or Item so you
can read it with attributes instead of subscripts. Pydantic's method for that is
called `model_validate`, which is unfortunate: it checks field types and coerces
the shapes real data comes in, and it is deliberately permissive about
everything else. An unmodelled property passes through untouched, and of 136
properties in real records only about a dozen have fields. Parsing something
successfully says very little about whether it is well formed.

`validate()` **checks conformance**, against the two JSON Schemas in
`bibframe_json/schema/`. That is where the guarantees live:

    dialect    the shape: arrays, references, value objects, blank nodes.
               Errors -- a document that breaks these cannot be read reliably.

    ontology   BIBFRAME's own domains and ranges, read as constraints.
               Warnings -- when the data and the ontology disagree the ontology
               is often the one that is behind, and four constraints are
               excluded outright for that reason.

So `load()` for reading and `validate()` for judging. A record can parse
perfectly and still fail both schemas.
"""

import json
from collections.abc import Iterator
from functools import cache
from importlib.resources import files
from typing import Any, NamedTuple

import jsonschema

from bibframe_json.models import Hub, Instance, Item, Resource, Work

DIALECT = "dialect"
ONTOLOGY = "ontology"

# Which model a record belongs to, by the type it claims. Order matters: a Work
# and an Instance share a base, so the first match wins rather than the longest.
BY_TYPE: tuple[tuple[str, type[Resource]], ...] = (
    ("Instance", Instance),
    ("Work", Work),
    ("Hub", Hub),
    ("Item", Item),
)


class Finding(NamedTuple):
    """One thing wrong with a record.

    `layer` is "dialect" or "ontology", and it is the part a caller most needs:
    a dialect finding is a defect in the shape, an ontology finding is a
    disagreement with BIBFRAME that may well be the ontology's fault.
    """

    layer: str
    path: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.layer == DIALECT

    def __str__(self) -> str:
        where = self.path or "the record"
        return f"[{self.layer}] {where}: {self.message}"


@cache
def schema(name: str) -> dict[str, Any]:
    """One of the shipped schemas, by name.

    Read through importlib.resources rather than a relative path, so it works
    from an installed package and not only from a checkout. The schemas live
    inside bibframe_json/ for the same reason.
    """
    if name not in (DIALECT, ONTOLOGY):
        raise ValueError(f"no such schema: {name!r}")
    text = (files("bibframe_json") / "schema" / f"{name}.json").read_text()
    return json.loads(text)


@cache
def context() -> dict[str, Any]:
    """The JSON-LD context that produces this shape.

    Shipped so a consumer can frame their own records into it, or point a
    JSON-LD processor at the same terms this library assumes.
    """
    text = (files("bibframe_json") / "context" / "bibframe.jsonld").read_text()
    return json.loads(text)


@cache
def _validator(name: str) -> jsonschema.protocols.Validator:
    return jsonschema.Draft202012Validator(schema(name))


def _causes(error: jsonschema.ValidationError, depth: int = 0) -> Iterator:
    """The errors that actually explain a failure.

    Both schemas use anyOf -- the dialect over four resource types and over the
    shapes a literal or a reference may take, the ontology over string-or-array
    @type. jsonschema reports the anyOf itself, whose message is the whole record
    printed back at you with "is not valid under any of the given schemas". The
    reason is further down, in error.context.

    This is the problem IIIF hit: their v3 schema's 38 oneOf are why their
    validator needs a 414-line error processor. Worth noting that switching from
    oneOf to anyOf does not avoid it -- it avoids wrongly rejecting a record that
    matches two branches, which is a different bug.

    The keywords are named rather than taking the deepest or the first, because
    which branch is "the" failure depends on how the schema happens to be
    ordered, and a named set gives the same answer either way.
    """
    if not error.context or depth > 6:
        yield error
        return
    explains = {"not", "required", "pattern", "type", "minItems", "const", "enum"}
    found = [
        cause
        for sub in error.context
        for cause in _causes(sub, depth + 1)
        if cause.validator in explains
    ]
    yield from (found or [error])


def _describe(error: jsonschema.ValidationError) -> str:
    """A message that says what is wrong rather than which keyword noticed."""
    if error.validator == "required":
        present = error.instance if isinstance(error.instance, dict) else {}
        missing = [key for key in error.validator_value if key not in present]
        return f"missing {', '.join(missing)}" if missing else error.message
    if error.validator == "not":
        wrong = error.validator_value
        if wrong.get("properties", {}).get("@id", {}).get("pattern") == "^_:":
            return "a blank node must not carry an @id"
        required = list(wrong.get("required", []))
        if set(required) == {"@type", "@language"}:
            return "a value object carries at most one of @type or @language"
        if len(required) == 1 and not wrong.get("properties"):
            # the ontology's domain constraint: "if the node is not of the right
            # type, this property must be absent"
            types = (
                error.instance.get("@type")
                if isinstance(error.instance, dict)
                else None
            )
            on = f" on {types}" if types else ""
            return f"{required[0]} does not belong{on} according to its rdfs:domain"
        return "not allowed here"
    if error.validator == "minItems":
        return "is empty"
    return error.message


def validate(
    record: dict[str, Any],
    *,
    dialect: bool = True,
    ontology: bool = True,
) -> list[Finding]:
    """Check a record against the schemas, and say what is wrong.

        for finding in validate(record):
            print(finding)

    Either layer can be asked for alone. `dialect=True, ontology=False` is the
    useful gate in a pipeline, since those are the guarantees a consumer depends
    on; ontology-only is the interesting report to run across a corpus.

    Findings are deduplicated by path and message, because an anyOf can surface
    the same cause through more than one branch.
    """
    # (layer, path, message, validator), so the filter below can work on the
    # keyword rather than on the shape of the message
    raw: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    explained: set[tuple[str, str]] = set()

    layers = [
        name for name, wanted in ((DIALECT, dialect), (ONTOLOGY, ontology)) if wanted
    ]
    for layer in layers:
        for error in _validator(layer).iter_errors(record):
            for cause in _causes(error):
                path = "/".join(str(part) for part in cause.absolute_path)
                message = _describe(cause)
                key = (layer, path, message)
                if key in seen:
                    continue
                seen.add(key)
                raw.append((layer, path, message, cause.validator))
                if cause.validator != "type":
                    explained.add((layer, path))

    # A failing anyOf reports every branch, so a node rejected for carrying an
    # @id also reports "is not of type string" from the branch that wanted a bare
    # URI. Where a path has a real explanation, the type complaint is noise.
    return [
        Finding(layer, path, message)
        for layer, path, message, validator in raw
        if not (validator == "type" and (layer, path) in explained)
    ]


def load(record: dict[str, Any]) -> Resource:
    """Parse a record into the model for whatever it says it is.

    Reading, not judging -- see the module docstring. Raises pydantic's
    ValidationError if the record cannot be parsed at all, which is a lower bar
    than conforming: call validate() for that.
    """
    types = record.get("@type") or []
    if isinstance(types, str):
        types = [types]
    for name, model in BY_TYPE:
        if name in types:
            return model.model_validate(record)
    raise ValueError(
        f"no model for {types or 'a record with no @type'}; expected one of "
        f"{', '.join(name for name, _ in BY_TYPE)}"
    )
