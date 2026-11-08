"""Config-driven portal catalogue and engine factory tests."""

from __future__ import annotations

import pytest

from src.config import Settings
from src.scrapers.engines import (
    ENGINE_REGISTRY,
    CosinexEngineScraper,
    EngineScraperError,
    RibEngineScraper,
    StandardEVergabeScraper,
    build_engine_scraper,
    engine_class_for,
)
from src.scrapers.portal_config import (
    PortalConfigError,
    PortalTarget,
    load_portal_targets,
    parse_portal_targets,
)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=None,
        scraper_request_delay_seconds=0,
        scraper_user_agents=[],
        scraper_harvest_attachments=False,
    )


def test_default_catalogue_has_wide_coverage_and_verified_enabled_subset() -> None:
    enabled = load_portal_targets()
    all_targets = load_portal_targets(enabled_only=False)

    assert len(all_targets) >= 100
    assert [target.id for target in enabled] == ["bayern", "bund"]
    assert {target.id for target in all_targets} >= {
        "bayern",
        "bund",
        "nrw",
        "hessen",
        "charite",
        "ted",
    }
    assert next(target for target in all_targets if target.id == "bayern").engine == "rib"
    assert next(target for target in all_targets if target.id == "nrw").engine == "cosinex"
    ted = next(target for target in all_targets if target.id == "ted")
    assert ted.api is not None and ted.api.type == "ted"


def test_catalogue_defaults_and_metadata_are_applied() -> None:
    targets = parse_portal_targets(
        {
            "defaults": {"engine": "standard", "country": "DE", "enabled": False},
            "portals": [
                {
                    "id": "example",
                    "name": "Example portal",
                    "start_url": "https://example.test/vergabe",
                },
                {
                    "id": "active",
                    "name": "Active portal",
                    "start_url": "https://active.test/vergabe",
                    "engine": "cosinex",
                    "enabled": True,
                },
            ],
        },
        enabled_only=False,
    )

    assert [target.id for target in targets] == ["example", "active"]
    assert targets[0].engine == "standard"
    assert targets[0].country == "DE"
    assert targets[0].enabled is False
    assert targets[0].effective_domain == "example.test"
    assert targets[1].engine == "cosinex"
    assert (
        len(
            parse_portal_targets(
                {
                    "defaults": {"engine": "standard"},
                    "portals": [
                        {
                            "id": "active",
                            "name": "Active",
                            "start_url": "https://active.test/vergabe",
                            "enabled": True,
                        }
                    ],
                }
            )
        )
        == 1
    )


def test_catalogue_rejects_duplicates_and_invalid_urls() -> None:
    with pytest.raises(PortalConfigError, match="duplicate portal id"):
        parse_portal_targets(
            {
                "portals": [
                    {"id": "same", "name": "One", "start_url": "https://a.test/", "engine": "rib"},
                    {"id": "same", "name": "Two", "start_url": "https://b.test/", "engine": "rib"},
                ]
            },
            enabled_only=False,
        )

    with pytest.raises(PortalConfigError, match="absolute HTTP"):
        parse_portal_targets(
            {"portals": [{"id": "bad", "name": "Bad", "start_url": "file:///etc/passwd", "engine": "rib"}]},
            enabled_only=False,
        )


def test_engine_registry_and_factory_use_target_metadata() -> None:
    target = PortalTarget(
        id="nrw",
        name="e-Vergabe NRW",
        start_url="https://www.evergabe.nrw.de/startseite",
        engine="cosinex",
        portal_name="nrw",
        portal_code="NW",
        region="Nordrhein-Westfalen",
        request_delay_seconds=0.25,
    )

    scraper = build_engine_scraper(target, make_settings())

    assert isinstance(scraper, CosinexEngineScraper)
    assert scraper.portal_name == "nrw"
    assert scraper.portal_code == "NW"
    assert scraper.start_url == target.start_url
    assert scraper._request_delay == pytest.approx(0.25)
    assert engine_class_for("rib") is RibEngineScraper
    assert engine_class_for("standard") is StandardEVergabeScraper
    assert engine_class_for("xvergabe") is StandardEVergabeScraper
    with pytest.raises(EngineScraperError):
        engine_class_for("unknown-engine")
    assert set(ENGINE_REGISTRY) >= {"cosinex", "rib", "standard", "xvergabe", "dtvp"}


def test_engine_factory_preserves_legacy_bayern_identity() -> None:
    target = load_portal_targets(enabled_only=False)[0]
    scraper = build_engine_scraper(target, make_settings())

    assert scraper.portal_name == "bayern"
    assert scraper.portal_code == "BY"
    assert isinstance(scraper, RibEngineScraper)
