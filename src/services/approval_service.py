"""Ed25519 approval service for customer offers.

The server stores only the public key. A human approver signs the canonical
offer payload with the private key; the service verifies that signature and
produces the approved ``CustomerOffer`` used by GAEB/ERPNext exports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import ValidationError

from src.models.enums import CustomerOfferStatus
from src.models.schemas import CustomerOffer

__all__ = [
    "ApprovalError",
    "ApprovalService",
    "approval_payload",
    "approval_payload_bytes",
    "approval_payload_hash",
    "approve_offer",
    "generate_keypair",
    "main",
    "sign_offer",
    "verify_offer_signature",
]

HEX128 = 128


class ApprovalError(ValueError):
    """Raised when approval payloads, keys or signatures are invalid."""


def _line_payload(line: Any, *, include_vat: bool) -> dict[str, str]:
    payload = {
        "position_number": line.position_number,
        "description": line.description,
        "quantity": str(line.quantity),
        "unit": line.unit.value,
        "cost_unit_price_net": str(line.cost_unit_price_net),
        "margin_rate": str(line.margin_rate),
        "unit_price_net": str(line.unit_price_net),
        "total_net": str(line.total_net),
    }
    if include_vat:
        payload["vat_rate"] = str(line.vat_rate)
    return payload


def approval_payload(
    offer: CustomerOffer,
    *,
    approver: str | None = None,
    approved_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical, deterministic payload a human signs."""

    approver_value = approver if approver is not None else offer.approved_by
    approved_at_value = approved_at if approved_at is not None else offer.approved_at
    if not approver_value:
        raise ApprovalError("approver is required for the approval payload")
    if approved_at_value is None:
        raise ApprovalError("approved_at is required for the approval payload")
    if approved_at_value.tzinfo is None:
        raise ApprovalError("approved_at must be timezone-aware")

    return {
        "offer_id": str(offer.id),
        "offer_number": offer.offer_number,
        "customer_name": offer.customer_name,
        "customer_reference": offer.customer_reference,
        "tender_id": str(offer.tender_id) if offer.tender_id is not None else None,
        "currency": offer.currency.value,
        "margin_rate": str(offer.margin_rate),
        "total_net": str(offer.total_net),
        "vat_rate": str(offer.vat_rate),
        "total_vat": str(offer.total_vat),
        "total_gross": str(offer.total_gross),
        "valid_until": offer.valid_until.isoformat(),
        "lines": [
            _line_payload(
                line,
                include_vat=len({item.vat_rate for item in offer.lines}) > 1,
            )
            for line in sorted(offer.lines, key=lambda line: line.position_number)
        ],
        "approved_by": approver_value,
        "approved_at": approved_at_value.astimezone(timezone.utc).isoformat(),
    }


def approval_payload_bytes(
    offer: CustomerOffer,
    *,
    approver: str | None = None,
    approved_at: datetime | None = None,
) -> bytes:
    payload = approval_payload(offer, approver=approver, approved_at=approved_at)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def approval_payload_hash(
    offer: CustomerOffer,
    *,
    approver: str | None = None,
    approved_at: datetime | None = None,
) -> str:
    return hashlib.sha256(
        approval_payload_bytes(offer, approver=approver, approved_at=approved_at)
    ).hexdigest()


def _public_key(value: str | Ed25519PublicKey | None) -> Ed25519PublicKey:
    if value is None:
        raise ApprovalError("no Ed25519 public key configured")
    if isinstance(value, Ed25519PublicKey):
        return value
    try:
        raw = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise ApprovalError("Ed25519 public key must be hex encoded") from exc
    if len(raw) != 32:
        raise ApprovalError("Ed25519 public key must be 32 bytes (64 hex characters)")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise ApprovalError("invalid Ed25519 public key") from exc


def _private_key(value: str | Ed25519PrivateKey) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    text = value.strip()
    if "BEGIN" in text:
        try:
            key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
        except Exception as exc:
            raise ApprovalError("invalid PEM Ed25519 private key") from exc
        if not isinstance(key, Ed25519PrivateKey):
            raise ApprovalError("private key is not Ed25519")
        return key
    try:
        raw = bytes.fromhex(text)
    except ValueError as exc:
        raise ApprovalError("Ed25519 private key must be hex or PEM encoded") from exc
    if len(raw) != 32:
        raise ApprovalError("Ed25519 private key seed must be 32 bytes (64 hex characters)")
    return Ed25519PrivateKey.from_private_bytes(raw)


def generate_keypair() -> tuple[str, str]:
    """Generate ``(private_seed_hex, public_key_hex)`` for local tooling/tests."""

    private = Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    public_hex = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    return private_hex, public_hex


def _with_approval(
    offer: CustomerOffer,
    *,
    signature: str,
    payload_hash: str,
    approver: str,
    approved_at: datetime,
    status: CustomerOfferStatus = CustomerOfferStatus.APPROVED,
) -> CustomerOffer:
    data = offer.model_dump()
    data.update(
        {
            "status": status,
            "ed25519_approval_signature": signature,
            "approval_payload_hash": payload_hash,
            "approved_by": approver,
            "approved_at": approved_at,
        }
    )
    return CustomerOffer.model_validate(data)


def sign_offer(
    offer: CustomerOffer,
    private_key: str | Ed25519PrivateKey,
    *,
    approver: str,
    approved_at: datetime | None = None,
    status: CustomerOfferStatus = CustomerOfferStatus.APPROVED,
) -> CustomerOffer:
    """Sign an offer and return the approved model (trusted approver tooling)."""

    timestamp = approved_at or datetime.now(timezone.utc)
    payload = approval_payload_bytes(offer, approver=approver, approved_at=timestamp)
    signature = _private_key(private_key).sign(payload).hex()
    return _with_approval(
        offer,
        signature=signature,
        payload_hash=hashlib.sha256(payload).hexdigest(),
        approver=approver,
        approved_at=timestamp,
        status=status,
    )


def verify_offer_signature(
    offer: CustomerOffer,
    public_key: str | Ed25519PublicKey | None = None,
) -> bool:
    """Verify the offer's signature and payload hash. Returns ``False`` on mismatch."""

    if not offer.ed25519_approval_signature or not offer.approval_payload_hash:
        return False
    if not offer.approved_by or offer.approved_at is None:
        return False
    try:
        payload = approval_payload_bytes(
            offer,
            approver=offer.approved_by,
            approved_at=offer.approved_at,
        )
        expected_hash = hashlib.sha256(payload).hexdigest()
        if expected_hash != offer.approval_payload_hash:
            return False
        signature = bytes.fromhex(offer.ed25519_approval_signature)
        if len(signature) != 64:
            return False
        _public_key(public_key).verify(signature, payload)
    except (ApprovalError, InvalidSignature, ValueError):
        return False
    return True


def approve_offer(
    offer: CustomerOffer,
    signature: str,
    *,
    approver: str,
    public_key: str | Ed25519PublicKey | None = None,
    approved_at: datetime | None = None,
    status: CustomerOfferStatus = CustomerOfferStatus.APPROVED,
) -> CustomerOffer:
    """Apply an externally produced signature after verifying it."""

    if len(signature) != HEX128:
        raise ApprovalError("Ed25519 signature must be 128 lowercase hex characters")
    try:
        signature_bytes = bytes.fromhex(signature)
    except ValueError as exc:
        raise ApprovalError("Ed25519 signature must be hex encoded") from exc

    timestamp = approved_at or datetime.now(timezone.utc)
    payload = approval_payload_bytes(offer, approver=approver, approved_at=timestamp)
    try:
        _public_key(public_key).verify(signature_bytes, payload)
    except InvalidSignature as exc:
        raise ApprovalError("Ed25519 signature does not match the offer payload") from exc

    return _with_approval(
        offer,
        signature=signature.lower(),
        payload_hash=hashlib.sha256(payload).hexdigest(),
        approver=approver,
        approved_at=timestamp,
        status=status,
    )


class ApprovalService:
    """Public-key verifier bound to one configured Ed25519 key."""

    def __init__(self, public_key_hex: str | None = None) -> None:
        self._public_key = _public_key(public_key_hex) if public_key_hex else None

    @property
    def configured(self) -> bool:
        return self._public_key is not None

    def verify(self, offer: CustomerOffer) -> bool:
        if self._public_key is None:
            return False
        return verify_offer_signature(offer, self._public_key)

    def approve(
        self,
        offer: CustomerOffer,
        signature: str,
        *,
        approver: str,
        approved_at: datetime | None = None,
    ) -> CustomerOffer:
        if self._public_key is None:
            raise ApprovalError("no Ed25519 public key configured")
        return approve_offer(
            offer,
            signature,
            approver=approver,
            public_key=self._public_key,
            approved_at=approved_at,
        )


def _load_offer(path: Path) -> CustomerOffer:
    try:
        return CustomerOffer.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ApprovalError(f"cannot load offer {path}: {exc}") from exc


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yasmina-approval",
        description="Sign or verify a CustomerOffer with an Ed25519 approval key.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sign = subparsers.add_parser("sign", help="sign an offer JSON with a private key")
    sign.add_argument("--offer", type=Path, required=True)
    sign.add_argument("--private-key-file", type=Path, required=True)
    sign.add_argument("--approver", required=True)
    sign.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="verify a signed offer JSON")
    verify.add_argument("--offer", type=Path, required=True)
    verify.add_argument("--public-key", required=True, help="64 lowercase hex characters")

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "sign":
            offer = _load_offer(args.offer)
            private_key = args.private_key_file.read_text(encoding="utf-8")
            approved = sign_offer(offer, private_key, approver=args.approver)
            args.output.write_text(approved.model_dump_json(indent=2), encoding="utf-8")
            print(f"Signed {offer.offer_number} -> {args.output}")
            return 0

        offer = _load_offer(args.offer)
        if verify_offer_signature(offer, args.public_key):
            print(f"Signature valid for offer {offer.offer_number}")
            return 0
        print(f"Signature INVALID for offer {offer.offer_number}", file=sys.stderr)
        return 1
    except (ApprovalError, OSError) as exc:
        print(f"approval error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
