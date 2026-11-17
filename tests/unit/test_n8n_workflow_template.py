"""The checked-in n8n workflow must keep the signed relay contract visible."""

from __future__ import annotations

import json
from pathlib import Path

TEMPLATE = Path("deploy/n8n/rfq-supplier-workflow.json")


def test_n8n_rfq_workflow_template_has_signed_relay_nodes() -> None:
    workflow = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert set(nodes) >= {
        "RFQ Webhook",
        "Verify HMAC & Build Quote",
        "POST Signed Supplier Quote",
        "Acknowledge Yasmina",
    }

    webhook = nodes["RFQ Webhook"]
    assert webhook["parameters"]["path"] == "yasmina/rfq"
    assert webhook["parameters"]["httpMethod"] == "POST"
    assert webhook["parameters"]["options"]["rawBody"] is True

    code = nodes["Verify HMAC & Build Quote"]["parameters"]["jsCode"]
    assert "YASMINA_N8N_HMAC_SECRET" in code
    assert "timingSafeEqual" in code
    assert "X-Yasmina-Signature" not in code  # headers are read dynamically
    assert "envelope.callback_url" in code

    callback = nodes["POST Signed Supplier Quote"]["parameters"]
    assert callback["method"] == "POST"
    assert callback["url"] == "={{ $json.callback_url }}"
    header_names = {header["name"] for header in callback["headerParameters"]["parameters"]}
    assert {"Idempotency-Key", "X-Yasmina-Timestamp", "X-Yasmina-Signature"} <= header_names
    assert callback["sendBody"] is True
    assert callback["body"] == "={{ $json.body }}"
