"""The mapping: rdf-config subjects/predicates -> InterMine classes/attributes.

Two editable files per source live in out/<src>/:

  mapping_subjects.tsv            one row per rdf-config subject (incl. inline blank nodes)
  mapping_predicates.sssom.tsv    one row per (subject, predicate, column), SSSOM layout (see sssom.py)

Both are regenerated on every run and 3-way merged against a hidden snapshot of the last
auto output (.mapping_subjects.base.tsv / .mapping_predicates.base.sssom.tsv): a cell the human
changed is kept, a cell the human left alone takes the fresh value.

Statuses (`status` / ext_status):
  sure   backed by uploaded evidence (term URI, Java converter, .properties, exact name)
  guess  proposal from general knowledge - review
  todo   unresolved - a human must decide
  human  set or changed by the human (protected)
  drop   deliberately not loaded
  link   structural row (object is another subject / blank node); nothing to load itself
"""
from __future__ import annotations
import csv
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

import yaml

from .model import InterMineModel
from .rdfconfig import RdfConfig, Subject, Predicate, ObjectVar, load_config
from .sssom import read_sssom, write_sssom

HERE = os.path.dirname(__file__)

CW_COLUMNS = ["table", "subject", "predicate", "column", "im_class", "im_field", "status",
              "basis", "transform", "filter", "value", "required", "via", "kind", "multi",
              "example", "note"]
CW_EDITABLE = ["table", "im_class", "im_field", "status", "transform", "filter", "value",
               "required", "note"]
SUBJ_COLUMNS = ["subject", "im_class", "role", "status", "basis", "rdf_types", "path",
                "example", "note"]
SUBJ_EDITABLE = ["im_class", "role", "status", "note"]

LOADABLE = ("sure", "guess", "human")


@dataclass
class Row:
    table: str = ""
    subject: str = ""
    predicate: str = ""
    column: str = ""
    im_class: str = ""
    im_field: str = ""
    status: str = "todo"
    basis: str = ""
    transform: str = ""
    filter: str = ""
    value: str = ""
    required: str = "no"
    via: str = ""
    kind: str = "literal"
    multi: str = "no"
    example: str = ""
    note: str = ""

    def key(self):
        return (self.subject, self.predicate, self.column)

    @property
    def im(self):
        return f"{self.im_class}.{self.im_field}" if self.im_class and self.im_field else ""


@dataclass
class Node:
    subject: Subject
    var: str                           # SPARQL variable
    display: str                       # name used in the TSV `subject` column
    parent: Optional["Node"] = None
    pred: Optional[Predicate] = None   # predicate from parent
    objvar: str = ""                   # rdf-config object variable naming this node
    union: bool = False                # reached through a [A, B, C] object
    im_class: str = ""
    status: str = ""
    root: Optional["Node"] = None
    children: List["Node"] = field(default_factory=list)

    @property
    def multi_path(self) -> bool:
        n = self
        while n.parent is not None:
            if n.pred is not None and n.pred.multi:
                return True
            n = n.parent
        return False

    def path_str(self) -> str:
        parts = []
        n = self
        while n.parent is not None:
            parts.append(f"{n.pred.curie}{n.pred.cardinality}")
            n = n.parent
        return " / ".join(reversed(parts)) if parts else "(root)"


# ----------------------------------------------------------------------- knowledge
class Knowledge:
    def __init__(self, path: Optional[str] = None):
        path = path or os.path.join(HERE, "data", "knowledge.yaml")
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        self.prefixes: Dict[str, str] = data.get("prefixes") or {}
        self.subjects = data.get("subjects") or []
        self.predicates = data.get("predicates") or []

    def expand(self, curie: str, cfg: RdfConfig) -> str:
        """Expand a knowledge-table CURIE: the source's own prefix.yaml wins, then ours."""
        if not curie:
            return ""
        u = cfg.expand(curie)
        if u.startswith("http"):
            return u
        if ":" in curie:
            p, loc = curie.split(":", 1)
            if p in self.prefixes:
                return self.prefixes[p] + loc
        return curie


def _norm(u: str) -> str:
    u = u.replace("https://", "http://")
    m = re.match(r"^(http://purl\.obolibrary\.org/obo/)([A-Za-z]+)[:_](\d+)$", u)
    return f"{m.group(1)}{m.group(2)}_{m.group(3)}" if m else u


# ------------------------------------------------------------------------ builder
class CrosswalkBuilder:
    def __init__(self, model: InterMineModel, cfg: RdfConfig, knowledge: Knowledge,
                 source_cfg: Optional[dict] = None):
        self.model = model
        self.cfg = cfg
        self.kn = knowledge
        self.src = cfg.name
        self.scfg = source_cfg or {}
        self.nodes: Dict[str, Node] = {}       # display -> node
        self.roots: List[Node] = []
        self.rows: List[Row] = []
        self.subject_rows: List[dict] = []
        self.messages: List[str] = []

    # ---------------------------------------------------------- subjects
    def bind_subject(self, s: Subject, display: str) -> Tuple[str, str, str, str]:
        """Return (im_class, status, basis, note)."""
        types = [_norm(self.cfg.expand(t)) for t in s.types]
        # 1. source-specific knowledge by subject name
        for e in self.kn.subjects:
            if e.get("source") == self.src and e.get("subject") == s.name:
                return e.get("im_class", ""), e.get("status", "guess"), e.get("basis", "knowledge"), e.get("note", "")
        # 2. term URI on the class
        for t in types:
            for cls, fld in self.model.find_by_term(t):
                if fld is None:
                    return cls, "sure", "term", f"type {t} = term of {cls}"
        # 3. knowledge by rdf type (source-agnostic)
        for e in self.kn.subjects:
            if e.get("source") and e.get("source") != self.src:
                continue
            if "type" in e and _norm(self.kn.expand(e["type"], self.cfg)) in types:
                return e.get("im_class", ""), e.get("status", "guess"), e.get("basis", "knowledge"), e.get("note", "")
        # 4. subject name equals a model class name
        if self.model.has_class(s.name):
            return s.name, "sure", "name", f"subject name is the model class {s.name}"
        # 5. inline blank nodes inherit the parent's class (flattened) unless typed
        return "", "todo", "", "no term/knowledge/name match - bind by hand or set role=skip"

    def subject_role(self, s: Subject) -> str:
        """The `role` a knowledge entry gives this subject, or "".

        Follows bind_subject's knowledge precedence (source-specific by name, then by rdf
        type).  It sits *below* the on-disk mapping_subjects.tsv: a role the human set there
        always wins.  This lets a decision such as "skip MeSH TreeNumber" live in
        knowledge.yaml with its evidence, instead of being written into the TSV where it
        would have to be marked `human`.
        """
        for e in self.kn.subjects:
            if e.get("source") == self.src and e.get("subject") == s.name:
                return e.get("role", "")
        types = [_norm(self.cfg.expand(t)) for t in s.types]
        for e in self.kn.subjects:
            if e.get("source") and e.get("source") != self.src:
                continue
            if "type" in e and _norm(self.kn.expand(e["type"], self.cfg)) in types:
                return e.get("role", "")
        return ""

    # ---------------------------------------------------------- traversal
    def build(self, subject_overrides: Dict[str, dict]):
        subjects = self.cfg.subjects
        if not subjects:
            self.messages.append("model.yaml has no subjects")
            return
        roles = {name: o.get("role", "") for name, o in subject_overrides.items()}
        # roots: explicit role=root overrides; else source config roots; else first subject
        root_names = [n for n, r in roles.items() if r == "root"]
        if not root_names:
            root_names = list(self.scfg.get("roots") or [subjects[0].name])
        for rn in root_names:
            s = self.cfg.subject(rn)
            if s is None:
                self.messages.append(f"root subject {rn} not in model.yaml")
                continue
            node = Node(subject=s, var=_var(s.name), display=s.name, objvar=s.name)
            node.root = node
            self._add_node(node, subject_overrides)
            self.roots.append(node)
            self._walk(node, subject_overrides)
        # the subject IRI itself as a column (UniProt accessions, GO ids live in the IRI)
        for node in list(self.nodes.values()):
            if node.subject.blank:
                continue
            self.rows.append(self._self_row(node))
        # unreachable named subjects: record with role=skip so the human can promote them
        for s in subjects:
            if s.name not in self.nodes:
                cls, st, basis, note = self.bind_subject(s, s.name)
                o = subject_overrides.get(s.name, {})
                self.subject_rows.append(dict(
                    subject=s.name, im_class=o.get("im_class", cls),
                    role=o.get("role") or self.subject_role(s) or "skip",
                    status=o.get("status", st), basis=basis, rdf_types=" ".join(s.types),
                    path="(unreachable from a root)", example=s.example, note=o.get("note", note)))
        self._assign_tables()

    def _add_node(self, node: Node, overrides: Dict[str, dict]):
        cls, st, basis, note = self.bind_subject(node.subject, node.display)
        if node.subject.blank and not cls and node.parent is not None:
            cls, st, basis = node.parent.im_class, node.parent.status, "inherit:parent"
            note = "inline blank node - flattened onto the parent's class"
        o = overrides.get(node.display, {})
        role = o.get("role") or self.subject_role(node.subject) or ("root" if node.parent is None else "node")
        node.im_class = o.get("im_class", cls)
        node.status = o.get("status", st)
        self.nodes[node.display] = node
        self.subject_rows.append(dict(
            subject=node.display, im_class=node.im_class, role=role, status=node.status,
            basis=basis, rdf_types=" ".join(node.subject.types), path=node.path_str(),
            example=node.subject.example, note=o.get("note", note)))

    def _walk(self, root: Node, overrides: Dict[str, dict]):
        """Breadth-first, so every subject is attached to its *shortest* path from the root."""
        queue = [root]
        while queue:
            node = queue.pop(0)
            s = node.subject
            for p in s.predicates:
                for o in p.objects:
                    if o.inline is not None:
                        child = Node(subject=o.inline, var=_uniq_var(self, f"{node.var}_{self.cfg.local_name(p.curie)}"),
                                     display=f"{node.display}/{self.cfg.local_name(p.curie)}",
                                     parent=node, pred=p, objvar=self.cfg.local_name(p.curie), root=node.root)
                        if child.display in self.nodes:
                            child.display += "#" + child.var
                        self._add_node(child, overrides)
                        node.children.append(child)
                        self.rows.append(self._row(node, p, o, kind="bnode"))
                        queue.append(child)
                    elif o.sub_subjects:
                        union = len(o.sub_subjects) > 1
                        placed = 0
                        for sn in o.sub_subjects:
                            sub = self.cfg.subject(sn)
                            if sub is None:
                                continue
                            if sn in self.nodes:
                                if self.nodes[sn] is node or self.nodes[sn] in _ancestors(node):
                                    self.messages.append(
                                        f"{node.display} {p.curie} {sn} is a cycle; kept as a column, not expanded")
                                elif self.nodes[sn].parent is not node:
                                    self.messages.append(f"{sn} is reachable by more than one path; using {self.nodes[sn].path_str()}")
                                continue
                            child = Node(subject=sub, var=_uniq_var(self, o.name if not union else f"{o.name}_{_var(sn)}"),
                                         display=sn, parent=node, pred=p, objvar=o.name, union=union, root=node.root)
                            self._add_node(child, overrides)
                            node.children.append(child)
                            queue.append(child)
                            placed += 1
                        # Every target subject was already placed elsewhere in the tree - a back-edge,
                        # and for MP/Uberon's `rdfs:subClassOf*: - subclass_of: Class` a self-edge.  We
                        # cannot recurse (that is the cycle) and there is no child node to bind the
                        # variable to, so a `link` row here would dangle and be silently dropped when
                        # the query is built.  The object is still just an IRI of a known class, which
                        # is exactly what GO and HPO get from the `superclass: obo:GO_0044237` spelling
                        # of the same edge, so emit an ordinary column and let the predicate knowledge
                        # bind it (rdfs:subClassOf -> OntologyTerm.parents).
                        self.rows.append(self._row(node, p, o, kind="link" if placed else "iri"))
                    else:
                        self.rows.append(self._row(node, p, o, kind="iri" if not o.is_literal else "literal"))

    # ---------------------------------------------------------- mapping
    def _self_row(self, node: Node) -> Row:
        r = Row(subject=node.display, predicate="-self-", column=node.var, kind="iri", multi="no",
                example=node.subject.example, basis="subject-iri")
        r._node = node
        cls = node.im_class
        hit = None
        for e in self.kn.predicates:
            if e.get("pred") == "-self-" and e.get("source") in (None, self.src) and \
                    (e.get("subject") in (None, node.subject.name)) and \
                    (not e.get("class") or (cls and self.model.is_a(cls, e["class"]))):
                if e.get("source") is None and e.get("subject") is None and not e.get("class"):
                    continue
                hit = e
                break
        if hit:
            r.status, r.basis = hit.get("status", "guess"), hit.get("basis", "knowledge")
            r.transform, r.note = hit.get("transform", "iri_localname"), hit.get("note", "")
            r.required = str(hit.get("required", "no"))
            if hit.get("im"):
                r.im_class, r.im_field = hit["im"].split(".", 1)
            return r
        ka = self.model.key_attribute(cls) if cls else None
        key_covered = any(x.im_class == cls and x.im_field == ka and x._node.root is node.root for x in self.rows)
        if node.parent is None and cls and ka and not key_covered:
            r.im_class, r.im_field, r.status = cls, ka, "guess"
            r.transform, r.required = "iri_localname", "yes"
            r.note = f"no predicate carries the {cls} key; using the local name of the subject IRI"
        else:
            r.status, r.note = "drop", "the subject's own IRI; map it (with a transform) if it is an identifier"
        return r

    def _row(self, node: Node, p: Predicate, o: ObjectVar, kind: str) -> Row:
        r = Row(subject=node.display, predicate=p.curie, column=o.name, kind=kind,
                multi="yes" if p.multi else "no",
                example=_ex(o.example) if kind != "bnode" else "[]")
        r._node = node  # not serialised
        cls = node.im_class
        pred_uri = _norm(self.cfg.expand(p.curie))
        if not pred_uri.startswith("http"):
            r.note = f"prefix of {p.curie} not in prefix.yaml"
        if kind in ("link", "bnode"):
            r.status, r.basis = "link", "structure"
            seen = []
            for sn in (o.sub_subjects if kind == "link" else []):
                c = self.nodes[sn].im_class if sn in self.nodes else ""
                if c and c not in seen:
                    seen.append(c)
            r.im_class = ", ".join(seen)
        hit = self._knowledge(node, p, o, source_specific=True)
        if hit is None and cls and kind != "bnode":
            hit = self._term(node, p, pred_uri, strict=True)
        if hit is None and cls and kind != "bnode":
            hit = self._knowledge(node, p, o, source_specific=False)
        if hit is None and cls and kind != "bnode":
            hit = self._term(node, p, pred_uri, strict=False)
        if hit is None and cls and kind not in ("link", "bnode"):
            hit = self._name(node, p, o)
        if hit:
            for k, v in hit.items():
                setattr(r, k, v)
        elif kind in ("literal", "iri"):
            if not cls:
                r.status, r.note = "todo", (r.note + "; " if r.note else "") + "subject not bound to a class (see subjects.tsv)"
            else:
                r.status, r.basis = "todo", ""
                r.note = (r.note + "; " if r.note else "") + f"no match on {cls} - choose a field, or set status=drop"
        # required: key attribute of the root class on the root node
        if r.im and node.parent is None and r.required != "yes":
            if r.im_field == self.model.key_attribute(r.im_class) and r.im_class == node.im_class:
                r.required = "yes"
        # new-field detection
        if r.im and r.status in LOADABLE:
            fd = self.model.field(r.im_class, r.im_field)
            if fd is None:
                r.basis = (r.basis + "+" if r.basis else "") + "new-field"
            elif fd.kind != "attribute":
                # expand reference/collection to the target's key attribute
                tgt = fd.type
                ka = self.model.key_attribute(tgt)
                r.via = f"{r.im_class}.{r.im_field}"
                if ka:
                    r.im_class, r.im_field = tgt, ka
                elif r.kind == "link":
                    r.im_class, r.im_field, r.status = tgt, "", "link"
                    r.note = f"{r.via} points at {tgt}, which has no key attribute; the sub-node's own rows carry the data"
                else:
                    r.im_class, r.im_field, r.status = tgt, "", "todo"
                    r.note = f"{r.via} points at {tgt}, which has no key attribute to carry in a column"
        return r

    def _knowledge(self, node: Node, p: Predicate, o: ObjectVar, source_specific: bool):
        pred_uri = _norm(self.cfg.expand(p.curie))
        for e in self.kn.predicates:
            if source_specific:
                if e.get("source") != self.src:
                    continue
            elif e.get("source"):
                continue
            euri = _norm(self.kn.expand(e["pred"], self.cfg)) if e.get("pred") else ""
            if euri != pred_uri:
                continue
            if e.get("subject") and e["subject"] != node.subject.name:
                continue
            if e.get("column") and e["column"] != o.name:
                continue
            if e.get("class") and not (node.im_class and self.model.is_a(node.im_class, e["class"])):
                continue
            im = e.get("im") or ""
            hit = {"status": e.get("status", "guess"), "basis": e.get("basis", "knowledge"),
                   "note": e.get("note", ""), "transform": e.get("transform", ""),
                   "filter": e.get("filter", ""), "value": str(e.get("value", "") or ""),
                   "required": str(e.get("required", "no"))}
            if e.get("table"):
                hit["table"] = e["table"]
            if im:
                hit["im_class"], hit["im_field"] = im.split(".", 1)
                if node.im_class and self.model.is_a(node.im_class, hit["im_class"]):
                    hit["im_class"] = node.im_class          # concrete class of the subject
                elif hit["im_class"] != node.im_class and node.im_class:
                    hit["via"] = self._via(node.im_class, hit["im_class"])
            if e.get("const"):
                hit["_const"] = e["const"]
            if e.get("const_via"):
                hit["_const_via"] = e["const_via"]
            return hit
        return None

    def _term(self, node: Node, p: Predicate, pred_uri: str, strict: bool = True):
        cls = node.im_class
        cands = self.model.find_by_term(pred_uri)
        best = None
        for c, f in cands:
            if f is None:
                continue
            if self.model.is_a(cls, c):
                best = (c, f)
                break
        if best is None and strict:
            return None
        if best is None:
            # term defined on an unrelated class but the field name also exists on cls
            for c, f in cands:
                if f and self.model.field(cls, f):
                    return {"im_class": cls, "im_field": f, "status": "guess", "basis": "term-on-other-class",
                            "note": f"term {pred_uri.split('/')[-1]} is declared on {c}.{f}; {cls} has a field of the same name"}
            return None
        c, f = best
        return {"im_class": cls, "im_field": f, "status": "sure", "basis": "term",
                "note": f"term match: {c}.{f}" if c != cls else ""}

    def _name(self, node: Node, p: Predicate, o: ObjectVar):
        cls = node.im_class
        fields = self.model.all_fields(cls)
        cands = {_flat(o.name), _flat(self.cfg.local_name(p.curie))}
        for fname, fd in fields.items():
            if fd.kind == "attribute" and _flat(fname) in cands:
                return {"im_class": cls, "im_field": fname, "status": "sure", "basis": "name",
                        "note": f"field name matches ({fname})"}
        return None

    def _via(self, from_cls: str, to_cls: str) -> str:
        """Find the reference/collection on from_cls (or its ancestors) that points at to_cls."""
        for fname, fd in self.model.all_fields(from_cls).items():
            if fd.kind != "attribute" and (fd.type == to_cls or self.model.is_a(to_cls, fd.type)):
                return f"{from_cls}.{fname}"
        return ""

    # ---------------------------------------------------------- tables
    def _assign_tables(self):
        multi_root = len(self.roots) > 1
        pruned = self._pruned_nodes()
        auto = set()
        for r in self.rows:
            node = r._node
            root_tbl = "main" if not multi_root else _var(node.root.subject.name).lower()
            if node.display in pruned:
                r.table = "drop"
                continue
            if r.table:
                continue
            step = None
            n, own = node, r
            if r.multi == "yes" and not (r.im_class == node.im_class and r.kind in ("literal", "iri")):
                step = r.column
            else:
                while n.parent is not None:
                    if n.pred is not None and n.pred.multi:
                        step = n.objvar if not n.union else f"{n.objvar}_{_var(n.subject.name).lower()}"
                    n = n.parent
            r.table = root_tbl if step is None else f"{root_tbl}_{step}"
            auto.add(id(r))
        self._split_collisions(auto)
        # knowledge constants -> extra rows in the same table
        extra = []
        for r in self.rows:
            c = getattr(r, "_const", None)
            if c:
                # `const_via` names the link a constant hangs off.  Without it a constant can only
                # attach to the table's root: HGNC's DataSource.name for an xref belongs on
                # CrossReference.source, not the Gene, and GWAS's organism on SNP and Gene, not
                # GWASResult - with no declared link, items.py created them unlinked.
                vias = getattr(r, "_const_via", None) or {}
                for im, val in c.items():
                    cls, fld = im.split(".", 1)
                    via = vias.get(im, "")
                    # the column name is part of the merge key, so two constants for one field
                    # hanging off different links must not share it
                    col = f"const_{cls}_{fld}" + (f"_{via.replace('.', '_')}" if via else "")
                    cr = Row(table=r.table, subject=r.subject, predicate="-const-",
                             column=col, im_class=cls, im_field=fld, via=via,
                             status=r.status, basis=r.basis, value=str(val), kind="const",
                             note=f"constant for {r.column}")
                    cr._node = r._node
                    extra.append(cr)
        self.rows.extend(extra)
        # source-level constants apply to every table
        for im, val in (self.scfg.get("consts") or {}).items():
            cls, fld = im.split(".", 1)
            cr = Row(table="*", subject=self.roots[0].display if self.roots else "", predicate="-const-",
                     column=f"const_{cls}_{fld}", im_class=cls, im_field=fld, status="sure",
                     basis="sources.yaml", value=str(val), kind="const", note="constant on every table")
            cr._node = self.roots[0] if self.roots else None
            self.rows.append(cr)


def _split_collisions_impl(self, auto: set):
    """Give every column that would collide with an earlier one in its table a table of its own.

    items.py groups a table's columns by the object they write (items.group_key) and keys their
    values by field, so two columns writing the same field of the same object collapse and one
    silently wins.  UniProt lost either a protein's full or its short alternative name this way,
    and PubMed three of every four subject headings.  A separate table is the move multi-valued
    predicates already get: each value then reaches its own object, or - for two sources of one
    root attribute - an attribute conflict items.py reports instead of a silent overwrite.

    Only tables this run assigned are touched; a table set in knowledge or by hand is left alone,
    and `check` reports any collision that remains.  Required columns are skipped: they are the
    key of every table, so a duplicate there is a mapping error to fix, not to move.
    """
    from .items import group_key
    taken = {r.table for r in self.rows if r.table}
    first = {}
    for r in self.rows:
        if id(r) not in auto or r.table in ("drop", "*") or r.kind in ("const", "link", "bnode"):
            continue
        if not r.im_class or not r.im_field or r.required == "yes":
            continue
        root = r._node.root.im_class if r._node is not None and r._node.root is not None else ""
        key = (r.table,) + group_key(self.model, root, r.im_class, r.via) + (r.im_field,)
        if key not in first:
            first[key] = r
            continue
        base = f"{r.table}_{_var(r.column).lower()}"
        new, i = base, 2
        while new in taken:
            new, i = f"{base}_{i}", i + 1
        taken.add(new)
        r.table = new


def _pruned_nodes_impl(self) -> set:
    """Nodes whose rows must not enter any table: subjects with role=skip and every node under
    a link row whose status is not link/sure/guess/human (i.e. the human set it to drop/todo)."""
    keep = {"link", "sure", "guess", "human"}
    start = set()
    for sr in self.subject_rows:
        if sr.get("role") == "skip" and sr["subject"] in self.nodes:
            start.add(sr["subject"])
    for r in self.rows:
        if r.kind in ("link", "bnode") and r.status not in keep:
            for c in r._node.children:
                if c.pred is not None and c.pred.curie == r.predicate and (c.objvar == r.column or r.kind == "bnode" and c.display.endswith("/" + self.cfg.local_name(r.predicate))):
                    start.add(c.display)
    out = set()
    todo = list(start)
    while todo:
        d = todo.pop()
        if d in out:
            continue
        out.add(d)
        todo.extend(c.display for c in self.nodes[d].children)
    return out


CrosswalkBuilder._pruned_nodes = _pruned_nodes_impl


CrosswalkBuilder._split_collisions = _split_collisions_impl
def _var(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def _ancestors(node: Node) -> List[Node]:
    out, n = [], node.parent
    while n is not None:
        out.append(n)
        n = n.parent
    return out


def _uniq_var(b: CrosswalkBuilder, v: str) -> str:
    used = {n.var for n in b.nodes.values()}
    base, i = v, 1
    while v in used:
        i += 1
        v = f"{base}{i}"
    return v


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _ex(e) -> str:
    if e is None:
        return ""
    if isinstance(e, list):
        return " | ".join(str(x) for x in e[:3])
    return str(e).replace("\n", " ")[:60]


# ----------------------------------------------------------------- TSV I/O + merge
def read_tsv(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh, delimiter="\t")]


def write_tsv(path: str, rows: List[dict], columns: List[str]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, delimiter="\t", extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else str(r.get(c))) for c in columns})


def three_way_merge(fresh: List[dict], disk: List[dict], base: List[dict], keyf, editable: List[str],
                    mark_human: bool = True, inert: Optional[dict] = None) -> Tuple[List[dict], int, List[dict]]:
    """Return (merged, n_preserved_edits, stale_rows).

    A stale row is one on disk whose key the fresh output no longer has (a subject or predicate
    left model.yaml, or a column was renamed).  It is kept only to protect a human's edit: one
    identical to its base snapshot is old auto output and is dropped.  A kept stale row gets
    `inert` applied (e.g. table=drop), because its key no longer exists and it must not load.

    Both points used to be missing: every stale row was kept with its status intact, so a renamed
    mapping went on loading under its old name - HGNC's xref DataSource constants did exactly
    that, loading once linked and once unlinked.
    """
    dmap = {keyf(r): r for r in disk}
    bmap = {keyf(r): r for r in base}
    merged, preserved = [], 0
    for f in fresh:
        k = keyf(f)
        d, b = dmap.get(k), bmap.get(k)
        if d is not None:
            ref = b if b is not None else f          # no base: compare against fresh
            edited = False
            for col in editable:
                if d.get(col, "") != ref.get(col, ""):
                    f[col] = d.get(col, "")
                    edited = True
            if edited:
                preserved += 1
                if mark_human and "status" in editable and d.get("status", "") == ref.get("status", "") \
                        and f.get("status") not in ("drop", "link"):
                    f["status"] = "human"
        merged.append(f)
    fresh_keys = {keyf(f) for f in fresh}
    stale = []
    for k, d in dmap.items():
        if k in fresh_keys:
            continue
        b = bmap.get(k)
        already = (d.get("note") or "").startswith("STALE")
        edited = b is None or any(d.get(col, "") != b.get(col, "") for col in editable)
        if not (already or edited):
            continue                                   # untouched auto output: let it go
        s = dict(d)
        if not already:
            s["note"] = ("STALE: no longer in model.yaml; " + s.get("note", "")).strip("; ")
        s.update(inert or {})
        merged.append(s)
        stale.append(s)
    return merged, preserved, stale


# ------------------------------------------------------------------- driver
def translate(model: InterMineModel, config_dir: str, out_dir: str, knowledge: Knowledge,
              source_cfg: Optional[dict] = None) -> dict:
    cfg = load_config(config_dir)
    os.makedirs(out_dir, exist_ok=True)
    subj_path = os.path.join(out_dir, "mapping_subjects.tsv")
    subj_base = os.path.join(out_dir, ".mapping_subjects.base.tsv")
    cw_path = os.path.join(out_dir, "mapping_predicates.sssom.tsv")
    cw_base = os.path.join(out_dir, ".mapping_predicates.base.sssom.tsv")

    # subject overrides = human edits found on disk (merged against base)
    disk_subj = read_tsv(subj_path)
    base_subj = read_tsv(subj_base)
    overrides: Dict[str, dict] = {}
    bmap = {r["subject"]: r for r in base_subj}
    for d in disk_subj:
        b = bmap.get(d["subject"])
        o = {}
        for col in SUBJ_EDITABLE:
            if b is None or d.get(col, "") != b.get(col, ""):
                o[col] = d.get(col, "")
        if o:
            overrides[d["subject"]] = o

    b = CrosswalkBuilder(model, cfg, knowledge, source_cfg)
    b.build(overrides)

    # ---- subjects.tsv (auto = what build() produced, incl. overrides already applied)
    fresh_subj = b.subject_rows
    auto_subj = [dict(r) for r in fresh_subj]
    merged_subj, n_subj_edits, _ = three_way_merge(fresh_subj, disk_subj, base_subj,
                                                   lambda r: r["subject"], SUBJ_EDITABLE, mark_human=False)
    write_tsv(subj_base, auto_subj, SUBJ_COLUMNS)
    write_tsv(subj_path, merged_subj, SUBJ_COLUMNS)

    # ---- mapping_predicates.sssom.tsv
    fresh_rows = [_row_dict(r) for r in b.rows]
    auto_rows = [dict(r) for r in fresh_rows]
    disk_rows, base_rows = read_sssom(cw_path)[1], read_sssom(cw_base)[1]
    merged, n_edits, stale = three_way_merge(fresh_rows, disk_rows, base_rows,
                                             lambda r: (r["subject"], r["predicate"], r["column"]),
                                             CW_EDITABLE, inert={"table": "drop"})
    # keep node refs for downstream generation
    node_by_key = {r.key(): r._node for r in b.rows}
    write_sssom(cw_base, auto_rows, cfg)
    write_sssom(cw_path, merged, cfg)

    return {"cfg": cfg, "builder": b, "rows": merged, "subjects": merged_subj,
            "preserved": n_edits, "subject_edits": n_subj_edits, "stale": len(stale),
            "node_by_key": node_by_key, "messages": b.messages}


def _row_dict(r: Row) -> dict:
    d = {c: getattr(r, c) for c in CW_COLUMNS}
    return d


def rows_for_query(rows: List[dict], include_guess: bool = True) -> List[dict]:
    ok = {"sure", "human"} | ({"guess"} if include_guess else set())
    return [r for r in rows if r.get("status") in ok and r.get("im_class") and r.get("im_field")]
