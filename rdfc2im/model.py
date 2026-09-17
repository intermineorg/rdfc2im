"""InterMine model loader.

Loads core.xml + genomic_additions.xml unconditionally, every *_additions.xml
whose path matches a token in the allow list, and (optionally) the live
merged model (humanmine_model.xml) for validation.  Provides an
inheritance-aware term index and integration-key lookup.
"""
from __future__ import annotations
import os
import re
import glob
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from lxml import etree

KEY_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\.key(?:[_.][A-Za-z0-9_]*)?\s*=\s*(.*?)\s*$")


@dataclass
class Field:
    name: str
    kind: str                     # attribute | reference | collection
    type: str                     # java type or referenced class
    term: Optional[str] = None
    reverse: Optional[str] = None
    owner: str = ""               # class where it is declared
    source_file: str = ""


@dataclass
class ClassDesc:
    name: str
    extends: List[str] = field(default_factory=list)
    is_interface: bool = True
    term: Optional[str] = None
    fields: Dict[str, Field] = field(default_factory=dict)
    source_files: List[str] = field(default_factory=list)


class InterMineModel:
    def __init__(self):
        self.classes: Dict[str, ClassDesc] = {}
        self.keys: Dict[str, Dict[str, List[str]]] = {}   # class -> keyname -> fields
        self.term_index: Dict[str, List[Tuple[str, Optional[str]]]] = {}
        self.files: List[str] = []

    # ------------------------------------------------------------------ load
    def load_xml(self, path: str):
        tree = etree.parse(path)
        root = tree.getroot()
        for c in root.iter("class"):
            name = c.get("name")
            cd = self.classes.setdefault(name, ClassDesc(name=name))
            cd.source_files.append(path)
            ext = c.get("extends")
            if ext:
                for e in ext.split():
                    if e not in cd.extends:
                        cd.extends.append(e)
            if c.get("is-interface") == "false":
                cd.is_interface = False
            if c.get("term") and not cd.term:
                cd.term = c.get("term")
            for f in c:
                if not isinstance(f.tag, str):
                    continue
                if f.tag not in ("attribute", "reference", "collection"):
                    continue
                fname = f.get("name")
                typ = f.get("type") if f.tag == "attribute" else f.get("referenced-type")
                fd = cd.fields.get(fname)
                if fd is None:
                    fd = Field(name=fname, kind=f.tag, type=typ, term=f.get("term"),
                               reverse=f.get("reverse-reference"), owner=name, source_file=path)
                    cd.fields[fname] = fd
                else:
                    if f.get("term") and not fd.term:
                        fd.term = f.get("term")
                    if f.get("reverse-reference") and not fd.reverse:
                        fd.reverse = f.get("reverse-reference")
        self.files.append(path)

    def load_keys(self, path: str):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = KEY_RE.match(line)
                if not m:
                    continue
                cls, flds = m.group(1), [x.strip() for x in m.group(2).split(",") if x.strip()]
                keyname = line.split("=")[0].strip().split(".", 1)[1]
                self.keys.setdefault(cls, {})[keyname] = flds

    def finalize(self):
        # implicit InterMineObject root
        for cd in self.classes.values():
            if not cd.extends and cd.name != "InterMineObject":
                cd.extends = ["InterMineObject"]
        self.classes.setdefault("InterMineObject", ClassDesc(name="InterMineObject", extends=[]))
        self.term_index = {}
        for cd in self.classes.values():
            if cd.term:
                for t in cd.term.split(","):
                    self.term_index.setdefault(_norm_term(t), []).append((cd.name, None))
            for fd in cd.fields.values():
                if fd.term:
                    for t in fd.term.split(","):
                        self.term_index.setdefault(_norm_term(t), []).append((cd.name, fd.name))

    # --------------------------------------------------------------- queries
    def ancestors(self, cls: str) -> List[str]:
        """Ancestors, nearest first, excluding cls itself."""
        out, todo, seen = [], list(self.classes.get(cls, ClassDesc(cls)).extends), set()
        while todo:
            a = todo.pop(0)
            if a in seen:
                continue
            seen.add(a)
            out.append(a)
            todo.extend(self.classes.get(a, ClassDesc(a)).extends)
        return out

    def descendants(self, cls: str) -> List[str]:
        return [c.name for c in self.classes.values() if cls in self.ancestors(c.name)]

    def is_a(self, cls: str, anc: str) -> bool:
        return cls == anc or anc in self.ancestors(cls)

    def all_fields(self, cls: str) -> Dict[str, Field]:
        """Own + inherited fields (own wins)."""
        out: Dict[str, Field] = {}
        for a in reversed(self.ancestors(cls)):
            if a in self.classes:
                out.update(self.classes[a].fields)
        if cls in self.classes:
            out.update(self.classes[cls].fields)
        return out

    def field(self, cls: str, fname: str) -> Optional[Field]:
        return self.all_fields(cls).get(fname)

    def has_class(self, cls: str) -> bool:
        return cls in self.classes

    def keys_for(self, cls: str) -> Tuple[str, Dict[str, List[str]]]:
        """Return (class that declares the keys, keys).  Walks up the hierarchy."""
        if cls in self.keys:
            return cls, self.keys[cls]
        for a in self.ancestors(cls):
            if a in self.keys:
                return a, self.keys[a]
        return cls, {}

    def key_attributes(self, cls: str) -> List[str]:
        """Every single-field key attribute cls has, in the same preference order
        key_attribute() uses to commit to just one - for a caller (items.py's in-memory object
        merging) that needs to try each in turn against what one particular row actually has,
        rather than one class-wide choice regardless of which key that row's own source
        populates. Two single-field keys on the same class is real, not an edge case: Gene has
        both key_primaryidentifier and a bare key_secondaryidentifier, and ncbigene/hgnc key on
        the former while ensembl - writing only Gene.secondaryIdentifier, since Ensembl ids are
        never HumanMine's primary Gene key - can only ever populate the latter. Committing to
        "primaryIdentifier" class-wide for ensembl's own rows left every one of them keyed on
        the fallback (a hash of everything the row happens to carry), which any two rows for the
        same real gene disagree on somewhere (multi-valued dcterms:description, a satellite
        table with fewer columns than main, ...) - silently splitting one gene into several
        Gene items instead of merging them, found only once ensembl was actually loaded
        (InterMine's own loader then refuses with "Duplicate objects found for pk
        Gene.key_secondaryidentifier" the moment two of those splintered items, or one of them
        and an already-loaded ncbigene row, turn out to share the same real secondaryIdentifier)."""
        owner, keys = self.keys_for(cls)
        single = [v[0] for v in keys.values() if len(v) == 1]
        ordered = [p for p in ("primaryIdentifier", "identifier", "primaryAccession", "taxonId",
                                "pubMedId", "name", "value", "symbol") if p in single]
        result = ordered + [s for s in single if s not in ordered]
        if result:
            return result
        # No curated key at all - same fallback key_attribute() uses, so a class no keys file
        # declares a key for (e.g. Reactome's Pathway, which project.py can only ever propose a
        # DRAFT key for, generated too late for items.py's own run to see it) still gets a
        # stable single-field key here, not the fragile all-values-hash fallback: found only by
        # an actual load, where two rows for the same real Pathway differing solely in an
        # OPTIONAL biopax:comment (Reactome's own comment field is multi-valued, and RDF Portal
        # binds the query once per value) hashed to two different keys and loaded as two
        # separate, never-merged Pathway items.
        for pref in ("primaryIdentifier", "identifier", "primaryAccession", "name"):
            fd = self.field(cls, pref)
            if fd and fd.kind == "attribute":
                return [pref]
        return []

    def key_attribute(self, cls: str) -> Optional[str]:
        """The single attribute best used to identify an object of cls in a TSV row."""
        owner, keys = self.keys_for(cls)
        # prefer single-field keys, primaryIdentifier first
        single = [v[0] for v in keys.values() if len(v) == 1]
        for pref in ("primaryIdentifier", "identifier", "primaryAccession", "taxonId",
                     "pubMedId", "name", "value", "symbol"):
            if pref in single:
                return pref
        if single:
            return single[0]
        for v in keys.values():          # multi-field key: take first attribute
            for f in v:
                fd = self.field(cls, f)
                if fd and fd.kind == "attribute":
                    return f
        # no keys at all: fall back to common identifiers present on the class
        for pref in ("primaryIdentifier", "identifier", "primaryAccession", "name"):
            fd = self.field(cls, pref)
            if fd and fd.kind == "attribute":
                return pref
        return None

    def find_by_term(self, uri: str) -> List[Tuple[str, Optional[str]]]:
        return self.term_index.get(_norm_term(uri), [])

    def classes_with_field(self, fname: str) -> List[str]:
        return [c for c in self.classes if fname in self.all_fields(c)]

    def concrete_owner(self, cls: str, fname: str) -> Optional[str]:
        """The class to *emit*: cls itself if it has the field (own or inherited)."""
        return cls if fname in self.all_fields(cls) else None


def _norm_term(t: str) -> str:
    t = t.strip()
    t = t.replace("https://", "http://")
    # OBO ids are written both SO:0000704 and SO_0000704
    m = re.match(r"^(http://purl\.obolibrary\.org/obo/)([A-Za-z]+)[:_](\d+)$", t)
    if m:
        t = f"{m.group(1)}{m.group(2)}_{m.group(3)}"
    return t


# ------------------------------------------------------------------ discovery
def find_model_files(model_dirs: List[str], allow_tokens: Optional[List[str]]):
    """Return (xml_files, key_files) to load."""
    xmls, keys = [], []
    for d in model_dirs:
        for p in glob.glob(os.path.join(d, "**", "core.xml"), recursive=True):
            xmls.append(p)
        for p in glob.glob(os.path.join(d, "**", "genomic_additions.xml"), recursive=True):
            xmls.append(p)
        for p in glob.glob(os.path.join(d, "**", "*_additions.xml"), recursive=True):
            if p.endswith("genomic_additions.xml"):
                continue
            if "/test/" in p:
                continue
            if allow_tokens is None or any(tok in p for tok in allow_tokens):
                xmls.append(p)
        for p in glob.glob(os.path.join(d, "**", "*_keys.properties"), recursive=True):
            if "/test/" in p:
                continue
            if allow_tokens is None or any(tok in p for tok in allow_tokens):
                keys.append(p)
    # de-dup byte-identical core.xml copies
    seen, out = set(), []
    for p in xmls:
        with open(p, "rb") as fh:
            h = hash(fh.read())
        if h in seen:
            continue
        seen.add(h)
        out.append(p)
    return out, sorted(set(keys))


def load_model(model_dirs: List[str], allow_file: Optional[str] = None,
               live_model: Optional[str] = None,
               extra_additions: Optional[List[str]] = None) -> "InterMineModel":
    """The model translate, items and check reason about.

    `extra_additions` are approved extensions - curation/extensions_additions.xml - which
    `rdfc2im project` puts into the mine's additions, so they will exist when the data loads.
    They must be in this model too.  They used to be left out, so a mapping onto an approved
    field could not resolve: Reactome's Pathway.organism found no link and its organisms were
    created unlinked, although the field the link needs was approved and would be in the mine.
    """
    tokens = None
    if allow_file:
        with open(allow_file) as fh:
            tokens = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
    m = InterMineModel()
    xmls, keys = find_model_files(model_dirs, tokens)
    for p in xmls:
        m.load_xml(p)
    for p in keys:
        m.load_keys(p)
    for p in extra_additions or []:
        if p and os.path.exists(p):
            m.load_xml(p)
    if live_model:
        m.live = InterMineModel()
        m.live.load_xml(live_model)
        m.live.finalize()
    else:
        m.live = None
    m.finalize()
    return m


def read_allow(path: str) -> List[str]:
    with open(path) as fh:
        return [l.strip() for l in fh if l.strip() and not l.startswith("#")]
