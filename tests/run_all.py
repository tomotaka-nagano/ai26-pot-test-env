"""Run all test scenarios via the REST API."""
import json
from urllib.request import urlopen, Request

BASE = "http://127.0.0.1:8000"

# Get scenarios list
files = json.loads(urlopen(f"{BASE}/api/scenarios").read())

total = 0
passed = 0
for f in files:
    filename = f["file"]
    url = f"{BASE}/api/scenarios/run/{filename}"
    req = Request(url, method="POST")
    resp = json.loads(urlopen(req).read())
    scenarios = resp.get("scenarios", [])
    print(f"  {filename} ({len(scenarios)} tests):")
    for sc in scenarios:
        total += 1
        status = sc.get("status", "?")
        mark = "PASS" if status == "passed" else "FAIL"
        name = sc.get("name", "?")
        if status == "passed":
            passed += 1
        print(f"    [{mark}] {name}: {status}")

print()
print(f"Result: {passed}/{total} passed")
