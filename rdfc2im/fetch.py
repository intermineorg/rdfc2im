"""POST generated queries to their endpoints and save the raw SPARQL-TSV."""
from __future__ import annotations
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ACCEPTS = ["application/sparql-results+json", "text/tab-separated-values", "text/csv"]

ENDPOINT_RE = re.compile(r"^# Endpoint:\s*(\S+)", re.M)


def endpoint_of(query_text: str):
    m = ENDPOINT_RE.search(query_text)
    ep = m.group(1) if m else None
    if not ep or ep.startswith("("):
        return None
    return ep


def is_empty_select(query_text: str) -> bool:
    m = re.search(r"^SELECT\s+(DISTINCT\s+)?(.*)$", query_text, re.M)
    return not m or not m.group(2).strip()


LIMIT_RE = re.compile(r"^LIMIT\s+\d+\s*$", re.M)


def with_limit(q: str, limit) -> str:
    """limit None = keep the query's own LIMIT; 0 = remove it; n = replace/append LIMIT n."""
    if limit is None:
        return q
    q = LIMIT_RE.sub("", q).rstrip("\n") + "\n"
    return q if not limit else q + f"LIMIT {int(limit)}\n"


def _sniff(body: bytes, fmt: str) -> str:
    """Servers do not always honour Accept; decide the format from the body."""
    head = body.lstrip()[:200]
    if head.startswith(b"{"):
        return "application/sparql-results+json"
    first = head.split(b"\n", 1)[0]
    if b"\t" in first:
        return "text/tab-separated-values"
    if b"," in first and fmt != "text/tab-separated-values":
        return "text/csv"
    return fmt


def _order_by(q: str) -> str:
    """Stable paging needs ORDER BY; use the first SELECT variable."""
    if re.search(r"^ORDER BY", q, re.M):
        return q
    m = re.search(r"^SELECT\s+(?:DISTINCT\s+)?\?(\w+)", q, re.M)
    if not m:
        return q
    return re.sub(r"^(}\s*)$", r"\1ORDER BY ?" + m.group(1) + "\n", q, count=1, flags=re.M)


def fetch_query(query_path: str, out_path: str, timeout: int = 600, dry_run: bool = False,
                force: bool = False, sleep: float = 0.0, limit=None, page: int = 0, log=print) -> str:
    with open(query_path, encoding="utf-8") as fh:
        q = with_limit(fh.read(), limit)
    ep = endpoint_of(q)
    if ep is None:
        log(f"  skip {query_path}: no endpoint"); return "skip-endpoint"
    if is_empty_select(q):
        log(f"  skip {query_path}: empty SELECT"); return "skip-empty"
    if os.path.exists(out_path) and not force:
        log(f"  keep {out_path} (exists; FORCE=1 to refetch)"); return "kept"
    if dry_run:
        m = LIMIT_RE.search(q)
        log(f"  DRY_RUN would POST {query_path} -> {ep} ({m.group(0).strip() if m else 'no LIMIT'})"); return "dry"
    t0 = time.time()
    if page and page > 0:
        return _fetch_paged(q, ep, out_path, timeout, sleep, limit, page, log, t0)
    body, fmt, err = _fetch_once(q, ep, timeout)
    if body is None:
        log(f"  FAILED {query_path}: {err}")
        return "failed"
    body = _normalise(body, fmt)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(body)
    n = max(body.count(b"\n") - 1, 0)
    log(f"  fetched {out_path}: {n} rows in {time.time() - t0:.1f}s" + (f" [{fmt}]" if fmt != ACCEPTS[0] else "")
        + ("  <-- no rows: check the query" if n == 0 else ""))
    if sleep:
        time.sleep(sleep)
    return "fetched"


def _normalise(body: bytes, fmt: str) -> bytes:
    fmt = _sniff(body, fmt)
    if fmt == "text/csv":
        return _csv_to_tsv(body)
    if fmt == "application/sparql-results+json":
        return _json_to_tsv(body)
    return body


SORTED_TOP_CAP = 200_000  # Virtuoso's hard ceiling on any ORDER BY + LIMIT/OFFSET query


def _select_vars(q: str):
    m = re.search(r"^SELECT\s+(?:DISTINCT\s+)?(.*)$", q, re.M)
    return re.findall(r"\?(\w+)", m.group(1)) if m else []


def _inject_values(q: str, var: str, terms) -> str:
    """Add VALUES ?var { term1 term2 ... } to the outer WHERE block, before its closing brace -
    the same bare-'}' line _order_by anchors ORDER BY on. VALUES-equality is the one comparison
    RDF Portal's Virtuoso evaluates reliably on plain literals (unlike `<`/`>` - see _fetch_paged's
    docstring), so this is the safe way to restrict a query to a known set of keys."""
    return re.sub(r"^(}\s*)$", "  VALUES ?" + var + " { " + " ".join(terms) + " }\n\\1", q, count=1, flags=re.M)


def _required_only(q: str):
    """Strip every OPTIONAL block from a WHERE clause, innermost first, to get the pattern that
    is actually required to match - used to safely enumerate a table's full key domain (a key
    that only appears inside an OPTIONAL cannot be batched on: some rows would have no value to
    match against). Returns None rather than a guess if the result doesn't look clean (an
    OPTIONAL survived, or braces don't balance) - this only needs to work for rdfc2im's own
    generated query shapes, not arbitrary SPARQL."""
    prev, text = None, q
    while prev != text:
        prev = text
        text = re.sub(r"OPTIONAL\s*\{[^{}]*\}\s*\.?\s*", "", text)
    if "OPTIONAL" in text or text.count("{") != text.count("}"):
        return None
    return text


def _count_rows(q: str, ep: str, timeout: int):
    """SELECT (COUNT(*) AS ?rdfc2im_c) over the query's own SELECT DISTINCT, so the count matches
    what paging would actually produce. Keeps the original PREFIX declarations (everything before
    the SELECT line) - a rebuilt query without them is invalid and fails as an ordinary request
    error, which looks identical to "count unavailable" if that isn't kept distinct: worth calling
    out because that is exactly the mistake this function first shipped with. Returns None (not
    0) on any failure - callers must not treat that as an empty table."""
    m = re.search(r"^SELECT\s", q, re.M)
    if not m:
        return None
    preamble, rest = q[: m.start()], q[m.start():]
    cq = preamble + "SELECT (COUNT(*) AS ?rdfc2im_c) WHERE {\n  { " + rest.rstrip("\n") + " }\n}\n"
    body, fmt, err = _fetch_once(cq, ep, timeout)
    if body is None:
        return None
    lines = _normalise(body, fmt).split(b"\n")
    if len(lines) < 2 or not lines[1].strip():
        return None
    m2 = re.match(rb'^"(\d+)"', lines[1])
    return int(m2.group(1)) if m2 else None


def _fetch_batched_text(q, ep, out_path, key_var, key_terms, timeout, sleep, batch_size, log):
    """Core of fetch_values_batched, operating on query text already read from disk."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    header = "\t".join("?" + v for v in _select_vars(q)).encode()
    terms = list(key_terms)
    total, failed = 0, 0
    n_batches = max((len(terms) + batch_size - 1) // batch_size, 1) if terms else 0
    with open(out_path, "wb") as out:
        out.write(header + b"\n")
        out.flush()
        for i in range(0, len(terms), batch_size):
            batch_no = i // batch_size + 1
            batch = terms[i:i + batch_size]
            body, fmt, err = _fetch_once(_inject_values(q, key_var, batch), ep, timeout)
            if body is None:
                log(f"  FAILED batch {batch_no}/{n_batches} of {out_path}: {err}")
                failed += 1
                continue
            body = _normalise(body, fmt)
            rows = [ln for ln in body.split(b"\n")[1:] if ln]
            out.write(b"\n".join(rows) + (b"\n" if rows else b""))
            out.flush()
            total += len(rows)
            if batch_no % 20 == 0 or batch_no == n_batches:
                log(f"    batch {batch_no}/{n_batches}: {total} rows so far")
            if sleep:
                time.sleep(sleep)
    log(f"  fetched {out_path}: {total} rows from {len(terms)} keys"
        + (f" in {n_batches} batch(es)" if terms else " (empty scope)")
        + (f"  <-- {failed}/{n_batches} batch(es) failed, data is incomplete" if failed else ""))
    return "partial" if failed else "fetched"


def fetch_values_batched(query_path, out_path, key_var, key_terms, timeout=600, sleep=0.0,
                          batch_size=500, log=print) -> str:
    """Fetch a table by restricting `key_var` to `key_terms` (already exact SPARQL term syntax,
    e.g. from another already-fetched table's own output) in fixed-size VALUES batches, instead
    of paging the whole thing. Every batch is a self-contained, LIMIT-free request, so no single
    batch can approach SORTED_TOP_CAP, and VALUES-equality is reliable on this endpoint where
    `<`/`>` on plain literals is not (see _fetch_paged). Used to scope a query to a known set of
    keys from elsewhere - e.g. PubMed limited to PMIDs already referenced by loaded genes - not
    just to page a table past the cap (_fetch_paged does that internally). An empty key_terms is
    a legitimate "nothing in scope yet", not a failure, and still writes a valid (header-only)
    file so downstream steps see zero rows rather than a missing file.
    """
    with open(query_path, encoding="utf-8") as fh:
        q = LIMIT_RE.sub("", fh.read()).rstrip("\n") + "\n"
    ep = endpoint_of(q)
    if ep is None:
        log(f"  skip {query_path}: no endpoint"); return "skip-endpoint"
    return _fetch_batched_text(q, ep, out_path, key_var, key_terms, timeout, sleep, batch_size, log)


def _fetch_paged(q, ep, out_path, timeout, sleep, limit, page, log, t0):
    """LIMIT page OFFSET k*page until a short page; LIMIT (total) still caps the extract.

    A table whose true row count exceeds SORTED_TOP_CAP cannot be paged this way to completion:
    Virtuoso refuses any ORDER BY + LIMIT/OFFSET request once offset+limit exceeds 200000
    ("SR353: Sorted TOP clause specifies more then N rows to sort. Only 200000 are allowed").
    A cheap COUNT(*) pre-check catches this before paging starts (skipped when it fails or when
    limit already keeps the extract under the cap) and switches to fetch_values_batched over the
    query's own required (non-OPTIONAL) key domain instead - VALUES-equality is reliable on this
    endpoint where the `<`/`>` a keyset rewrite would need is not (confirmed against the live
    endpoint: FILTER(?id > "1") on ncbigene's own data excludes "2" through "9" while admitting
    "10", "100", ...; see LOAD-TRIAL.md). If the count fails, or the ordering variable turns out
    to live only inside an OPTIONAL (so no safe domain query exists), pagination proceeds as
    before and simply reports "partial" if it does hit the cap, rather than guessing.
    """
    base_noorder = LIMIT_RE.sub("", q).rstrip("\n") + "\n"
    if not limit:
        count = _count_rows(base_noorder, ep, timeout)
        if count is not None and count > SORTED_TOP_CAP:
            var_m = re.search(r"^SELECT\s+(?:DISTINCT\s+)?\?(\w+)", base_noorder, re.M)
            keyvar = var_m.group(1) if var_m else None
            required = _required_only(base_noorder) if keyvar else None
            domain_body = None
            if required is not None and re.search(rf"\?{keyvar}\b", required.split("WHERE", 1)[1]):
                domain_q = re.sub(r"^SELECT\s+(?:DISTINCT\s+)?.*$", f"SELECT DISTINCT ?{keyvar}",
                                  required, count=1, flags=re.M)
                domain_body, domain_fmt, domain_err = _fetch_once(domain_q, ep, timeout)
            if domain_body is not None:
                keys = [ln.decode() for ln in _normalise(domain_body, domain_fmt).split(b"\n")[1:] if ln.strip()]
                log(f"  {out_path}: {count} rows exceeds the {SORTED_TOP_CAP}-row Sorted TOP cap; "
                    f"batching over {len(keys)} distinct ?{keyvar} values instead")
                return _fetch_batched_text(base_noorder, ep, out_path, keyvar, keys, timeout, sleep, 500, log)
            log(f"  {out_path}: {count} rows exceeds the {SORTED_TOP_CAP}-row Sorted TOP cap and "
                f"no safe key to batch on was found; falling back to plain paging, which will "
                f"stop at the cap")
    base = _order_by(base_noorder)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    total, k, header = 0, 0, None
    with open(out_path, "wb") as out:
        while True:
            size = page if not limit else min(page, int(limit) - total)
            if size <= 0:
                break
            offset = k * page
            if offset + size > SORTED_TOP_CAP:
                log(f"  PARTIAL {out_path}: stopping at {total} rows - Virtuoso refuses any "
                    f"ORDER BY + LIMIT/OFFSET request past {SORTED_TOP_CAP} rows (offset+limit "
                    f"{offset + size} would exceed it); more rows exist")
                return "partial"
            pq = base + f"LIMIT {size} OFFSET {offset}\n"
            body, fmt, err = _fetch_once(pq, ep, timeout)
            if body is None:
                log(f"  FAILED page {k + 1} of {out_path}: {err}")
                return "failed" if k == 0 else "partial"
            body = _normalise(body, fmt)
            lines = body.split(b"\n")
            if header is None:
                header = lines[0]
                out.write(header + b"\n")
            rows = [ln for ln in lines[1:] if ln]
            out.write(b"\n".join(rows) + (b"\n" if rows else b""))
            total += len(rows)
            log(f"    page {k + 1}: {len(rows)} rows (total {total})")
            k += 1
            if len(rows) < size:
                break
            if limit and total >= int(limit):
                log(f"    (LIMIT {limit} reached; more rows exist)")
                break
            if sleep:
                time.sleep(sleep)
    log(f"  fetched {out_path}: {total} rows in {k} page(s), {time.time() - t0:.1f}s" + ("  <-- no rows: check the query" if total == 0 else ""))
    if sleep:
        time.sleep(sleep)
    return "fetched"


def _fetch_once(q, ep, timeout):
    body, fmt, err = None, None, None
    # try POST then GET, and the three result formats, until one is accepted
    for method in ("POST", "GET"):
        for accept in ACCEPTS:
            try:
                body = _request(ep, q, method, accept, timeout)
                fmt = accept
                break
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code} {e.reason} ({method}, Accept: {accept})"
                if e.code not in (406, 415, 405, 400, 302, 303):
                    break
            except Exception as e:  # network errors: report and give up on this query
                err = f"{type(e).__name__}: {e}"
                break
        if body is not None:
            break
    return body, fmt, err


class _NoRedirectPost(urllib.request.HTTPRedirectHandler):
    """Re-issue a redirected POST as a GET with the query in the URL (what SPARQL servers expect)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() == "POST":
            q = urllib.parse.parse_qs(req.data.decode())["query"][0]
            sep = "&" if "?" in newurl else "?"
            return urllib.request.Request(newurl + sep + urllib.parse.urlencode({"query": q}),
                                          headers=dict(req.header_items()))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(ep: str, q: str, method: str, accept: str, timeout: int) -> bytes:
    headers = {"Accept": accept, "User-Agent": "rdfc2im/0.2"}
    if method == "POST":
        req = urllib.request.Request(ep, data=urllib.parse.urlencode({"query": q}).encode(),
                                     headers=dict(headers, **{"Content-Type": "application/x-www-form-urlencoded"}))
    else:
        sep = "&" if "?" in ep else "?"
        req = urllib.request.Request(ep + sep + urllib.parse.urlencode({"query": q}), headers=headers)
    opener = urllib.request.build_opener(_NoRedirectPost)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _term(b: dict) -> str:
    """SPARQL-JSON binding -> W3C SPARQL-TSV term."""
    v = b.get("value", "")
    if b.get("type") == "uri":
        return f"<{v}>"
    if b.get("type") == "bnode":
        return f"_:{v}"
    esc = v.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    if b.get("xml:lang"):
        return f'"{esc}"@{b["xml:lang"]}'
    if b.get("datatype"):
        return f'"{esc}"^^<{b["datatype"]}>'
    return f'"{esc}"'


def _json_to_tsv(body: bytes) -> bytes:
    d = json.loads(body.decode("utf-8"))
    vars_ = d["head"]["vars"]
    lines = ["\t".join("?" + v for v in vars_)]
    for row in d["results"]["bindings"]:
        lines.append("\t".join(_term(row[v]) if v in row else "" for v in vars_))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _csv_to_tsv(body: bytes) -> bytes:
    """SPARQL CSV has bare values (no term syntax); quote them as plain literals so tsv.py's
    cleaner treats every cell the same way.  IRIs are left bare (cleaner passes them through)."""
    import csv, io
    rd = csv.reader(io.StringIO(body.decode("utf-8")))
    rows = list(rd)
    if not rows:
        return b""
    out = ["\t".join("?" + h.lstrip("?") for h in rows[0])]
    for r in rows[1:]:
        out.append("\t".join(v if v.startswith("http") else '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in r))
    return ("\n".join(out) + "\n").encode("utf-8")
