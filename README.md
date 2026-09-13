# bibframe-json

Generates two artifacts from the BIBFRAME ontology: a JSON-LD context that pins
the shape of Blue Core's JSON, and JSON Schema constraints derived from
BIBFRAME's own domains and ranges.

**Status: a proposal, not a dependency.** Nothing in Blue Core uses this. Whether
the approach is worth adopting is a team decision, so the point of this repo is
to make the case checkable rather than to be imported. There is no library here —
no readers, no accessors, no `validate()`.

```
uv run python generate/from_ontology.py    # regenerate both artifacts
uv run pytest                              # 17 tests, no corpus or network
```

    wrote schema/ontology.json
      BIBFRAME 3.0.1
      150 range constraints, 110 domain constraints
      4 skipped by OVERRIDES
    wrote context/bibframe.jsonld
      249 terms declared; 6 as @type: @id; 2 skipped as rdf:List valued

## The context: why generate it

Phase 1 of `blue-core-lod/cbd/bluecore-json-shape-plan.md` calls for declaring
`@container: @set` on every repeatable property, because compaction otherwise
collapses a single value to a bare object and a consumer has to check the type of
everything it touches — 66 of 134 properties came out both ways in a 300-record
sample. JSON-LD has no way to set a default container, so every term has to be
named, which is exactly the kind of enumeration a generator should do.

Declaring all of them is safe, and that is a measurement rather than a guess:
BIBFRAME contains **zero** `owl:FunctionalProperty` declarations and **zero**
cardinality restrictions across 226 properties, so nothing in it is
single-valued.

`@type: @id` is applied to only six properties — `instanceOf`, `itemOf`,
`hasItem`, `electronicLocator`, `generationProcess`, `descriptionLevel` — because
those are the ones measured as *always* a bare reference. Not `hasInstance`,
despite `cbd-01.md` listing it: it is a string 262 times, a node with a URI 61
times, and a node with no URI 39 times. Coercing a property that sometimes embeds
produces a mix of strings and objects, which is the same inconsistency in
different clothing.

**Framing 150 real resources with this context and no code at all:** 0 triples
changed, 0 keys left as a full URI, and one property left as a scalar —
`mads:componentList`.

That exception is structural, and all three escapes are closed:
`@container: @set` cannot hold a list object, so the processor skips the term;
`@container: ["@list", "@set"]` says what is meant and pyld cannot expand it; and
`@container: "@list"` **loses data** — a property holding two lists compacts to
one, 6 triples becoming 3, silently. Real records do hold two, so that option is
out. `componentList` and `elementList` stay undeclared.

So a context gets 247 of 249 terms, and the residue is two list-valued properties
plus `@type`, which is a keyword no container can reach.

## The schema: why the ontology has to be read backwards

`rdfs:domain` and `rdfs:range` under OWL are **inference rules, not
constraints**. Asserting that `bf:title` has domain `bf:Work` does not make a
non-Work invalid — it *infers* that the subject is a Work. They cannot be
violated even in principle. So "validating against the BIBFRAME ontology" as OWL
is a category error rather than a weak check.

SHACL's move is to read the same vocabulary the other way, as closed-world
constraints. This does that, and emits the result as JSON Schema:

    rdfs:range R   ->  a value of this property, if it says what it is, is typed
                       R or one of R's subclasses
    rdfs:domain D  ->  this property appears only on a node typed D or one of
                       D's subclasses

Three properties of the output are worth knowing.

**It is pure JSON Schema.** Any JSON Schema library in any language can use
`schema/ontology.json` with no code from here. That portability is the best thing
about the approach, and it is what rules out expressing these as Pydantic
validators — a `model_validator` does not appear in `model_json_schema()` at all,
so the rule would be invisible to everyone using the published schema.

**Subclass hierarchies are flattened at generation time.** Acceptable types are
enumerated per property, so nothing walks a class tree to validate a document.
`bf:title` accepts seven types and the schema lists all seven.

**An untyped value is unknown, not wrong.** A reference the document only points
at, carrying no type of its own, is not checkable, and calling it a violation
would be reporting the validator's own ignorance.

## How much it actually catches

The part to read carefully, because the answer depends entirely on which artifact
you point it at.

**Against a full BIBFRAME graph** — a Concise Bounded Description from
id.loc.gov, say — it has real purchase: 31,292 checkable assertions across 200
records, **99.8% conforming**.

**Against Blue Core's stored per-resource JSON it finds nothing at all**: 0
findings in 200 resources. Not because that data is clean.

| Value shape in stored resources | Count |
| --- | --- |
| literal (plain string) | 14,021 |
| **bare reference, no `@type`** | **5,245** |
| node with `@type` (checkable) | 3,501 |
| literal (value object) | 771 |

Only **39% of node values carry a `@type`** a range constraint can check. Blue
Core keeps a referenced resource's description in its own row, so what remains in
the referring record is a bare `{"@id": ...}`. The 31 genuine violations in that
corpus — `bf:source` pointing at a `mads:Authority` — live in the source graph
and are absent from the stored slice.

So point this at source graphs. Over stored per-resource JSON what survives is
domain constraints, since a node's own `@type` is always present, and literal
ranges, which need no target type — thin, but not nothing.

## The four exclusions

`OVERRIDES` in the generator skips four constraints and records in the output
what was skipped and why. Each is a property where **every** real use violates
the flipped constraint, which makes the ontology the likelier culprit:

| Property | Ontology says | Every real use |
| --- | --- | --- |
| `bf:relief` | domain `bf:Instance` | `bf:Cartographic`, a Work |
| `bf:mediumComponent` | domain `bf:Work` | `bf:Ensemble` |
| `bf:ensembleSize` | domain `bf:Work` | `bf:Ensemble` |
| `bf:mediumOfPerformance` | range `bf:MediumOfPerformance` | `mads:Medium` |

Music and cartographic modelling the ontology has not caught up with, plus MADS
classes it does not reference. A useful side effect of the flip is that it finds
ontology bugs as readily as data bugs, and `bf:relief` looks worth reporting
upstream.

## Regenerating

`generate/bibframe.rdf` is vendored — BIBFRAME 3.0.1, issued 2025-12-03 — so
regenerating against a new release is a reviewable diff rather than a surprise.
The generated file records which version produced it.

`rdflib` is a dev dependency only — the ontology is read at build time, and
nothing at runtime parses RDF. The committed artifacts are plain JSON.

## Checking the claims

`uv run pytest` covers what can be checked without the corpus: that the schema is
valid, that the four exclusions are recorded with reasons, that the flip behaves
correctly across nine cases, and that the context declares what it says it does.

Two of those tests exist because this schema twice reported *nothing at all*
while looking like it worked — first with no recursion, then with recursion
reaching only properties that have a declared range. A schema that silently
checks nothing scores a perfect 100%.

The corpus measurements need the Blue Core ingest archive and are not in the test
suite. To reproduce them:

```
uv run --with-editable ../bluecore-models --with rdflib python - <<'EOF'
# frame entity graphs from uploads/batch_00001.tar.gz with context/bibframe.jsonld
# and count: triples changed, scalars remaining, full-URI keys
EOF
```

## Where this is going

Unsettled. This began as a standalone library for reading and validating Blue
Core's stored JSON shape, and two things changed that.

The generator turned out to have little purchase on the stored shape — the 39%
above — and belongs over source graphs instead, which is the SHACL slot in the
Blue Core plan rather than a JSON Schema library.

And that plan, `blue-core-lod/cbd/bluecore-json-shape-plan.md`, already covers
the JSON side more thoroughly: context-first `@container: @set`, Pydantic as
source of truth, JSON Schema over framed output, grounded in how Linked Art and
IIIF solved the same problems. `blue-core-lod/cbd/json-shape-amendments.md`
records what measurement changed about it.

So the flip mechanism and the `OVERRIDES` list are the parts worth keeping,
wherever they end up living. Whether that is this repo is an open question.

## Caveat on the numbers

Everything measured here comes from the first few hundred records of
`../bluecore-models/uploads/batch_00001.tar.gz` in tar order — at most 500 of
roughly 350,000, one of 35 batches, and not verified to be an unstratified
sample. Worth re-running across batches before treating any figure as settled.
