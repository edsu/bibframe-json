"""Pydantic models for the shape context/bibframe.jsonld guarantees.

Hand-written, unlike the context and the ontology schema. The division is
deliberate: enumerating 249 `@container: @set` declarations is mechanical and
belongs in a generator, while deciding which properties a template needs and
what to call the helper that finds a display title is editorial. `iiif-prezi3`
is hand-written for the same reason.

These assume the context is adopted. Every property is a list, references are
URI strings, `@type` is a list. Where a record predates that, the validators
below still accept the older shape -- a record restored from a backup will carry
it long after any backfill.

Two things to know before reading further.

**Models are open, not closed.** `extra="allow"` throughout. BIBFRAME has 226
properties and the data uses 136; modelling the dozen that templates care about
and rejecting the rest would make these useless for anything else. An unmodelled
property is reachable through `model_extra`.

**A plain `@property` is invisible to `model_json_schema()`.** So display helpers
are plain properties and stay out of the published schema, while anything meant
to appear in API output would need `@computed_field`. That distinction is what
lets one set of models serve templates and the schema without display logic
leaking into the contract.
"""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Datatypes worth naming, since asking "is this date exact?" is the common case.
XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"
XSD_DATETIME = "http://www.w3.org/2001/XMLSchema#dateTime"
EDTF = "http://id.loc.gov/datatypes/edtf"


class Text(BaseModel):
    """A literal that still knows its language and datatype.

    Flattening a literal to a bare string loses meaning here, not just metadata,
    which is why this is a model rather than a `str` field:

    - `date` carries three datatypes in real records -- xsd:dateTime, xsd:date
      and EDTF. EDTF encodes uncertainty (`199X`, `1970?`, intervals), so
      reading it as an xsd:date is wrong.
    - a language tag is often the only thing telling parallel scripts apart. 101
      properties hold both a romanised and a vernacular form of one value, and
      `bluecore_api` currently renders them comma-joined as though they were two
      different titles.

    `__str__` returns the value, so a template writing `{{ title }}` gets the
    text and needs to know none of this.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    value: str = Field(alias="@value")
    language: str | None = Field(default=None, alias="@language")
    datatype: str | None = Field(default=None, alias="@type")

    @model_validator(mode="before")
    @classmethod
    def accept_a_bare_string(cls, data: Any) -> Any:
        """A plain string is a literal with no language and no datatype.

        Most literals in the data are plain strings -- 14,021 against 771 value
        objects -- so the common case has to be the cheap one.
        """
        if isinstance(data, str):
            return {"@value": data}
        return data

    @model_validator(mode="after")
    def not_both_tags(self) -> "Text":
        """A value object carries at most one of @type or @language.

        Required by JSON-LD and confirmed in the data: 703 with a datatype, 68
        with a language, none with both.

        Note this rule does *not* appear in `model_json_schema()` -- a
        model_validator never does. It has to be written into the dialect schema
        separately, or consumers of that schema will not have it.
        """
        if self.datatype and self.language:
            raise ValueError("@type and @language are mutually exclusive")
        return self

    @property
    def approximate(self) -> bool:
        """Whether this is an EDTF value, and so may be uncertain."""
        return self.datatype == EDTF

    def __str__(self) -> str:
        return self.value


class Ref(BaseModel):
    """Something the record points at rather than describes.

    Blue Core keeps a referenced resource's description in its own row, so what
    remains here is usually just a URI -- 5,245 of 8,875 node values in a
    200-record sample. `label` is present when the record happens to carry one.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    uri: str | None = Field(default=None, alias="@id")
    types: list[str] = Field(default_factory=list, alias="@type")
    label: list[Text] = Field(default_factory=list, alias="rdfs:label")

    @model_validator(mode="before")
    @classmethod
    def accept_a_bare_uri(cls, data: Any) -> Any:
        """`@type: @id` in the context writes a reference as a plain string."""
        if isinstance(data, str):
            return {"@id": data}
        return data

    @model_validator(mode="before")
    @classmethod
    def a_scalar_type_is_still_allowed(cls, data: Any) -> Any:
        """@type is a list under the current context, a string before it."""
        if isinstance(data, dict) and isinstance(data.get("@type"), str):
            data = {**data, "@type": [data["@type"]]}
        return data

    def __str__(self) -> str:
        if self.label:
            return str(self.label[0])
        return self.uri or ""


# A value is a literal, a reference, or a nested node. Declared as a union of
# Text and Ref because those cover what templates read; a nested node with its
# own properties still parses as a Ref, with the rest reachable via model_extra.
Value = Annotated[Text | Ref, Field(union_mode="left_to_right")]


class Node(BaseModel):
    """Anything with a @type and properties: the shape all of these share."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    uri: str | None = Field(default=None, alias="@id")
    types: list[str] = Field(default_factory=list, alias="@type")

    @model_validator(mode="before")
    @classmethod
    def normalise_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("@type"), str):
            data = {**data, "@type": [data["@type"]]}
        return data

    def get(self, name: str) -> list[Any]:
        """An unmodelled property, always as a list.

        The escape hatch for the 120-odd properties not given fields here. The
        context guarantees a list; this tolerates a scalar for records written
        before it.
        """
        value = (self.model_extra or {}).get(name)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


class Title(Node):
    main: list[Text] = Field(default_factory=list, alias="mainTitle")
    subtitle: list[Text] = Field(default_factory=list)
    part_number: list[Text] = Field(default_factory=list, alias="partNumber")
    part_name: list[Text] = Field(default_factory=list, alias="partName")

    def __str__(self) -> str:
        parts = [str(self.main[0])] if self.main else []
        if self.subtitle:
            parts.append(str(self.subtitle[0]))
        return ": ".join(parts)


class Contribution(Node):
    agent: list[Ref] = Field(default_factory=list)
    role: list[Ref] = Field(default_factory=list)

    @property
    def primary(self) -> bool:
        """Whether this is the primary contribution, by its extra type."""
        return any(t.endswith("PrimaryContribution") for t in self.types)


class Identifier(Node):
    value: list[Text] = Field(default_factory=list, alias="rdf:value")
    qualifier: list[Text] = Field(default_factory=list)
    status: list[Ref] = Field(default_factory=list)

    @property
    def kind(self) -> str:
        """Isbn, Lccn, Local -- the first type that is not Identifier itself."""
        for t in self.types:
            if t != "Identifier":
                return t
        return "Identifier"


class ProvisionActivity(Node):
    place: list[Ref] = Field(default_factory=list)
    date: list[Text] = Field(default_factory=list)
    simple_date: list[Text] = Field(default_factory=list, alias="bflc:simpleDate")
    simple_place: list[Text] = Field(default_factory=list, alias="bflc:simplePlace")


class Resource(Node):
    """What a Work, Instance, Hub and Item have in common.

    Sharing a base is what lets one set of template partials serve all four --
    `{% include "partials/titles.html" %}` regardless of resource type.
    """

    title: list[Title] = Field(default_factory=list)
    note: list[Ref] = Field(default_factory=list)
    subject: list[Ref] = Field(default_factory=list)
    identified_by: list[Identifier] = Field(default_factory=list, alias="identifiedBy")
    admin_metadata: list[Node] = Field(default_factory=list, alias="adminMetadata")
    contribution: list[Contribution] = Field(default_factory=list)

    @property
    def main_title(self) -> Text | None:
        """The display title: the first bf:Title's mainTitle.

        `work.title[0].main[0]` with a check at each step is what a template
        would otherwise have to write, and every consumer would write it again.
        Prefers a Title over a VariantTitle or KeyTitle.
        """
        for title in self.title:
            if "Title" in title.types and title.main:
                return title.main[0]
        for title in self.title:
            if title.main:
                return title.main[0]
        return None

    def titles_in(self, language: str | None) -> list[Text]:
        """Titles in one language, or untagged ones when language is None.

        The operation `scalar()` in bluecore_api currently answers by joining
        every form with a comma, so a record with romanised and Cyrillic titles
        renders as both at once.
        """
        return [
            main
            for title in self.title
            for main in title.main
            if main.language == language
        ]

    @property
    def primary_contributions(self) -> list[Contribution]:
        return [c for c in self.contribution if c.primary]


class Work(Resource):
    instance_of: list[str] = Field(default_factory=list, alias="instanceOf")
    has_instance: list[Ref] = Field(default_factory=list, alias="hasInstance")
    classification: list[Node] = Field(default_factory=list)
    language: list[Ref] = Field(default_factory=list)
    genre_form: list[Ref] = Field(default_factory=list, alias="genreForm")


class Instance(Resource):
    instance_of: list[str] = Field(default_factory=list, alias="instanceOf")
    provision_activity: list[ProvisionActivity] = Field(
        default_factory=list, alias="provisionActivity"
    )
    extent: list[Ref] = Field(default_factory=list)
    publication_statement: list[Text] = Field(
        default_factory=list, alias="publicationStatement"
    )


class Hub(Resource):
    expression_of: list[Ref] = Field(default_factory=list, alias="expressionOf")


class Item(Resource):
    item_of: list[str] = Field(default_factory=list, alias="itemOf")
