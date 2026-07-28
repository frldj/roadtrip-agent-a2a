"""Tests de l'extraction de plan depuis la réponse LLM.

Couvre les deux chemins :
  - avec marqueur PLAN_READY: (format attendu)
  - sans marqueur (fallback bloc ```json```)
"""
from web_server.__main__ import _extract_plan

_FULL_JSON = """{
    "origin": "Angers",
    "destination": "Bayonne",
    "waypoints": [],
    "vehicle_type": "electric",
    "battery_capacity_kwh": 51.0,
    "consumption_kwh_per_100km": 17.0,
    "max_driving_hours_per_day": 4.0,
    "accommodation_type": "camping",
    "budget_per_night_eur": null,
    "start_date": null,
    "tesla_supercharger_only": false
}"""

_MINIMAL_JSON = '{"origin": "Paris", "destination": "Lyon", "vehicle_type": "electric"}'


class TestExtractWithMarker:
    def test_plan_ready_fenced_block(self):
        text = f"PLAN_READY:\n```json\n{_FULL_JSON}\n```"
        req = _extract_plan(text)
        assert req is not None
        assert req.origin == "Angers"
        assert req.destination == "Bayonne"

    def test_plan_ready_bare_json(self):
        text = f"PLAN_READY:\n{_FULL_JSON}"
        req = _extract_plan(text)
        assert req is not None
        assert req.origin == "Angers"

    def test_plan_ready_with_prefix_text(self):
        text = f"Super, voilà votre plan !\nPLAN_READY:\n```json\n{_MINIMAL_JSON}\n```"
        req = _extract_plan(text)
        assert req is not None
        assert req.origin == "Paris"


class TestExtractFallback:
    """Le LLM produit le bon JSON sans le marqueur PLAN_READY:."""

    def test_fenced_block_without_marker(self):
        text = f"Voici la configuration finale :\n```json\n{_FULL_JSON}\n```\nPLAN"
        req = _extract_plan(text)
        assert req is not None
        assert req.origin == "Angers"
        assert req.destination == "Bayonne"

    def test_minimal_fields_fallback(self):
        text = f"```json\n{_MINIMAL_JSON}\n```"
        req = _extract_plan(text)
        assert req is not None
        assert req.vehicle_type.value == "electric"

    def test_thermal_vehicle_fallback(self):
        j = '{"origin": "Paris", "destination": "Nice", "vehicle_type": "thermal"}'
        req = _extract_plan(f"```json\n{j}\n```")
        assert req is not None
        assert req.vehicle_type.value == "thermal"

    def test_tesla_supercharger_only(self):
        j = ('{"origin": "A", "destination": "B", "vehicle_type": "electric",'
             ' "tesla_supercharger_only": true}')
        req = _extract_plan(f"```json\n{j}\n```")
        assert req is not None
        assert req.tesla_supercharger_only is True


class TestExtractReturnsNone:
    def test_no_json_at_all(self):
        assert _extract_plan("Bonjour, où souhaitez-vous aller ?") is None

    def test_json_missing_required_fields(self):
        assert _extract_plan('```json\n{"foo": "bar"}\n```') is None

    def test_invalid_vehicle_type(self):
        j = '{"origin": "A", "destination": "B", "vehicle_type": "bike"}'
        assert _extract_plan(f"```json\n{j}\n```") is None

    def test_empty_origin(self):
        j = '{"origin": "", "destination": "B", "vehicle_type": "electric"}'
        assert _extract_plan(f"```json\n{j}\n```") is None

    def test_empty_destination(self):
        j = '{"origin": "A", "destination": "", "vehicle_type": "electric"}'
        assert _extract_plan(f"```json\n{j}\n```") is None

    def test_malformed_json(self):
        assert _extract_plan("```json\n{origin: Angers}\n```") is None

    def test_empty_string(self):
        assert _extract_plan("") is None
