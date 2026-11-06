"""Ed25519 approval-service tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.models.enums import CustomerOfferStatus, UnitOfMeasure
from src.models.schemas import CustomerOffer, CustomerOfferLine, q2, q4
from src.services.approval_service import (
    ApprovalError,
    ApprovalService,
    approval_payload,
    approval_payload_bytes,
    approval_payload_hash,
    approve_offer,
    generate_keypair,
    main,
    sign_offer,
    verify_offer_signature,
)

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def make_offer() -> CustomerOffer:
    cost = Decimal("10.0000")
    margin = Decimal("0.15")
    unit_price = q4(cost * (Decimal(1) + margin))
    line = CustomerOfferLine(
        position_number="1.1",
        description="Nitrilhandschuhe blau Gr. M",
        quantity=Decimal("10.0000"),
        unit=UnitOfMeasure.KARTON,
        cost_unit_price_net=cost,
        margin_rate=margin,
        unit_price_net=unit_price,
        total_net=q2(Decimal("10.0000") * unit_price),
    )
    net = line.total_net
    vat = q2(net * Decimal("0.19"))
    return CustomerOffer(
        offer_number="AN-2026-0001",
        customer_name="Stadt Muster",
        status=CustomerOfferStatus.DRAFT,
        lines=[line],
        margin_rate=margin,
        total_net=net,
        total_vat=vat,
        total_gross=q2(net + vat),
        valid_until=date(2026, 12, 31),
    )


def test_sign_and_verify_offer() -> None:
    private_hex, public_hex = generate_keypair()
    offer = make_offer()

    approved = sign_offer(offer, private_hex, approver="C. Mueller", approved_at=NOW)

    assert approved.status is CustomerOfferStatus.APPROVED
    assert approved.approved_by == "C. Mueller"
    assert len(approved.ed25519_approval_signature or "") == 128
    assert verify_offer_signature(approved, public_hex) is True
    assert ApprovalService(public_hex).verify(approved) is True


def test_verification_fails_after_payload_tampering() -> None:
    private_hex, public_hex = generate_keypair()
    approved = sign_offer(make_offer(), private_hex, approver="C. Mueller", approved_at=NOW)

    tampered = approved.model_copy(update={"customer_name": "Evil GmbH"})

    assert verify_offer_signature(tampered, public_hex) is False


def test_verification_fails_with_wrong_key() -> None:
    private_hex, _ = generate_keypair()
    _, other_public = generate_keypair()
    approved = sign_offer(make_offer(), private_hex, approver="C. Mueller", approved_at=NOW)

    assert verify_offer_signature(approved, other_public) is False


def test_approve_offer_accepts_external_signature() -> None:
    private_hex, public_hex = generate_keypair()
    offer = make_offer()
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    signature = private.sign(approval_payload_bytes(offer, approver="C. Mueller", approved_at=NOW)).hex()

    approved = approve_offer(
        offer,
        signature,
        approver="C. Mueller",
        public_key=public_hex,
        approved_at=NOW,
    )

    assert approved.status is CustomerOfferStatus.APPROVED
    assert verify_offer_signature(approved, public_hex) is True
    assert (
        approval_payload_hash(approved, approver="C. Mueller", approved_at=NOW)
        == approved.approval_payload_hash
    )


def test_payload_is_deterministic_and_sensitive_to_content() -> None:
    offer = make_offer()
    first = approval_payload_hash(offer, approver="C. Mueller", approved_at=NOW)
    second = approval_payload_hash(offer, approver="C. Mueller", approved_at=NOW)

    assert first == second
    altered = offer.model_copy(update={"customer_name": "Other"})
    assert approval_payload_hash(altered, approver="C. Mueller", approved_at=NOW) != first
    assert approval_payload(offer, approver="C. Mueller", approved_at=NOW)["approved_by"] == "C. Mueller"


def test_approval_rejects_bad_inputs() -> None:
    private_hex, public_hex = generate_keypair()
    offer = make_offer()
    signature = sign_offer(offer, private_hex, approver="C. Mueller", approved_at=NOW)

    with pytest.raises(ApprovalError, match="signature"):
        approve_offer(offer, "not-hex", approver="C. Mueller", public_key=public_hex, approved_at=NOW)
    with pytest.raises(ApprovalError, match="approver"):
        approval_payload(offer)
    with pytest.raises(ApprovalError, match="public key"):
        ApprovalService("bad-key")

    assert ApprovalService().configured is False
    assert signature.ed25519_approval_signature is not None


def test_cli_signs_and_verifies_offer(workspace_tmp) -> None:  # type: ignore[no-untyped-def]
    private_hex, public_hex = generate_keypair()
    offer = make_offer()
    offer_path = workspace_tmp / "offer.json"
    key_path = workspace_tmp / "approver.key"
    signed_path = workspace_tmp / "signed.json"
    offer_path.write_text(offer.model_dump_json(), encoding="utf-8")
    key_path.write_text(private_hex, encoding="utf-8")

    assert (
        main(
            [
                "sign",
                "--offer",
                str(offer_path),
                "--private-key-file",
                str(key_path),
                "--approver",
                "C. Mueller",
                "--output",
                str(signed_path),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "verify",
                "--offer",
                str(signed_path),
                "--public-key",
                public_hex,
            ]
        )
        == 0
    )
    assert main(["verify", "--offer", str(offer_path), "--public-key", public_hex]) == 1
