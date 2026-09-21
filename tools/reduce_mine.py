#!/usr/bin/env python3
"""Trim HumanMine's full ~40-source configuration down to just the sources a reduced demo build
actually runs. See LOAD-TRIAL.md's "Reducing a mine" section for the four files this touches and
why each one breaks differently if missed - this script is the not-yet-scripted step that section
describes doing by hand. Always validate against the REAL merged model the mine actually built
(dbmodel/build/resources/main/genomic_model.xml), never the live HumanMine model - a class core
to HumanMine's full deployment can be entirely absent from a 9-source reduced build, and vice
versa a class only some replaced source contributes can still be present.

Subcommands:
  sources   <project.xml> <type>
      Keep only <source type="TYPE" .../> entries (our rdfc2im-generated sources all share one
      type name, set once in rdfc2im.yaml - see that file's `type:` key). Drops every other
      stock HumanMine source project.xml would otherwise still list, which is what makes
      dbmodel:addSourceDependencies try to resolve a jar for each one, JCenter or not.

  priorities <genomic_priorities.properties> <genomic_model.xml>
      Drop every line whose class (before the first '.') is not in the merged model.

  webconfig <webconfig-model.xml> <genomic_model.xml>
      Drop <class> entries whose className is absent from the merged model, or whose own
      fieldExpr attributes reference a path that does not resolve from that class. Drop
      <widget> entries (any element under <widgets>) whose startClass/typeClass is absent, or
      whose views/enrich/enrichIdentifier/constraints/pathStrings paths do not resolve.

  objectstoresummary <objectstoresummary.config.properties> <genomic_model.xml>
      Drop every "X.autocomplete" (or "X.fields", "X.displayName" etc - any "X.*" key) line
      whose leading class X is not in the merged model.

Every subcommand edits in place and prints a one-line summary of what it removed, so the effect
is visible in the calling script's own log rather than silent.
"""
import re
import sys
import lxml.etree as ET


def load_model_classes(genomic_model_xml):
    """class name -> {field name -> (kind, referenced_type)}, kind in attribute|reference|collection.
    Inheritance-aware: a subclass's field lookup falls back to its ancestors (see resolve_path)."""
    t = ET.parse(genomic_model_xml)
    classes = {}
    extends = {}
    for c in t.getroot().iter("class"):
        name = c.get("name")
        fields = {}
        for f in c:
            if not isinstance(f.tag, str) or f.tag not in ("attribute", "reference", "collection"):
                continue
            typ = f.get("type") if f.tag == "attribute" else f.get("referenced-type")
            fields[f.get("name")] = (f.tag, typ)
        classes[name] = fields
        ext = c.get("extends")
        extends[name] = ext.split() if ext else []
    return classes, extends


def resolve_path(classes, extends, start_class, path):
    """True if each dot-separated step of `path` resolves from `start_class`, walking up
    `extends` (multiple, since InterMine interfaces can multiply-inherit) at each step."""
    def field_of(cls, fname, seen=None):
        seen = seen or set()
        if cls in seen or cls not in classes:
            return None
        seen.add(cls)
        if fname in classes[cls]:
            return classes[cls][fname]
        for parent in extends.get(cls, []):
            found = field_of(parent, fname, seen)
            if found:
                return found
        return None

    cls = start_class
    if cls not in classes:
        return False
    for step in path.split("."):
        if not step:
            continue
        f = field_of(cls, step)
        if f is None:
            return False
        kind, typ = f
        if kind == "attribute":
            return True  # an attribute must be the last step; treat reaching one as resolved
        cls = typ
    return True


def cmd_sources(args):
    # Extra positional args (beyond path/keep_type) are exact source names to keep in addition
    # to the type match - e.g. "reactome", the stock alongside source sources.yaml's own
    # `alongside: [reactome]` declares as a real dependency of our humanmine-reactome (Pathway
    # merges onto it by identifier; without it Pathway has no protein/gene participants at all).
    path, keep_type = args[0], args[1]
    keep_names = set(args[2:])
    t = ET.parse(path)
    sources = t.getroot().find("sources")
    kept, dropped = [], []
    for src in list(sources):
        if src.get("type") == keep_type or src.get("name") in keep_names:
            kept.append(src.get("name"))
        else:
            dropped.append(src.get("name"))
            sources.remove(src)
    t.write(path, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    print(f"reduce_mine sources: kept {len(kept)} ({', '.join(kept)}), dropped {len(dropped)}")


def cmd_priorities(args):
    path, model_xml = args
    classes, _ = load_model_classes(model_xml)
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    kept, dropped = [], []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            kept.append(line)
            continue
        cls = s.split(".", 1)[0]
        if cls in classes:
            kept.append(line)
        else:
            dropped.append(s)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(kept)
    print(f"reduce_mine priorities: dropped {len(dropped)} line(s) for absent classes"
          + (": " + ", ".join(sorted(set(d.split('.', 1)[0] for d in dropped))) if dropped else ""))


def cmd_objectstoresummary(args):
    path, model_xml = args
    classes, _ = load_model_classes(model_xml)
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    kept, dropped = [], []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            kept.append(line)
            continue
        key = s.split("=", 1)[0]
        cls = key.split(".", 1)[0]
        if cls in classes:
            kept.append(line)
        else:
            dropped.append(key)
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(kept)
    print(f"reduce_mine objectstoresummary: dropped {len(dropped)} line(s) for absent classes"
          + (": " + ", ".join(sorted(set(d.split('.', 1)[0] for d in dropped))) if dropped else ""))


WIDGET_PATH_ATTRS = ["views", "enrich", "enrichIdentifier", "constraints", "pathStrings"]


def cmd_webconfig(args):
    path, model_xml = args
    classes, extends = load_model_classes(model_xml)
    t = ET.parse(path)
    root = t.getroot()

    dropped_classes = []
    classes_el = root.find("classes")
    class_by_name = {}
    if classes_el is not None:
        for c in list(classes_el):
            name = c.get("className", "").rsplit(".", 1)[-1]
            if name in classes:
                class_by_name[name] = c
                continue
            dropped_classes.append(name)
            classes_el.remove(c)
        # A fieldExpr can reference a field of a class kept above but no longer resolvable if
        # that field's own type was dropped - re-check now that dropped_classes is final.
        for name, c in list(class_by_name.items()):
            for fc in c.iter("fieldconfig"):
                expr = fc.get("fieldExpr")
                if expr and not resolve_path(classes, extends, name, expr):
                    c.remove(fc)

    dropped_widgets = []
    widgets_el = root.find("widgets")
    if widgets_el is not None:
        for w in list(widgets_el):
            start = w.get("startClass") or w.get("typeClass")
            ok = start in classes if start else True
            if ok:
                for attr in WIDGET_PATH_ATTRS:
                    val = w.get(attr)
                    if not val:
                        continue
                    paths = [p.strip() for p in val.split(",") if p.strip()]
                    if not all(resolve_path(classes, extends, start, p) for p in paths):
                        ok = False
                        break
            if ok:
                continue
            dropped_widgets.append(w.get("id") or w.tag)
            widgets_el.remove(w)

    t.write(path, xml_declaration=True, encoding="UTF-8", pretty_print=True)
    print(f"reduce_mine webconfig: dropped {len(dropped_classes)} class(es)"
          + (" (" + ", ".join(dropped_classes) + ")" if dropped_classes else "")
          + f", {len(dropped_widgets)} widget(s)"
          + (" (" + ", ".join(dropped_widgets) + ")" if dropped_widgets else ""))


COMMANDS = {
    "sources": cmd_sources,
    "priorities": cmd_priorities,
    "objectstoresummary": cmd_objectstoresummary,
    "webconfig": cmd_webconfig,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: {sys.argv[0]} <{'|'.join(COMMANDS)}> ...")
    COMMANDS[sys.argv[1]](sys.argv[2:])
