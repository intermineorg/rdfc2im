"""Generate a HumanMine LinkML schema: intermine2linkml.py's translation, with the field terms
taken from humanmine_model.xml because the /service/model?format=json endpoint repeats each
class's term onto every attribute (988/988 attribute terms are wrong in the JSON)."""
from __future__ import annotations
import json
import os
from lxml import etree
import yaml

PREFIXES = {
    "intermine": "https://w3id.org/intermine/", "linkml": "https://w3id.org/linkml/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#", "schema": "http://schema.org/",
    "bibo": "http://purl.org/ontology/bibo/", "NCIT": "http://purl.obolibrary.org/obo/NCIT_",
    "SIO": "http://semanticscience.org/resource/SIO_", "foaf": "http://xmlns.com/foaf/0.1/",
    "OIO": "http://www.geneontology.org/formats/oboInOwl#", "EDAM": "http://edamontology.org/",
    "dc": "http://purl.org/dc/elements/1.1/", "dcterms": "http://purl.org/dc/terms/",
    "RO": "http://purl.obolibrary.org/obo/RO_", "GENO": "http://purl.obolibrary.org/obo/GENO_",
    "ERO": "http://purl.obolibrary.org/obo/ERO_", "uniprot.core": "http://purl.uniprot.org/core/",
    "SO": "http://purl.obolibrary.org/obo/SO_", "ECO": "http://purl.obolibrary.org/obo/ECO_"}
TYPES = {"java.lang.String": "string", "java.lang.Integer": "integer", "int": "integer",
         "java.lang.Double": "double", "java.lang.Boolean": "boolean"}


def gen_linkml(model_json: str, model_xml: str, out_path: str, log=print) -> str:
    if not (model_json and os.path.exists(model_json)):
        raise SystemExit(f"linkml: model_json not found: {model_json} (download /service/model?format=json)")
    d = json.load(open(model_json))["model"]["classes"]
    xterms = {}
    if model_xml and os.path.exists(model_xml):
        for c in etree.parse(model_xml).getroot().iter("class"):
            for f in c:
                if isinstance(f.tag, str) and f.tag in ("attribute", "reference", "collection") and f.get("term"):
                    xterms[(c.get("name"), f.get("name"))] = f.get("term")
    schema = {"id": "https://w3id.org/humanmine", "name": "humanmine", "version": "0.0.1",
              "description": "HumanMine Data Model (rdfc2im linkml: intermine2linkml.py translation, field terms from humanmine_model.xml)",
              "license": "https://creativecommons.org/publicdomain/zero/1.0/", "default_prefix": "humanmine",
              "default_range": "string", "prefixes": dict(PREFIXES, humanmine="https://www.humanmine.org/"),
              "imports": ["linkml:types"], "classes": {}, "slots": {}}
    for cn, c in d.items():
        cd = {"name": cn}
        if c.get("extends"):
            cd["is_a"] = c["extends"][0]
        if c.get("term"):
            cd["class_uri"] = c["term"].strip()
        if c.get("displayName") and c["displayName"] != cn:
            cd["aliases"] = [c["displayName"]]
        cd["slots"], cd["slot_usage"] = [], {}
        for kind, multi in (("attributes", None), ("references", False), ("collections", True)):
            for fn, f in (c.get(kind) or {}).items():
                cd["slots"].append(fn)
                su = {}
                if f.get("displayName") and f["displayName"] != fn:
                    su["aliases"] = [f["displayName"]]
                if kind == "attributes":
                    su["range"] = TYPES.get(f.get("type"), "string")
                else:
                    su["range"] = f.get("referencedType")
                    su["multivalued"] = multi
                t = xterms.get((cn, fn))
                if t:
                    su["slot_uri"] = t
                cd["slot_usage"][fn] = su
                schema["slots"].setdefault(fn, {})
        schema["classes"][cn] = cd
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        yaml.safe_dump(schema, fh, sort_keys=False, allow_unicode=True)
    log(f"linkml: {len(schema['classes'])} classes, {sum(1 for c in schema['classes'].values() if c.get('class_uri'))} class_uri, "
        f"{len(xterms)} slot_uri from the XML -> {out_path}")
    return out_path
