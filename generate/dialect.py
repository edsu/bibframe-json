"""Emit the dialect schema from the Pydantic models, plus what they cannot say.

    uv run python generate/dialect.py

Two steps, and the second is the interesting one.

`model_json_schema()` gives the structural shape, and gives it cleanly --
zero `oneOf`, zero `allOf` -- because the context pinned cardinality first. That
is the payoff §2.1 of the plan predicted: with every property a list, the models
are `list[X]` rather than `X | list[X] | None`, so there are no unions for the
schema to spell out. For contrast, the IIIF v3 schema has 38 `oneOf` and ships a
414-line error processor whose only job is working out which branch an error
came from.

Then the rules Pydantic cannot express are merged in. A `model_validator` runs in
Python and **never appears in `model_json_schema()`** -- tested: a model
forbidding `@type` and `@language` together rejects the bad object at runtime
while its generated schema contains no `not`, `if` or `dependentSchemas` and
accepts it. So a rule left in a validator is invisible to every consumer using
the published schema, which for a library whose point is a documented contract
is the whole game. Those rules are written in JSON Schema here and merged.
"""

import json
from pathlib import Path

from bibframe_json.models import Hub, Instance, Item, Work

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / "bibframe_json" / "schema" / "dialect.json"

MODELS = {"Work": Work, "Instance": Instance, "Hub": Hub, "Item": Item}


def require_arrays(definition: dict) -> dict:
    """Every unmodelled property must still be an array.

    The core guarantee of the shape, and it was going unchecked for most of the
    data: pydantic emits `additionalProperties: true` for extra="allow", so only
    the dozen properties with fields were constrained while the other 120-odd in
    real records passed whatever they liked.

    Applied to node definitions only, not to Text. A value object's extra keys
    are JSON-LD keywords rather than BIBFRAME properties -- @index and the like
    -- and those are not arrays.
    """
    return {
        **definition,
        "additionalProperties": {
            "$comment": (
                "A property with no field in the models is still a property, so "
                "it is still an array. Without this the guarantee holds only for "
                "the properties that happen to be modelled."
            ),
            "type": "array",
        },
    }


def tolerate_scalar_type(definition: dict) -> dict:
    """Accept @type as a string as well as an array.

    A third coercion lost the same way as the others: every model has a
    `mode="before"` validator normalising a scalar @type, and the generated
    schema demands the array.

    Not only for older records, either. @type is a JSON-LD keyword, so no
    `@container` can pin it -- a node with one type gets a string and a node with
    two gets an array, today, under the current context. bf:extent carries
    "@type": "Extent" for exactly this reason.
    """
    properties = definition.get("properties", {})
    if "@type" not in properties:
        return definition
    array_form = properties["@type"]
    return {
        **definition,
        "properties": {
            **properties,
            "@type": {
                "anyOf": [array_form, {"type": "string"}],
                "$comment": (
                    "An array when a node has several types, a string when it "
                    "has one. @type is a keyword, so no @container reaches it."
                ),
            },
        },
    }


def wrap_text(generated: dict) -> dict:
    """The shapes a literal may take, which the generated schema does not know.

    `Text` has a `mode="before"` validator accepting a bare string, and **no
    validator of any kind appears in `model_json_schema()`**. So the generated
    definition demands the `{"@value": ...}` object form and rejects `"x"` --
    while the model accepts it, and 14,021 of 14,792 literals in a 200-record
    sample are bare strings. A schema that rejects 95% of the literals its own
    models accept is worse than no schema.

    This is the Pydantic caveat at full size. It is not only the exclusion rules
    that go missing: every coercion does, and the coercions are what make the
    models tolerant of the shapes real data takes.
    """
    return {
        "$comment": (
            "A literal: a plain scalar, or a value object. The scalar branch "
            "exists because Text.accept_a_bare_string is a validator, and "
            "model_json_schema() emits no validators."
        ),
        "anyOf": [
            {"type": ["string", "number", "boolean"]},
            {
                **generated,
                "not": {"required": ["@type", "@language"]},
                "$comment": (
                    "A value object carries at most one of @type or @language, "
                    "never both: required by JSON-LD and confirmed in the data "
                    "-- 703 with a datatype, 68 with a language, none with "
                    "both. Written here rather than as a model validator: the "
                    "models parse and this judges, and a validator would not "
                    "reach model_json_schema() anyway."
                ),
            },
        ],
    }


def wrap_ref(generated: dict) -> dict:
    """The shapes a reference may take.

    `@type: @id` in the context writes a reference as a bare URI string, so the
    string branch is the common one -- and again invisible to the generated
    schema, since Ref.accept_a_bare_uri is a validator.
    """
    return {
        "$comment": (
            "A reference: a bare URI string, as @type: @id writes it, or a node."
        ),
        "anyOf": [
            {"type": "string"},
            {
                # the object branch is a node, so it needs the scalar @type
                # tolerance too -- @type is a keyword and the context cannot pin
                # it, so a node with one type carries a string and a node with
                # two carries an array. Missing this rejected 60 of 300 real
                # records on bf:extent and bf:note.
                **tolerate_scalar_type(generated),
                "not": {
                    "required": ["@id"],
                    "properties": {"@id": {"pattern": "^_:"}},
                },
                "$comment": (
                    "A blank node carries no @id. The parser-assigned _:b0 "
                    "labels are an artefact of framing rather than data, and "
                    "leaving them in makes two identical values distinguishable "
                    "by accident.\n\n"
                    "Note the `required` beside the `properties`: without it the "
                    "`not` is vacuously true for any object with no @id at all, "
                    "and the rule rejects every node instead of the blank ones. "
                    "That mistake was made twice while writing this."
                ),
            },
        ],
    }


# The three coercions in models.py that model_json_schema() does not emit.
WRAPPERS = {"Text": wrap_text, "Ref": wrap_ref}


def build() -> dict:
    """One schema, with a definition per resource type.

    Per-type definitions rather than one flat node shape, following IIIF v4's
    file-per-resource layout: an error path then reads
    `#/$defs/Work/properties/title` instead of needing interpretation. Pydantic
    produces the $defs structure for free.
    """
    defs: dict[str, dict] = {}
    for name, model in MODELS.items():
        schema = model.model_json_schema(by_alias=True, ref_template="#/$defs/{model}")
        defs.update(schema.pop("$defs", {}))
        defs[name] = schema

    for name, wrap in WRAPPERS.items():
        if name not in defs:
            raise SystemExit(f"cannot wrap missing definition {name!r}")
        defs[name] = wrap(defs[name])

    # every node-ish definition tolerates a scalar @type and requires that its
    # unmodelled properties are arrays. Text is neither: it is a value object,
    # whose extra keys are keywords rather than properties.
    for name, definition in list(defs.items()):
        if name not in WRAPPERS:
            defs[name] = require_arrays(tolerate_scalar_type(definition))

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://bibframe-json.org/schema/dialect.json",
        "title": "Blue Core's JSON shape, per resource type",
        "$comment": (
            "GENERATED by generate/dialect.py from bibframe_json/models.py. Do "
            "not edit.\n\n"
            "Structural only: it says nothing about which BIBFRAME types may "
            "appear where, which is schema/ontology.json's job. A record can "
            "satisfy this and still put an Agent where a Title belongs.\n\n"
            "Open by design. BIBFRAME has 226 properties and the data uses 136; "
            "additionalProperties is not restricted, so a property with no field "
            "here passes rather than failing."
        ),
        # anyOf, not oneOf. The four resource types are open and share a base,
        # so a Work satisfies Instance as well; oneOf demands exactly one match
        # and would reject every document. It is also what §2.2 of the plan warns
        # about -- IIIF v3's 38 oneOf are why its validator needs 414 lines to
        # work out which branch an error belongs to.
        "anyOf": [{"$ref": f"#/$defs/{name}"} for name in MODELS],
        "$defs": defs,
    }


def main() -> None:
    schema = build()
    OUTPUT.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    text = json.dumps(schema)
    print(f"wrote {OUTPUT.relative_to(HERE.parent)}")
    print(f"  {len(schema['$defs'])} definitions, {len(text):,} bytes")
    print(
        f"  oneOf {text.count('"oneOf"')}, allOf {text.count('"allOf"')}, "
        f"anyOf {text.count('"anyOf"')}"
    )
    print(
        f"  {len(WRAPPERS)} definitions wrapped for coercions Pydantic does "
        f"not emit: {', '.join(WRAPPERS)}"
    )


if __name__ == "__main__":
    main()
