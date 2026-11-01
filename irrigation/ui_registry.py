"""UI page and modal registry, kept in sync with the code and the templates.

The registry holds two kinds of entries:

``page``
    A GET view that renders a template, plus the 404 / 500 error pages.
``modal``
    A Bootstrap modal form declared in a template that a page renders.

On every application start the module rediscovers both - pages from the live
Flask URL map (a view function is a page when it reaches ``render_template``),
modals from ``irrigation/templates`` (every ``<div class="modal">``, attributed
to the pages whose templates render it) - and reconciles the ``ui_registry``
collection of the FIS database:

* a page or modal added to the code appears in the collection,
* a page or modal removed from the code disappears from it,
* the smart id (``section.page.modal``) of an entry that survives never changes.

``irrigation/ui_registry.json`` is the curated catalog: it carries the smart
ids, names and descriptions of the entries that exist today.  An entry the
catalog does not know is stored anyway, with a generated id and name, so a new
page or modal never needs a manual step.

Run ``python -m irrigation.ui_registry`` to rebuild the collection on demand.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from pymongo import ASCENDING, ReplaceOne
from pymongo.errors import BulkWriteError

#: Collection of the FIS database that holds the registry.
COLLECTION = "ui_registry"

CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_registry.json")

_ARABIC = re.compile(r"[\u0600-\u06ff]")

_DIV_RE = re.compile(r"<div\b[^>]*>", re.S)
_ATTR_RE = re.compile(r"""(\b(?:class|id)\s*=\s*)(['"])(.*?)\2""", re.S)
_TITLE_RE = re.compile(
    r"""class\s*=\s*['"][^'"]*modal-title[^'"]*['"][^>]*>(.*?)</h[25]>""", re.S
)
_TAG_RE = re.compile(r"<[^>]+>")
_INCLUDE_RE = re.compile(r"""\{%\s*(?:include|extends)\s+['"]([^'"]+)['"]""")
_JINJA_EXPR_RE = re.compile(r"\{\{.*?\}\}", re.S)


# ---------------------------------------------------------------------------
# Curated catalog
# ---------------------------------------------------------------------------

def load_catalog(path=CATALOG_PATH):
    """Smart ids / names / descriptions written by hand in ``ui_registry.json``."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"pages": [], "modals": []}
    return {"pages": data.get("pages", []), "modals": data.get("modals", [])}


# ---------------------------------------------------------------------------
# Pages: GET views that render a template
# ---------------------------------------------------------------------------

def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _FunctionVisitor(ast.NodeVisitor):
    """One pass over a module: templates rendered and functions called.

    A call inside a nested function counts for every enclosing one, which is
    what the fixed point in :func:`_all_templates` needs to follow a helper
    such as ``_render`` down to its ``render_template``.
    """

    def __init__(self):
        self.direct = {}
        self.calls = {}
        self._frames = []

    def _visit_function(self, node):
        self._frames.append(node.name)
        self.direct.setdefault(node.name, set())
        self.calls.setdefault(node.name, set())
        self.generic_visit(node)
        self._frames.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node):
        name = _call_name(node.func)
        if name == "render_template" and node.args:
            literal = _literal(node.args[0])
            if literal:
                for frame in self._frames:
                    self.direct[frame].add(literal)
        elif name and self._frames:
            for frame in self._frames:
                self.calls[frame].add(name)
        self.generic_visit(node)


def _function_maps(module):
    """``{function: templates rendered directly}`` and ``{function: callees}``."""
    try:
        source_file = inspect.getsourcefile(module)
        if not source_file:
            return {}, {}
        text = Path(source_file).read_text(encoding="utf-8", errors="replace")
        visitor = _FunctionVisitor()
        visitor.visit(ast.parse(text))
    except Exception:  # unreadable or unparsable module: treat as "no pages"
        return {}, {}
    return visitor.direct, visitor.calls


def _all_templates(function, direct, calls):
    """Templates ``function`` renders through its helpers (fixed point)."""
    templates, visited, stack = set(), set(), [function]
    while stack:
        name = stack.pop()
        if name in visited:
            continue
        visited.add(name)
        templates |= direct.get(name, set())
        stack.extend(callee for callee in calls.get(name, ()) if callee in direct)
    return templates


def _templates_of_callable(func):
    """Templates a plain function renders, from its source code."""
    try:
        source = textwrap.dedent(inspect.getsource(func))
        tree = ast.parse(source)
    except Exception:
        return set()
    templates = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) == "render_template" and node.args:
            literal = _literal(node.args[0])
            if literal:
                templates.add(literal)
    return templates


def _blueprint_module(app, endpoint):
    blueprint = app.blueprints.get(endpoint.split(".")[0])
    if blueprint is None:
        return None
    name = blueprint.import_name
    module = sys.modules.get(name)
    if module is None:
        try:
            module = importlib.import_module(name)
        except Exception:
            return None
    return module


def discover_pages(app):
    """``{endpoint: {url, blueprint, templates}}`` for every page of the app.

    An endpoint is a page when it answers GET and its view function reaches
    ``render_template`` - JSON endpoints, downloads, redirects and POST-only
    actions render nothing and stay out of the registry.
    """
    module_cache = {}
    templates_by_endpoint = {}
    pages = {}

    def _module_maps(module):
        name = getattr(module, "__name__", None) or str(module)
        if name not in module_cache:
            module_cache[name] = _function_maps(module)
        return module_cache[name]

    for rule in app.url_map.iter_rules():
        if "GET" not in (rule.methods or set()) or rule.endpoint == "static":
            continue
        endpoint = rule.endpoint

        if endpoint not in templates_by_endpoint:
            module = _blueprint_module(app, endpoint)
            if module is None:
                templates_by_endpoint[endpoint] = set()
            else:
                direct, calls = _module_maps(module)
                templates_by_endpoint[endpoint] = _all_templates(
                    endpoint.rsplit(".", 1)[-1], direct, calls)

        templates = templates_by_endpoint[endpoint]
        if not templates:
            continue

        entry = pages.setdefault(endpoint, {
            "url": rule.rule,
            "blueprint": endpoint.split(".")[0] if "." in endpoint else None,
            "templates": sorted(templates),
        })
        # A view registered under several rules keeps the shortest rule.
        if len(rule.rule) < len(entry["url"]):
            entry["url"] = rule.rule

    # Error pages are routes too: they render errors/404.html and errors/500.html.
    for group in (getattr(app, "error_handler_spec", None) or {}).values():
        for code, registered in (group or {}).items():
            if not isinstance(code, int):
                continue
            # Flask stores {code: {exception class: function}} (and older
            # versions {code: function}).
            handlers = registered.values() if isinstance(registered, dict) else [registered]
            for handler in handlers:
                templates = _templates_of_callable(handler)
                if templates:
                    pages.setdefault("error:%d" % code, {
                        "url": None,
                        "blueprint": None,
                        "templates": sorted(templates),
                    })
                    break

    return pages


# ---------------------------------------------------------------------------
# Modals: Bootstrap dialogs declared in the templates of a page
# ---------------------------------------------------------------------------

def _attributes(tag):
    attributes = {}
    for prefix, _quote, value in _ATTR_RE.findall(tag):
        attributes[prefix.strip().rstrip("=").strip()] = value
    return attributes


def _clean_title(raw):
    """``<i ...></i> Edit Sector / تعديل`` -> ``Edit Sector``.

    The English half is everything before the slash that starts the Arabic one,
    so ``KML/KMZ Elements / عناصر الملف`` keeps its own slash.
    """
    text = _TAG_RE.sub(" ", raw)
    text = text.replace("&nbsp;", " ").replace("&#39;", "'")
    text = re.sub(r"\s+", " ", text).strip()
    for separator in re.finditer(r"/\s*", text):
        if _ARABIC.match(text, separator.end()):
            text = text[:separator.start()]
            break
    return text.strip(" /")


def _name_from_id(modal_id):
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", modal_id).strip()
    words = re.sub(r"\s+(Modal|Dialog)$", "", words, flags=re.I)
    return words.title() or modal_id


def _modals_in(text):
    """Every modal dialog declared in one template, in file order."""
    found = {}
    for match in _DIV_RE.finditer(text):
        attributes = _attributes(match.group(0))
        classes = (attributes.get("class") or "").split()
        modal_id = (attributes.get("id") or "").strip()
        if "modal" not in classes or not modal_id:
            continue
        # Row modals are rendered once per row (editModal{{ s.id }}): the
        # Jinja expression is dropped so the key stays stable.
        key = _JINJA_EXPR_RE.sub("", modal_id).strip()
        if not key or key in found:
            continue
        window = text[match.start(): match.start() + 3000]
        title = _TITLE_RE.search(window)
        found[key] = {
            "modal": key,
            "raw_id": modal_id,
            "title": _clean_title(title.group(1)) if title else "",
            "line": text.count("\n", 0, match.start()) + 1,
        }
    return list(found.values())


def _template_names(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*.html"))


def _resolve_include(name, owner, known):
    if name in known:
        return name
    candidate = str(Path(owner).parent / name).replace("\\", "/")
    while candidate.startswith("./"):
        candidate = candidate[2:]
    return candidate if candidate in known else None


def _owners_map(templates, contents):
    """``{template: {templates that contain it}}`` over include/extends."""
    known = set(templates)
    parents = {template: {template} for template in templates}
    for template in templates:
        for target in _INCLUDE_RE.findall(contents[template]):
            resolved = _resolve_include(target, template, known)
            if resolved:
                parents[resolved].add(template)

    # Transitive: whoever contains a container also contains its modals.
    owners = {template: set(mine) for template, mine in parents.items()}
    changed = True
    while changed:
        changed = False
        for child in templates:
            extra = set()
            for container in parents[child]:
                extra |= owners[container]
            if not extra <= owners[child]:
                owners[child] |= extra
                changed = True
    return owners


def discover_modals(template_root, page_owners):
    """``{template#modal: {modal, title, line, pages}}`` for reachable modals.

    ``page_owners`` maps a template to the endpoints rendering it; a modal of a
    template no page renders is dead code and stays out of the registry.
    """
    root = Path(template_root)
    templates = _template_names(root)
    contents = {
        template: (root / template).read_text(encoding="utf-8", errors="replace")
        for template in templates
    }
    owners = _owners_map(templates, contents)

    modals = {}
    for template in templates:
        pages = set()
        for owner in owners[template]:
            pages |= page_owners.get(owner, set())
        if not pages:
            continue
        for modal in _modals_in(contents[template]):
            modals["%s#%s" % (template, modal["modal"])] = dict(modal, pages=sorted(pages))
    return modals


# ---------------------------------------------------------------------------
# Smart ids
# ---------------------------------------------------------------------------

def _parse_id(smart_id):
    try:
        return tuple(int(part) for part in str(smart_id).split("."))
    except (ValueError, TypeError):
        return (10 ** 6,)


def _humanize_page(endpoint):
    if endpoint.startswith("error:"):
        return "Error %s" % endpoint.split(":", 1)[1]
    return endpoint.rsplit(".", 1)[-1].replace("_", " ").title()


def _humanize_page_description(endpoint):
    if endpoint.startswith("error:"):
        return "Error page served by the %s handler." % endpoint.split(":", 1)[1]
    return "Page served by %s (auto-registered)." % endpoint


#: Page documents cached for the life of the process: the collection is only
#: rewritten by :func:`sync_ui_registry`, which clears this cache.
_PAGE_CACHE = {}


def page_document(db, key):
    """Registry document of the page with ``key``, or ``None`` when unknown."""
    if key not in _PAGE_CACHE:
        _PAGE_CACHE[key] = db[COLLECTION].find_one(
            {"key": key, "kind": "page"}, {"_id": 0})
    return _PAGE_CACHE[key]


def sync_ui_registry(app, db, catalog=None):
    """Reconcile the ``ui_registry`` collection with the running application."""
    catalog = load_catalog() if catalog is None else catalog
    page_catalog = {entry["key"]: entry for entry in catalog.get("pages", [])}
    modal_catalog = {entry["key"]: entry for entry in catalog.get("modals", [])}

    # -- discover ----------------------------------------------------------
    pages = discover_pages(app)
    page_owners = {}
    for endpoint, info in pages.items():
        for template in info["templates"]:
            page_owners.setdefault(template, set()).add(endpoint)
    modals = discover_modals(Path(app.root_path) / (app.template_folder or ""), page_owners)

    collection = db[COLLECTION]
    stored = {doc["key"]: doc for doc in collection.find({})}

    # -- ids ---------------------------------------------------------------
    # Catalogued ids are fixed, ids already stored survive, anything else is
    # allocated next to the entries of its own section / page.
    page_ids, used_modal_ids = {}, set()

    for key in pages:
        entry = page_catalog.get(key) or stored.get(key)
        if entry and entry.get("id"):
            page_ids[key] = str(entry["id"])

    for key in modals:
        entry = modal_catalog.get(key) or stored.get(key)
        if entry and entry.get("id"):
            used_modal_ids.add(str(entry["id"]))

    # Blueprint -> the section its catalogued pages already live in.
    sections_by_blueprint = {}
    page_sections = set()
    for key, smart_id in page_ids.items():
        parts = smart_id.split(".")
        section = int(parts[0])
        page_sections.add(section)
        blueprint = pages[key]["blueprint"]
        if blueprint:
            sections_by_blueprint.setdefault(blueprint, section)
            sections_by_blueprint[blueprint] = min(sections_by_blueprint[blueprint], section)

    section_indexes = {}
    for smart_id in page_ids.values():
        parts = smart_id.split(".")
        if len(parts) > 1 and parts[0].isdigit() and parts[1].isdigit():
            section_indexes.setdefault(int(parts[0]), set()).add(int(parts[1]))

    for key in sorted(set(pages) - set(page_ids)):
        blueprint = pages[key]["blueprint"]
        section = sections_by_blueprint.get(blueprint)
        if section is None:
            section = max(page_sections) + 1 if page_sections else 0
            page_sections.add(section)
            if blueprint:
                sections_by_blueprint[blueprint] = section
        index = max(section_indexes.get(section, {-1})) + 1
        section_indexes.setdefault(section, set()).add(index)
        page_ids[key] = "%d.%d" % (section, index)

    def _page_sort(key):
        return (_parse_id(page_ids[key]), key)

    def _new_modal_id(primary):
        base = page_ids[primary]
        index = 1
        while "%s.%d" % (base, index) in used_modal_ids:
            index += 1
        smart_id = "%s.%d" % (base, index)
        used_modal_ids.add(smart_id)
        return smart_id

    # -- documents ---------------------------------------------------------
    now = datetime.now(timezone.utc)
    documents = []

    for key in sorted(pages, key=_page_sort):
        info, catalogued, previous = pages[key], page_catalog.get(key, {}), stored.get(key, {})
        documents.append({
            "key": key,
            "kind": "page",
            "id": page_ids[key],
            "section": _parse_id(page_ids[key])[0],
            "name": catalogued.get("name") or previous.get("name")
                    or _humanize_page(key),
            "description": catalogued.get("description") or previous.get("description")
                           or _humanize_page_description(key),
            "url": info["url"],
            "endpoint": key,
            "blueprint": info["blueprint"],
            "templates": info["templates"],
            "updated_at": now,
        })

    # A modal of a template no page renders is dead code: it is not registered.
    assigned_modals = {key: info for key, info in modals.items() if info["pages"]}

    # A modal belongs to the page that shows it first in the workflow.
    for key, info in sorted(assigned_modals.items()):
        primary = min(info["pages"], key=_page_sort)
        catalogued, previous = modal_catalog.get(key, {}), stored.get(key, {})
        smart_id = catalogued.get("id") or previous.get("id") or _new_modal_id(primary)
        used_modal_ids.add(str(smart_id))
        template, modal = key.split("#", 1)
        documents.append({
            "key": key,
            "kind": "modal",
            "id": str(smart_id),
            "section": _parse_id(smart_id)[0],
            "name": catalogued.get("name") or previous.get("name")
                    or (info.get("title") or _name_from_id(modal)),
            "description": catalogued.get("description") or previous.get("description")
                           or "Modal form declared in %s." % template,
            "url": pages.get(primary, {}).get("url"),
            "endpoint": None,
            "template": template,
            "modal": modal,
            "raw_id": info.get("raw_id") or modal,
            "pages": sorted(info["pages"], key=_page_sort),
            "primary_page": primary,
            "updated_at": now,
        })

    # -- reconcile ---------------------------------------------------------
    target = {document["key"]: document for document in documents}
    added = sorted(set(target) - set(stored))
    removed = sorted(set(stored) - set(target))

    # One round trip instead of one per entry: the registry is rebuilt from
    # scratch, so a partial write is corrected by the next sync anyway.
    if target:
        try:
            collection.bulk_write(
                [ReplaceOne({"key": key}, document, upsert=True)
                 for key, document in target.items()],
                ordered=False,
            )
        except BulkWriteError as error:
            # A duplicate key only means another process synced first: its
            # document is the same registry, so the sync can continue.
            unexpected = [detail for detail in error.details.get("writeErrors", [])
                          if detail.get("code") != 11000]
            if unexpected:
                raise
    if removed:
        collection.delete_many({"key": {"$in": removed}})

    collection.create_index([("key", ASCENDING)], unique=True, name="ui_registry_key_unique")
    collection.create_index([("kind", ASCENDING), ("id", ASCENDING)],
                            name="ui_registry_kind_id")
    _PAGE_CACHE.clear()

    return {
        "pages": sum(1 for document in documents if document["kind"] == "page"),
        "modals": sum(1 for document in documents if document["kind"] == "modal"),
        "total": len(documents),
        "added": added,
        "removed": removed,
    }


if __name__ == "__main__":
    from irrigation import create_app
    from irrigation.extensions import mongo
    from irrigation.schema import real_database

    application = create_app()
    summary = sync_ui_registry(application, real_database(mongo))
    print("UI registry ready: %(pages)d pages, %(modals)d modal forms "
          "(%(total)d entries, %(added)d added, %(removed)d removed)" % {
              "pages": summary["pages"],
              "modals": summary["modals"],
              "total": summary["total"],
              "added": len(summary["added"]),
              "removed": len(summary["removed"]),
          })
