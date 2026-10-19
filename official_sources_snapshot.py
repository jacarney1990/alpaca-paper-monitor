"""Update a public, dated snapshot of official SEC and FRED research data.

Run from GitHub Actions only. No AI, brokerage, orders, model credentials,
portfolio holdings or personal financial information are read or published.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import intelligence as base

OUTPUT = Path("data/official_sources_snapshot.json")
SERIES = ("UNRATE", "FEDFUNDS", "CPIAUCSL", "DGS10", "GDP", "PAYEMS")
SEC_FORMS = {"8-K", "10-K", "10-Q", "S-1", "S-3", "424B5", "20-F", "6-K"}
MAX_BYTES = 1_800_000


def _fred_text(url):
    req = Request(url, headers={"User-Agent": "NexusOfficialDataSnapshot/1.0",
                                "Accept": "text/csv"})
    try:
        with urlopen(req, timeout=20) as resp:
            raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("FRED_CSV_TOO_LARGE")
        return raw.decode("utf-8-sig")
    except (HTTPError, URLError, TimeoutError, UnicodeError, OSError) as exc:
        raise ValueError("FRED_CSV_UNAVAILABLE") from exc


def sec_snapshot():
    agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not agent:
        return {"status": "missing_user_agent", "filings": {}, "covered": 0, "failed": 0}
    headers = {"User-Agent": agent, "Accept": "application/json"}
    try:
        directory = base.request_json("https://www.sec.gov/files/company_tickers.json",
                                      headers, retries=0)
    except (HTTPError, URLError, ValueError):
        return {"status": "source_unavailable", "filings": {}, "covered": 0, "failed": 0}
    lookup = {}
    for row in directory.values():
        if isinstance(row, dict) and row.get("ticker") and row.get("cik_str"):
            lookup[row["ticker"].upper()] = str(int(row["cik_str"])).zfill(10)
    results, covered, failed = {}, 0, 0
    for ticker in base.SYMBOLS:
        cik = lookup.get(ticker)
        if not cik:
            continue
        try:
            doc = base.request_json("https://data.sec.gov/submissions/CIK" + cik + ".json",
                                    headers, retries=0)
            recent = doc.get("filings", {}).get("recent", {})
            filings = []
            for form, date, accession, filename in list(zip(
                    recent.get("form", []), recent.get("filingDate", []),
                    recent.get("accessionNumber", []),
                    recent.get("primaryDocument", [])))[:180]:
                if form not in SEC_FORMS:
                    continue
                if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or "") and
                        re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession or "") and
                        re.fullmatch(r"[A-Za-z0-9_.-]{1,130}", filename or "")):
                    continue
                url = ("https://www.sec.gov/Archives/edgar/data/" + str(int(cik)) +
                       "/" + accession.replace("-", "") + "/" + quote(filename, safe=""))
                filings.append({"symbol": ticker, "form": form, "date": date,
                                "accession": accession, "title": ticker + " • Form " + form,
                                "url": url})
                if len(filings) >= 8:
                    break
            results[ticker] = filings
            covered += 1
        except (HTTPError, URLError, ValueError, KeyError, TypeError):
            failed += 1
    return {"status": "ok" if covered and not failed else
            "partial" if covered else "source_unavailable",
            "filings": results, "covered": covered, "failed": failed}


def fred_snapshot():
    key = os.getenv("FRED_API_KEY", "").strip()
    series = {}
    failures = []
    for name in SERIES:
        try:
            if key:
                result = base.request_json(
                    "https://api.stlouisfed.org/fred/series/observations?" +
                    urlencode({"series_id": name, "api_key": key,
                               "file_type": "json", "sort_order": "desc", "limit": 16}),
                    retries=0)
                observations = list(reversed(result.get("observations", [])))
                points = [(row.get("date"), row.get("value")) for row in observations]
            else:
                raw = _fred_text("https://fred.stlouisfed.org/graph/fredgraph.csv?" +
                                 urlencode({"id": name, "cosd": "2022-01-01"}))
                reader = csv.DictReader(io.StringIO(raw))
                if not reader.fieldnames or name not in reader.fieldnames:
                    raise ValueError("FRED_FORMAT_UNAVAILABLE")
                from collections import deque
                points = list(deque(((row.get("DATE") or row.get("observation_date"),
                                      row.get(name)) for row in reader), maxlen=16))
            series[name] = [{"date": date, "value": value}
                            for date, value in points
                            if date and isinstance(value, str) and value not in ("", ".")][-8:]
        except (HTTPError, URLError, ValueError, KeyError, TypeError):
            failures.append(name)
    return {
        "status": "ok" if series and not failures else
                  "partial" if series else "source_unavailable",
        "series": series, "failed": failures,
        "method": "FRED official JSON API" if key else "Public FRED graph CSV",
    }


def _preserve_prior(prior, new, source):
    """Keep previously verified observations, but label them stale on outage."""
    if new["status"] in ("ok", "partial"):
        return dict(new, stale=False)
    old = prior.get(source) if isinstance(prior, dict) else None
    if (isinstance(old, dict) and old.get("status") in ("ok", "partial") and
            (old.get("filings") if source == "sec" else old.get("series"))):
        return dict(old, stale=True, latest_refresh_status=new["status"])
    return dict(new, stale=True)


def build_snapshot(old=None, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Snapshot clock must be timezone-aware")
    old = old or {}
    return {
        "schema": 1,
        "updated_at": now.astimezone(timezone.utc).isoformat(),
        "sec": _preserve_prior(old, sec_snapshot(), "sec"),
        "fred": _preserve_prior(old, fred_snapshot(), "fred"),
        "read_only": True,
        "model_inference_calls": 0,
        "brokerage_orders": 0,
        "note": "Official source metadata only; figures may be revised, delayed or incomplete.",
    }


def main():
    old = {}
    if OUTPUT.exists():
        try:
            old = json.loads(OUTPUT.read_text(encoding="utf-8"))
            if old.get("schema") != 1:
                old = {}
        except (ValueError, OSError, AttributeError):
            old = {}
    result = build_snapshot(old=old)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print("OFFICIAL_SNAPSHOT SEC=%s covered=%d FRED=%s series=%d "
          "model_calls=0 brokerage_orders=0" %
          (result["sec"]["status"], result["sec"].get("covered", 0),
           result["fred"]["status"], len(result["fred"].get("series", {}))))


if __name__ == "__main__":
    main()
