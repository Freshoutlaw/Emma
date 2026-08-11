"""Tests for the real-data map resolution (no synthetic regions)."""

import asyncio
import types

from agents import map as map_mod
from agents.map import MapAgent, _curated_region, _unresolved_region, extract_region_name, match_region


def _agent(gate_open: bool = True) -> MapAgent:
    pipeline = types.SimpleNamespace(
        network_gate=types.SimpleNamespace(is_open=gate_open),
    )
    agent = MapAgent.__new__(MapAgent)
    agent.pipeline = pipeline
    return agent


def test_match_region_london_real_facts():
    region = match_region("show me a map of london")
    assert region is not None
    assert region["name"] == "London"
    assert region["country"] == "United Kingdom"
    assert abs(region["lat"] - 51.51) < 0.01
    assert abs(region["lon"] - (-0.13)) < 0.01


def test_match_region_tokyo():
    region = match_region("where is tokyo")
    assert region is not None
    assert region["name"] == "Tokyo"
    assert region["lat"] > 30


def test_extract_region_name_phrase():
    assert extract_region_name("weather in atlantis") == "atlantis"
    assert extract_region_name("where is buenos aires") == "buenos aires"


def test_curated_region_payload():
    region = _curated_region(match_region("london"))
    assert region["resolved"] is True
    assert region["source"] == "curated dataset"


def test_unresolved_region_is_honest():
    region = _unresolved_region("atlantis")
    assert region["resolved"] is False
    assert region["lat"] is None
    assert region["lon"] is None
    assert region["pop_m"] is None
    assert "could not be resolved" in region["desc"]


def test_geocode_parses_real_result(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "results": [
                    {
                        "name": "Atlantis",
                        "latitude": 33.1,
                        "longitude": -13.2,
                        "country": "Atlantic Ocean",
                        "country_code": "AO",
                        "timezone": "UTC",
                        "population": 1200000,
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(map_mod.httpx, "AsyncClient", FakeClient)
    region = asyncio.run(_agent()._geocode("atlantis"))
    assert region is not None
    assert region["resolved"] is True
    assert region["name"] == "Atlantis"
    assert region["lat"] == 33.1
    assert region["lon"] == -13.2
    assert region["pop_m"] == 1.2
    assert region["source"] == "Open-Meteo geocoding"


def test_geocode_respects_network_gate():
    assert asyncio.run(_agent(gate_open=False)._geocode("atlantis")) is None


def test_geocode_empty_results_returns_none(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            return FakeResponse()

    monkeypatch.setattr(map_mod.httpx, "AsyncClient", FakeClient)
    assert asyncio.run(_agent()._geocode("nowhere-city")) is None
