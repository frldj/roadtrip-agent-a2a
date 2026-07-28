"""Tests d'enregistrement et d'utilisation des métriques Prometheus."""
import pytest
from prometheus_client import Counter, Gauge, Histogram

from monitoring.metrics import (
    agent_call_duration,
    agent_calls,
    cache_operations,
    llm_generation_rate,
    llm_requests,
    llm_tokens_generated,
    llm_ttft,
    llm_request_duration,
    plan_extractions,
    planning_duration,
    planning_requests,
    ws_connections_active,
    ws_messages,
)


class TestMetricTypes:
    def test_llm_histograms(self):
        assert isinstance(llm_request_duration, Histogram)
        assert isinstance(llm_ttft, Histogram)
        assert isinstance(llm_generation_rate, Histogram)

    def test_llm_counters(self):
        assert isinstance(llm_tokens_generated, Counter)
        assert isinstance(llm_requests, Counter)

    def test_planning_metrics(self):
        assert isinstance(planning_duration, Histogram)
        assert isinstance(planning_requests, Counter)
        assert isinstance(plan_extractions, Counter)

    def test_agent_metrics(self):
        assert isinstance(agent_call_duration, Histogram)
        assert isinstance(agent_calls, Counter)

    def test_websocket_metrics(self):
        assert isinstance(ws_connections_active, Gauge)
        assert isinstance(ws_messages, Counter)

    def test_cache_metrics(self):
        assert isinstance(cache_operations, Counter)


class TestMetricBehavior:
    def test_gauge_inc_dec(self):
        before = ws_connections_active._value.get()
        ws_connections_active.inc()
        assert ws_connections_active._value.get() == before + 1
        ws_connections_active.dec()
        assert ws_connections_active._value.get() == before

    def test_counter_increments(self):
        before = plan_extractions.labels(status="_test")._value.get()
        plan_extractions.labels(status="_test").inc()
        assert plan_extractions.labels(status="_test")._value.get() == before + 1

    def test_histogram_observe_does_not_raise(self):
        llm_request_duration.labels(model="_test", status="success").observe(1.23)
        llm_ttft.labels(model="_test").observe(0.42)
        agent_call_duration.labels(agent="route", status="success").observe(2.5)
        planning_duration.labels(status="success").observe(15.0)

    def test_cache_counter_labels(self):
        before = cache_operations.labels(cache="osrm", result="hit")._value.get()
        cache_operations.labels(cache="osrm", result="hit").inc()
        assert cache_operations.labels(cache="osrm", result="hit")._value.get() == before + 1

    def test_ws_messages_labels(self):
        before_in = ws_messages.labels(direction="inbound")._value.get()
        ws_messages.labels(direction="inbound").inc(3)
        assert ws_messages.labels(direction="inbound")._value.get() == before_in + 3
