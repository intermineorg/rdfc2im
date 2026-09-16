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


def _fetch_paged(q, ep, out_path, timeout, sleep, limit, page, log, t0):
    """LIMIT page OFFSET k*page until a short page; LIMIT (total) still caps the extract."""
    base = _order_by(LIMIT_RE.sub("", q).rstrip("\n") + "\n")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    total, k, header = 0, 0, None
    with open(out_path, "wb") as out:
        while True:
            size = page if not limit else min(page, int(limit) - total)
            if size <= 0:
                break
            pq = base + f"LIMIT {size} OFFSET {k * page}\n"
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
