#!/usr/bin/env python3
"""Self-check for the skill package: structure, frontmatter, links, script syntax, examples, unit tests.

Usage: python scripts/validate_skill.py [--json]
Runs the tests without pytest (plain functions with asserts), so it works in a bare Python environment.
"""
from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import re
import subprocess
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = [
    "SKILL.md", "assets/strategy_registry.json",
    *[f"references/{n}.md" for n in ("market-data-policy", "setup-validation", "quantitative-research", "derivatives-analysis",
                                      "position-sizing", "bitget-integration", "execution-protocol", "portfolio-risk", "trade-output")],
    *[f"scripts/{n}.py" for n in ("validate_market_data", "calculate_position_size", "evaluate_setup", "evaluate_portfolio_risk",
                                   "backtest_strategy", "analyze_market", "validate_skill")],
    *[f"tests/{n}.py" for n in ("test_market_data", "test_position_sizing", "test_risk_limits", "test_no_trade")],
    "examples/valid_trade.json", "examples/rejected_trade.json",
]
SECRET_PATTERNS = [r"ACCESS-KEY\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}", r"bg_[a-f0-9]{24,}", r"(?i)passphrase\s*[:=]\s*['\"][^'\"]{6,}['\"]",
                   r"(?i)api[_-]?secret\s*[:=]\s*['\"][^'\"]{8,}['\"]"]
WRITE_PATTERNS = [r"place-order", r"cancel-order", r"close-positions", r"withdraw", r"method\s*=\s*['\"]POST"]


def check() -> dict:
    results, ok = [], True

    def add(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        results.append({"check": name, "passed": passed, "detail": detail})

    missing = [p for p in REQUIRED if not os.path.exists(os.path.join(ROOT, p))]
    add("structure: required files present", not missing, ", ".join(missing))

    text = open(os.path.join(ROOT, "SKILL.md")).read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep and not line.startswith(" "):
                fm[k.strip()] = v.strip()
    name, desc = fm.get("name", ""), fm.get("description", "")
    yaml_ok, yaml_detail = True, "checked with built-in rules"
    try:                                   # strict parse when PyYAML is installed
        import yaml
        parsed = yaml.safe_load(m.group(1)) if m else None
        yaml_ok = isinstance(parsed, dict) and isinstance(parsed.get("description"), str)
        yaml_detail = "PyYAML"
        if yaml_ok:
            name, desc = str(parsed.get("name", "")), parsed["description"]
    except ImportError:                    # fallback: an unquoted value must not contain ': ' or ' #'
        for k, v in fm.items():
            if not (v[:1] in "'\"" and v[-1:] == v[:1]) and (": " in v or " #" in v or v[:1] in "[{>|*&!%@`"):
                yaml_ok, yaml_detail = False, f"unquoted value of {k!r} contains YAML syntax"
    except Exception as exc:  # noqa: BLE001
        yaml_ok, yaml_detail = False, str(exc).splitlines()[0]
    add("frontmatter: valid YAML", yaml_ok, yaml_detail)
    add("frontmatter: parses, only name/description keys", bool(m) and set(fm) == {"name", "description"}, str(sorted(fm)))
    add("frontmatter: name is lowercase-hyphen, <= 64 chars, matches folder",
        bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name)) and len(name) <= 64 and name == os.path.basename(ROOT), name)
    add("frontmatter: description 1..1024 chars, no angle brackets", 0 < len(desc) <= 1024 and "<" not in desc, f"{len(desc)} chars")
    add("SKILL.md under 500 lines", text.count("\n") < 500, f"{text.count(chr(10))} lines")

    refs = set(re.findall(r"`((?:references|scripts|assets|examples|tests)/[A-Za-z0-9_\-./]+)`", text))
    for f in os.listdir(os.path.join(ROOT, "references")):
        refs |= set(re.findall(r"`((?:references|scripts|assets|examples|tests)/[A-Za-z0-9_\-./]+)`", open(os.path.join(ROOT, "references", f)).read()))
    broken = sorted(r for r in refs if not os.path.exists(os.path.join(ROOT, r)))
    add("links: every referenced file exists", not broken, ", ".join(broken))
    unlinked = [f for f in os.listdir(os.path.join(ROOT, "references")) if f"references/{f}" not in text]
    add("links: every reference file is reachable from SKILL.md", not unlinked, ", ".join(unlinked))

    bad = []
    for f in sorted(os.listdir(os.path.join(ROOT, "scripts"))):
        if f.endswith(".py"):
            try:
                py_compile.compile(os.path.join(ROOT, "scripts", f), doraise=True)
            except py_compile.PyCompileError as exc:
                bad.append(f"{f}: {exc.msg}")
    add("scripts: all compile", not bad, "; ".join(bad))

    leaks, writes = [], []
    for base, _, files in os.walk(ROOT):
        if "__pycache__" in base:
            continue
        for f in files:
            if f.endswith((".pyc", ".zip", ".skill")):
                continue
            p = os.path.join(base, f)
            body = open(p, errors="ignore").read()
            if any(re.search(pt, body) for pt in SECRET_PATTERNS):
                leaks.append(os.path.relpath(p, ROOT))
            if f.endswith(".py") and f != "validate_skill.py" and any(re.search(pt, body) for pt in WRITE_PATTERNS):
                writes.append(os.path.relpath(p, ROOT))
    add("safety: no credentials in any file", not leaks, ", ".join(leaks))
    add("safety: no order-placement or withdrawal calls in scripts", not writes, ", ".join(writes))

    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    try:
        ex_ok = json.load(open(os.path.join(ROOT, "examples/valid_trade.json")))
        ex_bad = json.load(open(os.path.join(ROOT, "examples/rejected_trade.json")))
        import calculate_position_size as cps, evaluate_portfolio_risk as epr, evaluate_setup as es, validate_market_data as vmd
        add("example valid_trade: data VERIFIED", vmd.validate(ex_ok["market_data"])["status"] == "VERIFIED")
        add("example valid_trade: account may open", epr.evaluate(ex_ok["account"])["can_open_new_position"])
        plan = cps.size(ex_ok["sizing_request"]).get("plan") or {}
        add("example valid_trade: sizing matches the hand-checked plan", all(plan.get(k) == v for k, v in ex_ok["expected_plan"].items()), json.dumps({k: plan.get(k) for k in ex_ok["expected_plan"]}))
        d = es.evaluate(ex_ok["setup"])["decision"]
        add("example valid_trade: decision today is WAIT (no validated strategy)", d == ex_ok["expected_decision_today"], d)
        add("example rejected_trade: data not VERIFIED", vmd.validate(ex_bad["market_data"])["status"] == ex_bad["expected_market_data_status"])
        add("example rejected_trade: account blocked", epr.evaluate(ex_bad["account"])["can_open_new_position"] is False)
        d = es.evaluate(ex_bad["setup"])["decision"]
        add("example rejected_trade: decision INSUFFICIENT DATA", d == ex_bad["expected_decision"], d)
        reg = json.load(open(os.path.join(ROOT, "assets/strategy_registry.json")))
        add("registry: statuses are valid", all(s.get("status") in ("VALIDATED", "RESEARCH_CANDIDATE", "REJECTED") for s in reg["strategies"]))
    except Exception:  # noqa: BLE001
        add("examples run through the scripts", False, traceback.format_exc(limit=2))

    passed = failed = 0
    failures = []
    for f in sorted(os.listdir(os.path.join(ROOT, "tests"))):
        if not (f.startswith("test_") and f.endswith(".py")):
            continue
        spec = importlib.util.spec_from_file_location(f[:-3], os.path.join(ROOT, "tests", f))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001
            failed += 1
            failures.append(f"{f}: import error: {traceback.format_exc(limit=1)}")
            continue
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            try:
                getattr(mod, name)()
                passed += 1
            except Exception:  # noqa: BLE001
                failed += 1
                failures.append(f"{f}::{name}: {traceback.format_exc(limit=1).strip().splitlines()[-1]}")
    add(f"unit tests: {passed} passed, {failed} failed", failed == 0 and passed > 0, "; ".join(failures))

    for script, arg in (("calculate_position_size.py", "examples/valid_trade.json"), ("evaluate_setup.py", "examples/rejected_trade.json")):
        doc = json.load(open(os.path.join(ROOT, arg)))
        payload = json.dumps(doc["sizing_request"] if "position" in script else doc["setup"])
        pr = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", script)], input=payload, capture_output=True, text=True)
        want = 0 if "position" in script else 2
        add(f"cli: {script} exit code {want} and JSON output", pr.returncode == want and pr.stdout.strip().startswith("{"), pr.stderr[-200:])

    return {"skill": name, "valid": ok, "checks_passed": sum(r["passed"] for r in results), "checks_total": len(results), "results": results}


if __name__ == "__main__":
    rep = check()
    if "--json" in sys.argv:
        print(json.dumps(rep, indent=2))
    else:
        for r in rep["results"]:
            print(("PASS  " if r["passed"] else "FAIL  ") + r["check"] + (f"  [{r['detail']}]" if r["detail"] and not r["passed"] else ""))
        print(f"\n{rep['checks_passed']}/{rep['checks_total']} checks passed. Skill valid: {rep['valid']}")
    sys.exit(0 if rep["valid"] else 1)
