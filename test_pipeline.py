"""Quick test: run the full pipeline and print results."""
import requests
import json
import sys

print("Starting analysis for TCS...")
r = requests.post(
    "http://127.0.0.1:8000/api/analyze",
    json={"company": "TCS"},
    stream=True,
    timeout=300,
)

for line in r.iter_lines(decode_unicode=True):
    if line.startswith("data: "):
        event = json.loads(line[6:])
        etype = event.get("type")
        if etype == "progress":
            stage = event.get("stage", "")
            msg = event.get("message", "")
            print(f"  [{stage}] {msg}")
        elif etype == "result":
            audit = event.get("data", {}).get("audit_report", {})
            print("\n=== EXTRACTION RESULT ===")
            print(f"Company: {audit.get('company_name', '?')}")
            opinion = audit.get("auditor_opinion", {})
            print(f"Opinion Type: {opinion.get('type', '?')}")
            print(f"Opinion Summary: {opinion.get('summary', '?')[:200]}")
            kams = audit.get("key_audit_matters", [])
            print(f"Key Audit Matters: {len(kams)}")
            for i, kam in enumerate(kams, 1):
                print(f"  KAM {i}: {kam.get('title', '?')}")
            sig = audit.get("signature_block", {})
            print(f"Audit Firm: {sig.get('audit_firm', '?')}")
            print(f"Partner: {sig.get('partner_name', '?')}")
            print(f"Date: {sig.get('report_date', '?')}")
            gc = audit.get("going_concern", {})
            print(f"Going Concern: {'Yes' if gc.get('material_uncertainty') else 'No'}")
            ifc = audit.get("internal_financial_controls", {})
            print(f"IFC Opinion: {ifc.get('opinion_type', '?')}")

            # Save full JSON
            with open("result.json", "w", encoding="utf-8") as f:
                json.dump(audit, f, indent=2, ensure_ascii=False)
            print("\nFull JSON saved to result.json")
        elif etype == "error":
            print(f"  ERROR: {event.get('message', '?')}")
            sys.exit(1)

print("\nDone!")
