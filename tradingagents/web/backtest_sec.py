"""Read NVIDIA's extension capex facts from the original SEC XBRL instance.

Companyfacts excludes issuer-specific tags. Never substitute a later filing's
comparatives. Raw instance and index are retained for reproducibility.
"""

import hashlib
import json
import xml.etree.ElementTree as ET

import requests

from tradingagents.dataflows.vendors.sec_edgar import _user_agent

CAPEX_TAGS = {
    "PurchasesOfPropertyAndEquipmentAndIntangibleAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets",
}


def extension_capex(xml, original, url):
    root = ET.fromstring(xml)
    ns = {"x": "http://www.xbrl.org/2003/instance"}
    contexts = {}
    for ctx in root.findall("x:context", ns):
        if ctx.find(".//x:segment", ns) is not None or ctx.find("x:scenario", ns) is not None:
            continue
        contexts[ctx.attrib["id"]] = (
            ctx.findtext("x:period/x:startDate", namespaces=ns),
            ctx.findtext("x:period/x:endDate", namespaces=ns),
        )
    matches = []
    for el in root:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in CAPEX_TAGS or contexts.get(el.get("contextRef")) != (
            original["start"],
            original["end"],
        ):
            continue
        unit = root.find(f"x:unit[@id='{el.get('unitRef')}']/x:measure", ns)
        if unit is None or unit.text.rsplit(":", 1)[-1] != "USD":
            continue
        matches.append(
            {
                "val": float(el.text),
                "tag": tag,
                "unit": "USD",
                "start": original["start"],
                "end": original["end"],
                "filed": original["filed"],
                "accn": original["accn"],
                "source_url": url,
                "source_sha256": hashlib.sha256(xml).hexdigest(),
            }
        )
    if not matches or len({m["val"] for m in matches}) != 1:
        raise ValueError("原始 XBRL 无唯一可核验的资本开支扩展字段")
    return matches[0]


def supplement(facts, cache):
    from tradingagents.web.valuation_backtest import annual_snapshots

    found, errors = {}, {}
    for snap in annual_snapshots(facts):
        if snap.get("error") != "缺少 capex":
            continue
        original = snap["evidence"]["revenue"]
        # Earliest supported test starts 2013; 2009/2010 originals are still
        # needed as lookback. Fetch each original once, then reuse immutable cache.
        base = (
            "https://www.sec.gov/Archives/edgar/data/1045810/"
            + original["accn"].replace("-", "")
            + "/"
        )
        folder = cache / "sec-originals" / original["accn"]
        folder.mkdir(parents=True, exist_ok=True)

        def fetch(name, folder=folder, base=base):
            path = folder / name
            if not path.exists():
                r = requests.get(base + name, headers={"User-Agent": _user_agent()}, timeout=30)
                r.raise_for_status()
                path.write_bytes(r.content)
            return path.read_bytes()

        try:
            listing = json.loads(fetch("index.json"))["directory"]["item"]
            candidates = [
                x["name"]
                for x in listing
                if x["name"].endswith(".xml")
                and not any(
                    x["name"].endswith(s) for s in ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml")
                )
                and x["name"] != "FilingSummary.xml"
            ]
            candidates = [n for n in candidates if n.startswith("nvda")]
            if len(candidates) != 1:
                raise ValueError("原始 XBRL 实例文件无法唯一识别")
            found[snap["end"]] = extension_capex(
                fetch(candidates[0]), original, base + candidates[0]
            )
        except Exception as exc:
            errors[snap["end"]] = str(exc)
    return found, errors
