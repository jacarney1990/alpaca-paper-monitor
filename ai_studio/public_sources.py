"""Fixed-host, read-only SEC / openFDA / FRED public research adapters.

No brokerage methods, generated text, arbitrary URLs or spending. Every result
has official provenance and is labeled as research context, not trading advice.
"""
from __future__ import annotations

import csv
import io
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

FORM_TYPES = {"8-K", "10-Q", "10-K", "S-1", "S-3", "424B5", "424B2", "20-F", "6-K"}
FDA_DATASETS = {
    "drug_recalls": ("drug/enforcement.json", "product_description"),
    "device_recalls": ("device/enforcement.json", "product_description"),
    "drug_approvals": ("drug/drugsfda.json", "sponsor_name"),
}
FRED_SERIES = {
    "UNRATE": "US unemployment rate",
    "CPIAUCSL": "Consumer Price Index (all urban consumers)",
    "FEDFUNDS": "Effective federal funds rate",
    "DGS10": "10-year US Treasury yield",
    "GDP": "US gross domestic product",
    "PAYEMS": "Total nonfarm payrolls",
    "PCEPI": "Personal Consumption Expenditures Price Index",
    "INDPRO": "Industrial production index",
}
_CACHE = {}
_CACHE_LOCK = threading.Lock()
TTL_SECONDS = 900
MAX_BYTES = 2_000_000
SEC_AGENT = "NexusResearch/1.0 (https://github.com/jacarney1990/alpaca-paper-monitor)"
USER_AGENT = "NexusResearch/1.0 data research"


class SourceError(RuntimeError):
    """Sanitized source status; never includes API keys or raw responses."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _download(url, *, params=None, sec=False):
    # Callers MUST provide one of the fixed URLs composed below.
    headers = {"User-Agent": (
        os.getenv("SEC_USER_AGENT", "").strip() or SEC_AGENT
        if sec else USER_AGENT),
        "Accept": "application/json, text/csv"}
    try:
        with requests.get(url, params=params, headers=headers,
                          timeout=(4, 11), stream=True,
                          allow_redirects=False) as response:
            if response.status_code == 404:
                raise SourceError("no_results")
            if response.status_code == 429:
                raise SourceError("rate_limited")
            if response.status_code in (401, 403):
                raise SourceError("access_denied")
            if response.status_code != 200:
                raise SourceError("source_unavailable")
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise SourceError("response_too_large")
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException as exc:
        raise SourceError("network_unavailable") from exc


def _get_json(url, *, params=None, sec=False):
    import json
    try:
        return json.loads(_download(url, params=params, sec=sec).decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise SourceError("invalid_source_data") from exc


def _get_csv(url, *, params=None):
    try:
        return _download(url, params=params).decode("utf-8-sig")
    except UnicodeError as exc:
        raise SourceError("invalid_source_data") from exc


def _cache(key, fetch):
    now = time.monotonic()
    with _CACHE_LOCK:
        old = _CACHE.get(key)
        if old and now - old[0] < TTL_SECONDS:
            return dict(old[1], cached=True)
    result = fetch()
    if result.get("status") in ("ok", "no_results"):
        with _CACHE_LOCK:
            if len(_CACHE) >= 40:
                _CACHE.pop(next(iter(_CACHE)), None)
            _CACHE[key] = (time.monotonic(), result)
    return dict(result, cached=False)


def _result(source, status, items=(), *, note="", reference=""):
    return {
        "source": source, "status": status, "items": list(items)[:8],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "note": note, "reference": reference,
        "trading": "DISABLED", "ai_requests": 0,
    }


def _short(value, length=220):
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").strip()[:length]


def sec_filings(ticker):
    """List official EDGAR filings for a strictly validated ticker."""
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,7}", ticker):
        raise SourceError("invalid_ticker")

    def fetch():
        directory = _get_json("https://www.sec.gov/files/company_tickers.json", sec=True)
        records = directory.values() if isinstance(directory, dict) else directory
        cik = None
        company = ticker
        for row in records:
            if isinstance(row, dict) and row.get("ticker", "").upper() == ticker:
                cik = str(int(row["cik_str"])).zfill(10)
                company = _short(row.get("title", ticker), 100)
                break
        ref = "https://www.sec.gov/edgar/search/"
        if cik is None:
            return _result("SEC", "no_results", note="Ticker not found in SEC directory.",
                           reference=ref)
        raw = _get_json("https://data.sec.gov/submissions/CIK" + cik + ".json", sec=True)
        recent = raw.get("filings", {}).get("recent", {})
        entries = []
        for form, date, acc, doc in list(zip(
                recent.get("form", []), recent.get("filingDate", []),
                recent.get("accessionNumber", []),
                recent.get("primaryDocument", [])))[:140]:
            if form not in FORM_TYPES or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                continue
            if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", acc):
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,140}", doc):
                continue
            url = ("https://www.sec.gov/Archives/edgar/data/" + str(int(cik)) +
                   "/" + acc.replace("-", "") + "/" + doc)
            entries.append({
                "title": ticker + " • Form " + form,
                "date": date, "summary": company + " official SEC filing",
                "url": url, "id": "sec:" + acc + ":" + form,
                "authority": "SEC EDGAR",
            })
            if len(entries) == 8:
                break
        return _result("SEC", "ok" if entries else "no_results", entries,
                       note="Filing metadata only; open the original filing for details.",
                       reference="https://data.sec.gov/submissions/CIK" + cik + ".json")
    return _cache(("sec", ticker), fetch)


def _fda_phrase(value):
    phrase = value.strip()
    if not 2 <= len(phrase) <= 60 or not re.fullmatch(r"[A-Za-z0-9 .,()&'/-]+", phrase):
        raise SourceError("invalid_search")
    return phrase.replace("\\", "").replace('"', "")


def fda_records(query, dataset="drug_recalls"):
    if dataset not in FDA_DATASETS:
        raise SourceError("invalid_fda_dataset")
    phrase = _fda_phrase(query)
    endpoint, field = FDA_DATASETS[dataset]
    url = "https://api.fda.gov/" + endpoint
    params = {"search": field + ':"' + phrase + '"', "limit": 8}
    if dataset != "drug_approvals":
        params["sort"] = "report_date:desc"

    def fetch():
        remote = dict(params)
        key = os.getenv("OPENFDA_API_KEY", "").strip()
        if key:
            remote["api_key"] = key
        try:
            data = _get_json(url, params=remote)
        except SourceError as exc:
            if exc.code == "no_results":
                return _result("FDA", "no_results",
                               note="No matches in this official dataset.",
                               reference=url + "?" + urlencode(params))
            raise
        rows = data.get("results", [])
        entries = []
        for row in rows[:8]:
            if dataset == "drug_approvals":
                app_no = _short(row.get("application_number"), 40)
                sponsor = _short(row.get("sponsor_name"), 110)
                products = row.get("products") or []
                product = _short(products[0].get("brand_name", "") if products else "", 100)
                submissions = row.get("submissions") or []
                date = _short(submissions[-1].get("submission_status_date", "")
                              if submissions else "", 20)
                label = (product or app_no or sponsor or "Drug application")
                details = sponsor + (" • " + app_no if app_no else "")
                rid = app_no or label
            else:
                rid = _short(row.get("recall_number") or row.get("event_id"), 60)
                label = _short(row.get("product_description"), 110) or "FDA recall"
                details = (_short(row.get("reason_for_recall"), 170) + " • " +
                           _short(row.get("classification"), 25))
                date = _short(row.get("report_date") or row.get("recall_initiation_date"), 20)
            entries.append({
                "id": "fda:" + rid, "title": label, "summary": details,
                "date": date, "url": url + "?" + urlencode(params),
                "authority": "FDA openFDA", "dataset": dataset,
            })
        return _result("FDA", "ok" if entries else "no_results", entries,
                       note="Official reported records; not proof of current product safety, clinical efficacy or an investment catalyst.",
                       reference=url + "?" + urlencode(params))
    return _cache(("fda", dataset, phrase.lower()), fetch)


def fred_observations(series):
    """FRED official API if keyed; public FRED graph CSV otherwise."""
    series = series.strip().upper()
    if series not in FRED_SERIES:
        raise SourceError("unsupported_fred_series")
    ref = "https://fred.stlouisfed.org/series/" + series

    def fetch():
        key = os.getenv("FRED_API_KEY", "").strip()
        observations = []
        source_method = "FRED API" if key else "Public FRED CSV"
        if key:
            data = _get_json("https://api.stlouisfed.org/fred/series/observations",
                             params={"series_id": series, "api_key": key,
                                     "file_type": "json", "sort_order": "desc",
                                     "limit": 24})
            rows = list(reversed(data.get("observations", [])))
            observations = [(x.get("date"), x.get("value")) for x in rows]
        else:
            csv_text = _get_csv(
                "https://fred.stlouisfed.org/graph/fredgraph.csv",
                params={"id": series, "cosd": "2020-01-01"})
            reader = csv.DictReader(io.StringIO(csv_text))
            if not reader.fieldnames or series not in reader.fieldnames:
                raise SourceError("invalid_source_data")
            from collections import deque
            observations = list(deque(
                ((x.get("DATE") or x.get("observation_date"), x.get(series))
                 for x in reader), maxlen=24))
        items = []
        for date, value in observations:
            if not isinstance(value, str) or value in ("", ".") or not date:
                continue
            items.append({
                "id": "fred:" + series + ":" + date,
                "title": FRED_SERIES[series],
                "date": date, "value": value,
                "summary": series + " = " + value + " (unit depends on series)",
                "url": ref, "authority": "Federal Reserve Bank of St. Louis",
            })
        return _result("FRED", "ok" if items else "no_results", items[-8:],
                       note=source_method +
                       ". Values can be revised and may be delayed; check units and release dates on FRED.",
                       reference=ref)
    return _cache(("fred", series), fetch)


def source_status():
    return {
        "SEC": {"ready": True, "requires_key": False,
                "mode": "EDGAR public API, identified User-Agent"},
        "FDA": {"ready": True, "requires_key": False,
                "mode": "openFDA public API" +
                        (" (optional key saved)" if os.getenv("OPENFDA_API_KEY") else "")},
        "FRED": {"ready": True, "requires_key": False,
                 "mode": ("Official FRED API (key saved)" if os.getenv("FRED_API_KEY")
                          else "Public FRED CSV; optional FRED_API_KEY for API access")},
    }


def lookup(source, payload):
    if source == "SEC":
        return sec_filings(str(payload.get("ticker") or ""))
    if source == "FDA":
        return fda_records(str(payload.get("query") or ""),
                           str(payload.get("dataset") or "drug_recalls"))
    if source == "FRED":
        return fred_observations(str(payload.get("series") or ""))
    raise SourceError("unknown_source")
