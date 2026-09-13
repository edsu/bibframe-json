"""Turn the BIBFRAME ontology into JSON Schema constraints.

Build-time only. This is the one module in the project that imports rdflib; the
library itself ships the schema this emits and never touches RDF.

    uv run python generate/from_ontology.py

The idea, and why it is not the obvious thing. `rdfs:domain` and `rdfs:range`
under OWL are *inference rules, not constraints*: asserting that bf:title has
domain bf:Work does not make a non-Work invalid, it infers that the subject is a
Work. They cannot be violated even in principle, so "validating against the
ontology" as OWL is a category error rather than a weak check.

SHACL's move is to read the same vocabulary the other way, as closed-world
constraints. That is what this does, and the result is a schema derived from
BIBFRAME rather than hand-written:

    rdfs:range R   ->  a value of this property, if it says what it is, is typed
                       R or one of R's subclasses
    rdfs:domain D  ->  this property appears only on a node typed D or one of
                       D's subclasses

Measured against 200 records of the Blue Core corpus before this was written:
31,292 checkable assertions, 99.8% conforming. The 50 violations are almost all
the ontology being incomplete rather than the data being wrong, which is what
OVERRIDES is for.
"""

import json
from collections import defaultdict
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, URIRef

HERE = Path(__file__).resolve().parent
ONTOLOGY = HERE / "bibframe.rdf"
OUTPUT = HERE.parent / "schema" / "ontology.json"
CONTEXT_OUTPUT = HERE.parent / "context" / "bibframe.jsonld"

BF = "http://id.loc.gov/ontologies/bibframe/"
ONTOLOGY_IRI = URIRef(BF)

# Constraints we decline to enforce, and why. Each of these is a property whose
# flipped constraint *every* real use violates -- measured over 200 corpus
# records, zero conforming uses each. When data and ontology disagree on every
# single occurrence, the ontology is the likelier culprit: music and cartographic
# modelling it has not caught up with, and MADS classes it does not know about.
#
# Kept here rather than silently dropped, so that the next person can disagree
# with the judgement, and so that the generated file can record what was skipped.
OVERRIDES: dict[tuple[str, str], str] = {
    ("domain", "relief"): (
        "declares bf:Instance; every use is on bf:Cartographic, a Work. The "
        "ontology looks simply wrong -- relief is a work-level characteristic. "
        "Worth reporting upstream."
    ),
    ("domain", "mediumComponent"): (
        "declares bf:Work; every use is on bf:Ensemble, which is not a Work "
        "subclass. Music modelling the ontology has not caught up with."
    ),
    ("domain", "ensembleSize"): (
        "declares bf:Work; every use is on bf:Ensemble, as with mediumComponent."
    ),
    ("range", "mediumOfPerformance"): (
        "declares bf:MediumOfPerformance; every use points at mads:Medium, a "
        "class from a vocabulary BIBFRAME does not reference."
    ),
}


def subclass_closure(graph: Graph) -> dict[URIRef, set[URIRef]]:
    """For each class, itself and everything it is a subclass of.

    Iteratively rather than recursively: the hierarchy is shallow in practice but
    nothing guarantees it is acyclic, and a cycle in someone's ontology should
    not be a RecursionError in our build.

    Flattening the hierarchy here is what keeps it out of the runtime. The schema
    enumerates acceptable types per property, so nothing has to walk a class tree
    to validate a document.
    """
    parents: dict[URIRef, set[URIRef]] = defaultdict(set)
    for child, _, parent in graph.triples((None, RDFS.subClassOf, None)):
        if isinstance(child, URIRef) and isinstance(parent, URIRef):
            parents[child].add(parent)

    closures: dict[URIRef, set[URIRef]] = {}
    for start in {
        s for s in graph.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)
    } | set(parents):
        seen: set[URIRef] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(parents.get(current, ()))
        closures[start] = seen
    return closures


def subclasses_of(closures: dict[URIRef, set[URIRef]], parent: URIRef) -> list[str]:
    """Every class that satisfies a constraint naming `parent`, as a term name.

    Term names rather than URIs, because the dialect compacts them: a node's
    @type reads "Title", not the full IRI. Anything outside the BIBFRAME
    namespace keeps its URI, since that is how the dialect writes it.
    """
    names = {
        term(child) for child, ancestors in closures.items() if parent in ancestors
    }
    names.add(term(parent))
    return sorted(names)


def term(uri: URIRef) -> str:
    """A class or property URI as the dialect writes it."""
    text = str(uri)
    return text.removeprefix(BF)


def properties(graph: Graph) -> set[URIRef]:
    """Every property BIBFRAME declares, of whatever flavour."""
    found: set[URIRef] = set()
    for kind in (
        OWL.ObjectProperty,
        OWL.DatatypeProperty,
        OWL.AnnotationProperty,
        RDF.Property,
    ):
        found |= {
            s
            for s in graph.subjects(RDF.type, kind)
            if isinstance(s, URIRef) and str(s).startswith(BF)
        }
    return found


# Acceptable-type sets are named once in $defs/class and referenced, not inlined
# at each use. The same closures recur constantly: inlined, there were 386 enum
# occurrences for only 78 distinct lists -- subclasses of bf:Instance appearing 78
# times and of bf:Work 48 times -- which is a quarter of the file spent repeating
# itself. Naming them also makes a constraint legible, since a reader sees
# "class/Work" rather than counting 22 class names.
#
# The shape is borrowed from IIIF's v4 schema, which keeps shared definitions
# (properties.json, agent.json) apart from its per-resource files.
CLASS_DEFS = "#/$defs/class/"
LITERAL_DEF = "#/$defs/literal"


def class_matcher(closures: dict[URIRef, set[URIRef]], target: URIRef) -> dict:
    """Matches an @type that includes this class or one of its subclasses.

    @type may be a string or an array. The dialect normalises to an array, but a
    record written before that did not, so both are accepted.
    """
    acceptable = subclasses_of(closures, target)
    return {
        "$comment": f"{term(target)} or a subclass ({len(acceptable)} accepted)",
        "anyOf": [
            {"enum": acceptable},
            {"type": "array", "contains": {"enum": acceptable}},
        ],
    }


def range_constraint(target: URIRef) -> dict:
    """What a value of this property must look like.

    The condition on @type is load-bearing. A value the document only points at,
    carrying no type of its own, is *unknown* rather than wrong -- 1,004 of the
    assertions in the 200-record sample are in that state, and a validator that
    called them violations would be reporting on its own ignorance.
    """
    if target == RDFS.Literal:
        return {"$ref": LITERAL_DEF}
    return {
        "$comment": f"rdfs:range {term(target)}",
        "if": {"type": "object", "required": ["@type"]},
        "then": {"properties": {"@type": {"$ref": CLASS_DEFS + term(target)}}},
    }


def domain_constraint(name: str, target: URIRef) -> dict:
    """That this property appears only on a node of the right type.

    Expressed as: if the node's @type does not include an acceptable class, the
    property must be absent. A node with no @type at all is left alone, for the
    same reason an untyped value is.
    """
    return {
        "$comment": f"rdfs:domain {term(target)}: where {name} belongs",
        "if": {
            "allOf": [
                {"required": ["@type"]},
                {"not": {"properties": {"@type": {"$ref": CLASS_DEFS + term(target)}}}},
            ]
        },
        "then": {"not": {"required": [name]}},
    }


# Properties whose values are rdf:Lists. @container: @set cannot hold a list
# object, so a term declared that way is skipped by the processor and the property
# comes out as a compact IRI with no container applied -- which looks exactly like
# the declaration not working. These get no container.
LIST_VALUED = {"componentList", "elementList"}

# The properties that are always a bare reference, measured over 200 corpus
# records. @type: @id is only safe here: on a property that sometimes embeds a
# node it produces a mix of strings and objects, which is the same inconsistency
# in a different costume.
#
# Deliberately not hasInstance, despite cbd-01.md listing it among the
# reference-only properties: it is a string 262 times, a node with a URI 61
# times, and a node with *no* URI 39 times. Those 39 are blank nodes that no
# coercion can turn into a reference.
#
# Deliberately not dcterms:isPartOf either -- bluecore-models strips DCTERMS
# before persistence, so it never reaches the stored shape.
ALWAYS_A_REFERENCE = {
    "instanceOf",
    "itemOf",
    "hasItem",
    "electronicLocator",
    "generationProcess",
    "descriptionLevel",
}

# Properties from outside BIBFRAME that appear in the data. The ontology cannot
# supply these, so they are listed -- 27 local names measured across 300 records,
# minus rdf:type/first/rest which are structural rather than properties to
# declare. Adding the BFLC and MADS ontologies as further generator inputs would
# replace this list; until then it is the one hand-maintained part of the context.
FOREIGN = {
    "bflc": [
        "aap",
        "aap-normalized",
        "applicableInstitution",
        "appliesTo",
        "citation",
        "demographicGroup",
        "encodingLevel",
        "governmentPubType",
        "marcKey",
        "movingImageTechnique",
        "nonSortNum",
        "projectedProvisionDate",
        "serialPubType",
        "simpleAgent",
        "simpleDate",
        "simplePlace",
    ],
    "mads": [
        "authoritativeLabel",
        "componentList",
        "elementList",
        "elementValue",
        "isMemberOfMADSScheme",
    ],
    "rdfs": ["label"],
    "rdf": ["value"],
}

NAMESPACES = {
    "bf": BF,
    "bflc": "http://id.loc.gov/ontologies/bflc/",
    "mads": "http://www.loc.gov/mads/rdf/v1#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
}


def build_context(graph: Graph) -> tuple[dict, int]:
    """The JSON-LD context, with cardinality and references pinned.

    Phase 1 steps 1 and 2 of the Blue Core plan, generated rather than
    enumerated by hand. Two declarations do the work.

    @container: @set makes a property an array whether it holds one value or
    five. Without it, compaction collapses a single value to a bare object and a
    consumer has to check the type of everything it touches -- 66 of 134
    properties came out both ways in a 300-record sample. There is no way to set
    a default container in JSON-LD, so every term has to be named, which is why
    this is generated.

    Declaring all 226 properties is safe: BIBFRAME contains zero
    owl:FunctionalProperty declarations and zero cardinality restrictions, so
    nothing in it is single-valued and no declaration here can be wrong about
    that.

    @type: @id writes a reference as a plain URI string rather than an {"@id":
    ...} wrapper, and is applied only to ALWAYS_A_REFERENCE.

    What this deliberately does not decide: whether the term set is closed
    (§1.3), and whether the output uses CURIEs or plain names (§1.3b). The
    existing prefixed style is preserved so those can be settled separately.
    """
    context: dict[str, object] = {
        "@version": 1.1,
        "@vocab": BF,
        **{prefix: uri for prefix, uri in NAMESPACES.items()},
    }

    declared = 0
    for prop in sorted(properties(graph), key=str):
        name = term(prop)
        if name in LIST_VALUED:
            continue
        definition: dict[str, object] = {"@container": "@set"}
        if name in ALWAYS_A_REFERENCE:
            definition["@type"] = "@id"
        context[name] = definition
        declared += 1

    for prefix, names in FOREIGN.items():
        for name in names:
            key = f"{prefix}:{name}"
            definition = {"@id": NAMESPACES[prefix] + name}
            if name not in LIST_VALUED:
                definition["@container"] = "@set"
            context[key] = definition
            declared += 1

    return {"@context": context}, declared


def build(graph: Graph) -> dict:
    closures = subclass_closure(graph)
    value_constraints: dict[str, dict] = {}
    node_constraints: list[dict] = []
    skipped: dict[str, str] = {}
    # every class a constraint names, so each closure is emitted once
    referenced: set[URIRef] = set()

    for prop in sorted(properties(graph), key=str):
        name = term(prop)

        for target in graph.objects(prop, RDFS.range):
            if not isinstance(target, URIRef):
                continue
            if ("range", name) in OVERRIDES:
                skipped[f"range {name}"] = OVERRIDES[("range", name)]
                continue
            value_constraints[name] = range_constraint(target)
            if target != RDFS.Literal:
                referenced.add(target)

        for target in graph.objects(prop, RDFS.domain):
            if not isinstance(target, URIRef):
                continue
            if ("domain", name) in OVERRIDES:
                skipped[f"domain {name}"] = OVERRIDES[("domain", name)]
                continue
            node_constraints.append(domain_constraint(name, target))
            referenced.add(target)

    version = next(graph.objects(ONTOLOGY_IRI, OWL.versionInfo), None)
    issued = next(
        graph.objects(ONTOLOGY_IRI, URIRef("http://purl.org/dc/terms/issued")), None
    )

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://bibframe-json.org/schema/ontology.json",
        "title": "BIBFRAME domains and ranges, read as constraints",
        "$comment": (
            "GENERATED by generate/from_ontology.py. Do not edit.\n\n"
            f"From BIBFRAME {version}, issued {issued}.\n\n"
            "rdfs:domain and rdfs:range are inference rules under OWL and cannot "
            "be violated; this reads them as constraints instead, which is the "
            "flip SHACL makes. Violations are warnings rather than errors: when "
            "the data and the ontology disagree, the ontology is often the one "
            "that is behind."
        ),
        "x-bibframe-version": str(version) if version else None,
        "x-skipped": skipped,
        "type": "object",
        "$ref": "#/$defs/node",
        "$defs": {
            # Named once, referenced many times. See CLASS_DEFS.
            "class": {
                term(target): class_matcher(closures, target)
                for target in sorted(referenced, key=str)
            },
            "literal": {
                "$comment": (
                    "rdfs:range rdfs:Literal: a literal rather than a reference. "
                    "A plain string, number or boolean passes, and so does a value "
                    "object; only a node with an @id fails."
                ),
                "not": {"type": "object", "required": ["@id"]},
            },
            # Every property's values are checked against their range *and*
            # against $defs/node again, so the constraints reach a nested
            # resource at any depth. Without the recursion the schema only looks
            # at the top-level resource's immediate properties and reports
            # nothing at all: the first version did exactly that and scored a
            # perfect 100% against records known to contain violations.
            #
            # Deliberately no "type": "object" here, even though a node is one.
            # The recursion applies to every value, and most values are plain
            # strings; asserting the type would fail every literal in the
            # document. properties and allOf are ignored for a non-object, so a
            # string passes through untouched. The root is required to be an
            # object above, where that is true.
            "node": {
                "properties": {
                    name: {
                        "type": "array",
                        "items": {"allOf": [constraint, {"$ref": "#/$defs/node"}]},
                    }
                    for name, constraint in sorted(value_constraints.items())
                },
                # Recursion has to reach through *every* property, not only the
                # ones with a declared range. Half of BIBFRAME's properties
                # declare neither domain nor range -- bf:subject is one -- and
                # attaching the recursion to `properties` alone means nothing
                # nested under those is ever visited. That was the second way
                # this schema managed to report nothing at all.
                #
                # Guarded on being an array rather than asserting it, because
                # additionalProperties also sees the JSON-LD keywords, and @id is
                # a string. A non-array simply passes.
                "additionalProperties": {
                    "if": {"type": "array"},
                    "then": {"items": {"$ref": "#/$defs/node"}},
                },
                "allOf": node_constraints,
            },
        },
    }


def main() -> None:
    graph = Graph()
    graph.parse(ONTOLOGY)
    schema = build(graph)
    OUTPUT.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")

    context, declared = build_context(graph)
    CONTEXT_OUTPUT.write_text(json.dumps(context, indent=2, ensure_ascii=False) + "\n")

    values = len(schema["$defs"]["node"]["properties"])
    nodes = len(schema["$defs"]["node"]["allOf"])
    print(f"wrote {OUTPUT.relative_to(HERE.parent)}")
    print(f"  BIBFRAME {schema['x-bibframe-version']}")
    print(f"  {values} range constraints, {nodes} domain constraints")
    print(
        f"  {len(schema['x-skipped'])} skipped by OVERRIDES: {', '.join(schema['x-skipped'])}"
    )
    print(f"wrote {CONTEXT_OUTPUT.relative_to(HERE.parent)}")
    print(
        f"  {declared} terms declared; {len(ALWAYS_A_REFERENCE)} as @type: @id; "
        f"{len(LIST_VALUED)} skipped as rdf:List valued"
    )


if __name__ == "__main__":
    main()
