"""
SECOP II (Colombia) answers every page of its portal with "403 Forbidden" to
visitors from outside Colombia, so the stored tender link looks broken to a
foreign user. The tender briefing now explains that and offers the tender's
published open-data record instead (opens anywhere).

- the API must hand the frontend the source's own id (external_ref)
- the frontend helper must only appear for SECOP II and must escape the id
"""

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parents[2] / "frontend" / "defence-opportunity-intelligence-app-v12.html"


def test_opportunity_detail_returns_the_sources_own_id(client, auth_headers, db_cursor):
    ref = f"CO1.REQ.test-{uuid.uuid4().hex[:8]}"
    db_cursor.execute(
        """insert into programmes (name, country, stage, naics_code, external_ref)
           values (%s, 'Testland', 'rfp_issued', '35421000', %s) returning id""",
        (f"Mechanical spare parts for military vehicles {uuid.uuid4().hex[:6]}", ref),
    )
    programme_id = db_cursor.fetchone()["id"]
    product_id = None
    try:
        product_id = client.post("/products", headers=auth_headers, json={
            "name": "extref product", "description": "spare parts and replacement parts supplier for military vehicles", "trl": 9,
        }).json()["id"]
        cands = client.post(f"/products/{product_id}/classify", headers=auth_headers).json()["candidates"]
        cap = next(c for c in cands if c["code"] == "SPARES.REPLACEMENT")
        client.post(f"/products/{product_id}/capabilities/{cap['capability_id']}/confirm", headers=auth_headers)
        matches = client.post(f"/products/{product_id}/match-programmes", headers=auth_headers).json()["matches"]
        mine = next(m for m in matches if m["programme_id"] == str(programme_id))

        detail = client.patch(f"/opportunities/{mine['opportunity_id']}", headers=auth_headers, json={})
        assert detail.status_code == 200, detail.text
        assert detail.json()["external_ref"] == ref
    finally:
        db_cursor.execute("delete from opportunities where programme_id = %s", (programme_id,))
        db_cursor.execute("delete from programmes where id = %s", (programme_id,))
        if product_id:
            client.delete(f"/products/{product_id}", headers=auth_headers)


HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[1], 'utf8');
const a = html.indexOf('const SECOP_OPEN_DATA');
const b = html.indexOf('function showTenderBriefing(opp){');
(0, eval)(html.slice(a, b));
const secop = {source_name: 'SECOP II (Colombia Compra Eficiente)', external_ref: 'CO1.REQ.10947838'};
console.log(JSON.stringify({
  secop: portalAccessHelp(secop),
  other: portalAccessHelp({source_name: 'SAM.gov Contract Opportunities API', external_ref: 'x'}),
  none: portalAccessHelp(null),
  noRef: portalAccessHelp({source_name: 'SECOP II (Colombia Compra Eficiente)'}),
  evil: portalAccessHelp({source_name: 'SECOP II (x)', external_ref: '"><script>alert(1)</script>'}),
}));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_help_appears_only_for_secop_and_escapes_the_id():
    proc = subprocess.run(["node", "-e", HARNESS, str(HTML)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert "Forbidden" in out["secop"]
    assert 'href="https://www.datos.gov.co/resource/p6dx-8zbt.json?id_del_proceso=CO1.REQ.10947838"' in out["secop"]
    assert out["other"] == "" and out["none"] == ""
    assert "datos.gov.co" not in out["noRef"] and "Forbidden" in out["noRef"], "still explains, without a broken link"
    assert "<script>" not in out["evil"] and "%3Cscript%3E" in out["evil"]
