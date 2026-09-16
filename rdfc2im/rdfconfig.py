"""Parse rdf-config per-source YAML (model.yaml, prefix.yaml, endpoint.yaml, sparql.yaml).

model.yaml grammar (as used by DBCLS rdf-config):

  - SubjectName example:uri:          # or "- SubjectName:" (no example), or "- []:" (blank node)
    - a: type | [types]
    - prefix:pred[?*+{n,m}]:          # cardinality suffix; may carry a trailing "# comment"
      - object_var: example           # example = literal, CURIE/<IRI>, SubjectName, [SubjectName,...]
      - []:                           # inline anonymous blank node
        - pred: ...
    - prefix:pred*: SubjectName       # scalar value = object is that subject, no variable
"""
from __future__ import annotations
import os
import re
import yaml
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CARD_RE = re.compile(r"^(.*?)([?*+]|\{\d*,?\d*\})?$")


@dataclass
class ObjectVar:
    name: str                         # variable name (synthesised for anonymous bnodes)
    example: Any = None
    sub_subjects: List[str] = field(default_factory=list)   # named subjects it references
    inline: Optional["Subject"] = None                       # inline "[]:" blank node
    is_literal: bool = False


@dataclass
class Predicate:
    curie: str
    cardinality: str                  # "", "?", "*", "+", "{n,m}"
    objects: List[ObjectVar] = field(default_factory=list)
    comment: str = ""

    @property
    def multi(self) -> bool:
        return self.cardinality in ("*", "+") or self.cardinality.startswith("{")

    @property
    def optional(self) -> bool:
        return self.cardinality in ("?", "*")


@dataclass
class Subject:
    name: str
    example: str = ""
    types: List[str] = field(default_factory=list)
    predicates: List[Predicate] = field(default_factory=list)
    blank: bool = False


@dataclass
class RdfConfig:
    name: str
    dir: str
    subjects: List[Subject]
    prefixes: Dict[str, str]
    endpoint: Optional[str]
    graphs: List[str]
    sparql: Dict[str, Any]

    def subject(self, name: str) -> Optional[Subject]:
        for s in self.subjects:
            if s.name == name:
                return s
        return None

    def expand(self, curie: str) -> str:
        if curie is None:
            return ""
        curie = str(curie).strip()
        if curie.startswith("<") and curie.endswith(">"):
            return curie[1:-1]
        if ":" in curie:
            p, loc = curie.split(":", 1)
            if p in self.prefixes and not loc.startswith("//"):
                return self.prefixes[p] + loc
        return curie

    def local_name(self, curie: str) -> str:
        uri = self.expand(curie)
        return re.split(r"[/#]", uri.rstrip("/"))[-1] if uri else ""


# ----------------------------------------------------------------- parsing
def _read_yaml(path: str):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        txt = fh.read().replace("\r\n", "\n").replace("\r", "\n")
        # tolerate tabs inside YAML indentation
        txt = txt.replace("\t", "  ")
        # "- []:" (anonymous blank node) is an unhashable YAML key -> quote it
        txt = re.sub(r"^(\s*-\s*)\[\s*\](\s*:)", r'\1"[]"\2', txt, flags=re.M)
        try:
            return yaml.safe_load(txt)
        except yaml.YAMLError as e:      # pragma: no cover
            raise ValueError(f"{path}: {e}")


def parse_prefixes(path: str) -> Dict[str, str]:
    data = _read_yaml(path) or {}
    out = {}
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("<") and v.endswith(">"):
                v = v[1:-1]
            out[str(k)] = v
    return out


def parse_endpoint(path: str):
    data = _read_yaml(path) or {}
    ep, graphs = None, []
    lst = data.get("endpoint") if isinstance(data, dict) else None
    if isinstance(lst, list):
        for item in lst:
            if isinstance(item, str) and ep is None:
                ep = item.strip()
            elif isinstance(item, dict) and "graph" in item:
                g = item["graph"]
                graphs.extend(g if isinstance(g, list) else [g])
    elif isinstance(lst, str):
        ep = lst.strip()
    return ep, graphs


_bn_counter = [0]


def _parse_subject(name: str, example: str, items: list, blank: bool) -> Subject:
    s = Subject(name=name, example=example, blank=blank)
    if not isinstance(items, list):
        return s
    for it in items:
        if not isinstance(it, dict):
            continue
        for k, v in it.items():
            key = str(k).strip()
            if key == "a":
                s.types = [str(x) for x in (v if isinstance(v, list) else [v]) if x is not None]
                continue
            m = CARD_RE.match(key)
            pred, card = m.group(1), m.group(2) or ""
            p = Predicate(curie=pred.strip(), cardinality=card)
            if isinstance(v, list):
                for ov in v:
                    if isinstance(ov, dict):
                        for on, oex in ov.items():
                            on = str(on).strip()
                            if on == "[]":
                                _bn_counter[0] += 1
                                bn = _parse_subject(f"_bn{_bn_counter[0]}", "", oex, True)
                                p.objects.append(ObjectVar(name=bn.name, inline=bn))
                            else:
                                p.objects.append(_objvar(on, oex))
                    elif isinstance(ov, str):
                        p.objects.append(_objvar(ov, None))
            elif isinstance(v, str):
                p.objects.append(ObjectVar(name=_snake(v), example=v, sub_subjects=[v]))
            elif v is None:
                p.objects.append(ObjectVar(name=_snake(pred.split(":")[-1]), example=None))
            s.predicates.append(p)
    return s


def _objvar(name: str, ex: Any) -> ObjectVar:
    ov = ObjectVar(name=name, example=ex)
    cands = ex if isinstance(ex, list) else [ex]
    for c in cands:
        if isinstance(c, str) and re.match(r"^[A-Z][A-Za-z0-9_]*$", c.strip()):
            ov.sub_subjects.append(c.strip())
    if not ov.sub_subjects:
        ov.is_literal = _looks_literal(ex)
    return ov


def _looks_literal(ex: Any) -> bool:
    if ex is None:
        return True
    if isinstance(ex, (int, float, bool)):
        return True
    if isinstance(ex, list):
        return all(_looks_literal(e) for e in ex)
    s = str(ex).strip()
    if s.startswith("<") and s.endswith(">"):
        return False
    if re.match(r"^[A-Za-z][\w.-]*:\S+$", s) and " " not in s and not s.startswith('"'):
        return False          # CURIE-looking
    return True


def _snake(s: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


def parse_model(path: str) -> List[Subject]:
    _bn_counter[0] = 0
    data = _read_yaml(path)
    subjects: List[Subject] = []
    if not isinstance(data, list):
        return subjects
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for k, v in entry.items():
            head = str(k).strip()
            parts = head.split(None, 1)
            name = parts[0]
            example = parts[1].strip() if len(parts) > 1 else ""
            blank = example == "[]" or name == "[]"
            subjects.append(_parse_subject(name, example, v, blank))
    # resolve sub_subjects: only keep names that really are subjects
    known = {s.name for s in subjects}
    for s in subjects:
        _fix_refs(s, known)
    return subjects


def _fix_refs(s: Subject, known: set):
    for p in s.predicates:
        for o in p.objects:
            if o.inline:
                _fix_refs(o.inline, known)
                continue
            o.sub_subjects = [x for x in o.sub_subjects if x in known]
            if not o.sub_subjects and not o.inline:
                o.is_literal = _looks_literal(o.example)


def load_config(config_dir: str) -> RdfConfig:
    name = os.path.basename(os.path.normpath(config_dir))
    subjects = parse_model(os.path.join(config_dir, "model.yaml"))
    prefixes = parse_prefixes(os.path.join(config_dir, "prefix.yaml"))
    ep, graphs = parse_endpoint(os.path.join(config_dir, "endpoint.yaml"))
    sparql = _read_yaml(os.path.join(config_dir, "sparql.yaml")) or {}
    if not isinstance(sparql, dict):
        sparql = {}
    return RdfConfig(name=name, dir=config_dir, subjects=subjects, prefixes=prefixes,
                     endpoint=ep, graphs=graphs, sparql=sparql)
