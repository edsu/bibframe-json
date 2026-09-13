"""A predictable, opinionated JSON shape for BIBFRAME.

Two entry points, doing two different things:

    load(record)        parse into a Work, Instance, Hub or Item for reading
    validate(record)    check conformance against the shipped JSON Schemas

See bibframe_json.validate for why those are not the same.
"""

from bibframe_json.models import (
    EDTF,
    XSD_DATE,
    XSD_DATETIME,
    Contribution,
    Hub,
    Identifier,
    Instance,
    Item,
    Node,
    ProvisionActivity,
    Ref,
    Resource,
    Text,
    Title,
    Work,
)
from bibframe_json.validate import (
    DIALECT,
    ONTOLOGY,
    Finding,
    context,
    load,
    schema,
    validate,
)

__all__ = [
    "DIALECT",
    "EDTF",
    "ONTOLOGY",
    "XSD_DATE",
    "XSD_DATETIME",
    "Contribution",
    "Finding",
    "Hub",
    "Identifier",
    "Instance",
    "Item",
    "Node",
    "ProvisionActivity",
    "Ref",
    "Resource",
    "Text",
    "Title",
    "Work",
    "context",
    "load",
    "schema",
    "validate",
]
