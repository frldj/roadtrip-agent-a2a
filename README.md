# Roadtrip A2A

> **Work in progress** — active development ongoing (AI route optimisation model, GPX export, multi-vehicle comparison…).

![CI](https://github.com/frldj/roadtrip-a2a/actions/workflows/ci.yml/badge.svg)

Road-trip planning assistant built on a **multi-agent A2A architecture**
([Agent2Agent protocol](https://a2a-protocol.org)): each concern
(routing, vehicle range, accommodation) is delegated to a dedicated, independently
deployed A2A agent.

Two interfaces are available: a **conversational chat** driven by a local LLM (Ollama)
in the terminal, and a **web UI** with an interactive map.

---

![Roadtrip A2A web interface](docs/screenshot.png)

*Conversational chat (left) · Leaflet/OSM map with charging stops and accommodation (right) · Clickable cards with direct links*

---

## Features

- **Routing**: Nominatim/OSM geocoding, OSRM route calculation, day-by-day split
- **EV charging**: battery discharge simulation, real charging stops via Overpass/OSM or Open Charge Map (optional key), Tesla Supercharger filter
- **Elevation**: consumption correction using a physics-based EV model (mass, motor efficiency, regenerative braking), SRTM 90 m data via OpenTopoData (free, no key)
- **Accommodation**: hotels and campsites via Google Places (optional key) or Overpass/OSM, with price range estimates
- **Streaming**: real-time progress via A2A `TaskStatusUpdateEvent` during long computations
- **In-memory cache**: OSRM routes, Overpass stops, OCM stops and elevation cached to avoid redundant API calls
- **LLM chat**: Ollama-powered conversational interface (local model) that collects trip parameters and triggers planning
- **Web UI**: interactive Leaflet/OSM map with clickable cards for charging stops and accommodation

## Architecture

```mermaid
graph TB
    subgraph Interfaces
        WEB["🌐 Web UI\npython -m web_server\nlocalhost:8765"]
        CLI["💬 Chat CLI\npython -m chat_client"]
        DIRECT["⌨️ Direct CLI\npython -m orchestrator"]
    end

    subgraph LLM["Local LLM"]
        OLLAMA["🤖 Ollama\nqwen2.5 / mistral…\nlocalhost:11434"]
    end

    subgraph Orchestration
        ORCH["🎯 orchestrator/core\nplan_roadtrip()"]
        SERVER["📡 A2A Server\nlocalhost:9000\n(optional)"]
    end

    subgraph Agents["Specialised A2A Agents"]
        ROUTE["🗺 Route Agent\nlocalhost:9011\nNominatim + OSRM"]
        VEHICLE["⚡ Vehicle Agent\nlocalhost:9012\nOverpass + OCM\n+ OpenTopoData"]
        ACCOM["🏨 Accommodation Agent\nlocalhost:9013\nGoogle Places / Overpass"]
    end

    subgraph APIs["External APIs (free)"]
        OSM["OpenStreetMap\nNominatim + OSRM\nOverpass"]
        TOPO["OpenTopoData\nSRTM 90m"]
        OCM["Open Charge Map\n(optional key)"]
        GPLACES["Google Places\n(optional key)"]
    end

    subgraph Monitoring["Observability (Docker)"]
        PROM["📊 Prometheus\nlocalhost:9090\nscrape every 10s"]
        GRAFANA["📈 Grafana\nlocalhost:3000\nLLM & agent dashboard"]
    end

    WEB -->|WebSocket streaming| OLLAMA
    CLI --> OLLAMA
    WEB --> ORCH
    CLI --> ORCH
    DIRECT --> ORCH
    ORCH --> ROUTE
    ORCH --> VEHICLE
    ORCH --> ACCOM
    ORCH -.->|exposed as agent| SERVER
    ROUTE --> OSM
    VEHICLE --> OSM
    VEHICLE --> TOPO
    VEHICLE --> OCM
    ACCOM --> GPLACES
    ACCOM --> OSM
    WEB -->|/metrics| PROM
    PROM --> GRAFANA
```

## Data Flow

```mermaid
sequenceDiagram
    participant U as User
    participant W as Web / Chat CLI
    participant LLM as Ollama (local LLM)
    participant O as Orchestrator
    participant R as Route Agent :9011
    participant V as Vehicle Agent :9012
    participant A as Accommodation Agent :9013

    U->>W: "Paris → Barcelona, Tesla, camping"
    W->>LLM: conversation (streaming tokens)
    LLM-->>W: clarifying questions (max 2)
    W-->>U: displays questions
    U->>W: answers questions
    W->>LLM: continues conversation
    LLM-->>W: PLAN_READY + JSON parameters
    Note over W: hides PLAN_READY in UI
    W->>O: plan_roadtrip(RoadtripRequest)
    O-->>W: ⏳ progress (streaming)
    O->>R: RoutePlanRequest → A2A DataPart
    R-->>O: RoutePlanResponse (segments + lat/lon)
    O->>V: ChargingRequest → A2A DataPart
    Note over V: SRTM elevation + Overpass/OCM stops
    V-->>O: ChargingPlanResponse (stops + warnings)
    O->>A: AccommodationRequest → A2A DataPart
    A-->>O: AccommodationPlanResponse (options + prices)
    O-->>W: complete RoadtripPlan (JSON)
    W-->>U: interactive map + clickable cards
    W->>LLM: "plan displayed, suggest adjustments"
    LLM-->>W: follow-up question (streaming)
    W-->>U: displays follow-up question
```

## APIs Used

| API | Usage | Key required |
|---|---|---|
| Nominatim / OSM | Geocoding | No |
| OSRM (public demo) | Route calculation | No |
| Overpass / OSM | Charging stops + accommodation | No |
| OpenTopoData SRTM 90m | Elevation for consumption correction | No |
| Ollama | Local LLM for conversational chat | No (local) |
| Open Charge Map | Enriched stop details (operator, connectors, kW) | Optional — free key at [openchargemap.org](https://openchargemap.org/site/develop) |
| Google Places | Accommodation with ratings and price levels | Optional — $200/month free credit at [console.cloud.google.com](https://console.cloud.google.com) |

## Installation

```bash
# Create and activate environment (conda or venv)
conda create -n roadtrip-a2a python=3.11
conda activate roadtrip-a2a

# Or with venv:
python3 -m venv .venv && source .venv/bin/activate

pip install -r requirements.txt

# For LLM chat and web UI — install Ollama
# https://ollama.com/download
ollama pull qwen2.5          # recommended lightweight model (~4.7 GB)
# or: ollama pull qwen2.5:7b

# Configure environment variables
cp .env.example .env
# Edit .env to add optional API keys
```

## Configuration (`.env`)

```dotenv
# Agent listening ports
ROUTE_AGENT_PORT=9011
VEHICLE_AGENT_PORT=9012
ACCOMMODATION_AGENT_PORT=9013

# Agent URLs as seen by the orchestrator
ROUTE_AGENT_URL=http://localhost:9011
VEHICLE_AGENT_URL=http://localhost:9012
ACCOMMODATION_AGENT_URL=http://localhost:9013

# Optional API keys
OPEN_CHARGE_MAP_KEY=   # enriched stop details (operator, connectors, power)
GOOGLE_PLACES_KEY=     # accommodation with real ratings and price levels
```

## Running

### Web UI + monitoring (recommended)

Start the 4 services in separate terminals (or with `&`):

```bash
# Terminal 1 — local LLM
ollama serve

# Terminal 2 — planning agents
python -m route_agent &
python -m vehicle_agent &
python -m accommodation_agent &

# Terminal 3 — web server (also exposes /metrics)
python -m web_server

# Terminal 4 — monitoring stack (requires Docker)
docker compose -f docker-compose.monitoring.yml up -d
```

| URL | Description |
|---|---|
| http://localhost:8765 | Web UI (chat + map) |
| http://localhost:8765/metrics | Raw Prometheus metrics |
| http://localhost:9090 | Prometheus UI |
| http://localhost:3000 | Grafana dashboard (admin / roadtrip) |

The **Roadtrip A2A — LLM & Agent Performance** dashboard opens automatically in Grafana.

### Stop monitoring

```bash
docker compose -f docker-compose.monitoring.yml down
# To also remove historical data:
docker compose -f docker-compose.monitoring.yml down -v
```

### Terminal chat

```bash
# (agents + ollama serve already running)
python -m chat_client

# Force a specific model
python -m chat_client --model qwen2.5:7b
# Or via environment variable
OLLAMA_MODEL=mistral python -m chat_client
```

### Direct CLI (no LLM)

```bash
# (agents already running)
python -m orchestrator \
    --origin Paris --destination Barcelona --waypoint Lyon \
    --vehicle electric --battery-kwh 60 --consumption 17 \
    --max-hours-per-day 5

# Raw JSON output
python -m orchestrator \
    --origin Paris --destination Nice --vehicle thermal --format json

# Tesla Supercharger only, camping
python -m orchestrator \
    --origin Paris --destination Barcelona --waypoint Bordeaux \
    --vehicle electric --battery-kwh 75 --consumption 16 \
    --tesla-supercharger --accommodation camping
```

### Stop agents

```bash
pkill -f "python -m route_agent"
pkill -f "python -m vehicle_agent"
pkill -f "python -m accommodation_agent"
```

### A2A server (optional)

```bash
python -m orchestrator.server   # exposes the orchestrator on :9000
```

## CLI options (`python -m orchestrator`)

| Option | Description | Default |
|---|---|---|
| `--origin` | Departure city | required |
| `--destination` | Arrival city | required |
| `--waypoint` | Intermediate stop (repeatable) | — |
| `--vehicle` | `electric` or `thermal` | `thermal` |
| `--battery-kwh` | Battery capacity in kWh | 60 (if electric) |
| `--consumption` | Consumption in kWh/100km | 17 (if electric) |
| `--max-hours-per-day` | Max driving hours per day | `6.0` |
| `--accommodation` | `hotel`, `camping`, `no_preference` | `no_preference` |
| `--budget` | Accommodation budget €/night | — |
| `--tesla-supercharger` | Search Tesla Superchargers only | `false` |
| `--start-date` | Departure date ISO (e.g. `2026-08-10`) | — |
| `--format` | `text` (readable) or `json` (raw) | `text` |

## Project Structure

```
roadtrip-a2a/
├── common/
│   ├── schemas.py          # Pydantic models shared between agents
│   ├── elevation.py        # Elevation correction via OpenTopoData (SRTM 90m)
│   ├── geocoding.py        # Nominatim geocoding with in-memory cache
│   ├── a2a_client_utils.py # Generic A2A HTTP client
│   └── a2a_data_utils.py   # DataPart + TaskStatusUpdateEvent helpers
├── route_agent/            # agent :9011 — OSRM routing
├── vehicle_agent/          # agent :9012 — charging stops + elevation
├── accommodation_agent/    # agent :9013 — accommodation
├── orchestrator/
│   ├── core.py             # direct orchestration (no server)
│   ├── __main__.py         # Python CLI
│   └── server.py           # exposed as A2A agent on :9000
├── chat_client/
│   ├── ollama_client.py    # Ollama client (streaming via /api/chat)
│   └── __main__.py         # conversational terminal chat
├── web_server/
│   ├── __main__.py         # FastAPI + WebSocket :8765 + /metrics
│   └── static/index.html   # SPA (Leaflet, chat, accommodation/charging cards)
├── monitoring/
│   ├── metrics.py          # Prometheus metric definitions (14 metrics)
│   ├── prometheus.yml      # Prometheus scrape config
│   └── grafana/
│       ├── provisioning/   # auto-provisioned datasource + dashboard
│       └── dashboards/roadtrip_a2a.json  # pre-wired dashboard
├── tests/                  # 76 tests (pytest, respx, asyncio)
├── docker-compose.monitoring.yml  # Prometheus :9090 + Grafana :3000
└── docs/
    └── screenshot.png      # web UI screenshot
```

## Technical Details

### Elevation and EV consumption

`common/elevation.py` queries OpenTopoData (SRTM 90 m) for 8 sampled points
between the start and end of each segment, then computes a correction factor:

```
baseline_kwh = consumption_kwh_100km / 100 × distance_km
extra_climb  = gain_m × 1800 kg × g / (motor_efficiency × 3_600_000)
saved_regen  = loss_m × 1800 kg × g × regen_efficiency / 3_600_000
factor       = (baseline + extra_climb − saved_regen) / baseline  [clamped 0.5–2.5]
```

A warning is added to the plan if the factor deviates more than 5 % from 1.

### In-memory cache

| Module | Cache | Key |
|---|---|---|
| `route_agent/core.py` | OSRM routes | `"lat,lon\|lat,lon"` |
| `vehicle_agent/core.py` | Overpass stops | `(lat×3, lon×3, tesla_only)` |
| `vehicle_agent/core.py` | OCM stops | `(lat×3, lon×3, tesla_only)` |
| `common/elevation.py` | Elevation points | `"lat,lon\|…\|n"` |
| `common/geocoding.py` | Nominatim coordinates | place name |

### Overpass rate limiting

Overpass requests are limited to 2 concurrent requests via `asyncio.Semaphore(2)`
to avoid 429 errors on multi-stop trips. Timeouts: 25 s per request, 30 s httpx connection.

### Web UI

The FastAPI server (`web_server/__main__.py`) exposes a `/ws/chat` WebSocket that:
1. Streams Ollama tokens to the frontend
2. Detects the `PLAN_READY:` marker in the LLM response (with fallback to any valid JSON block)
3. Hides the marker and JSON from the chat bubble
4. Calls `plan_roadtrip()` directly and sends the structured plan
5. Displays stops and accommodation on the Leaflet map

Clickable cards include Google Maps links for each stop/accommodation,
Tesla.com links for Superchargers, and CampingFrance/Booking.com links for accommodation.

### Observability (Prometheus + Grafana)

`monitoring/metrics.py` defines 14 metrics instrumented via `prometheus_client` and
exposed at `GET /metrics` on the web server. Prometheus scrapes this endpoint every 10 s;
Grafana visualises in real time.

```mermaid
graph LR
    APP["web_server :8765\n(ollama_client + orchestrator)"]
    MET["/metrics\nPrometheus format"]
    PROM["Prometheus :9090\nscrape 10s"]
    GRAF["Grafana :3000\nauto-provisioned dashboard"]

    APP -->|instruments| MET
    PROM -->|scrapes| MET
    PROM --> GRAF
```

**Available metrics:**

| Category | Metric | Type | Labels |
|---|---|---|---|
| LLM | `roadtrip_llm_request_duration_seconds` | Histogram | `model`, `status` |
| LLM | `roadtrip_llm_time_to_first_token_seconds` | Histogram | `model` |
| LLM | `roadtrip_llm_tokens_generated_total` | Counter | `model` |
| LLM | `roadtrip_llm_tokens_per_second` | Histogram | `model` |
| LLM | `roadtrip_llm_requests_total` | Counter | `model`, `status` |
| LLM | `roadtrip_llm_conversation_turns_total` | Counter | `model` |
| Planning | `roadtrip_planning_duration_seconds` | Histogram | `status` |
| Planning | `roadtrip_planning_requests_total` | Counter | `status` |
| Planning | `roadtrip_plan_extractions_total` | Counter | `status` |
| A2A Agents | `roadtrip_agent_call_duration_seconds` | Histogram | `agent`, `status` |
| A2A Agents | `roadtrip_agent_calls_total` | Counter | `agent`, `status` |
| WebSocket | `roadtrip_websocket_connections_active` | Gauge | — |
| WebSocket | `roadtrip_websocket_messages_total` | Counter | `direction` |
| Cache | `roadtrip_cache_operations_total` | Counter | `cache`, `result` |

**Grafana dashboard** (`monitoring/grafana/dashboards/roadtrip_a2a.json`) — 4 rows:
- **Overview**: LLM requests/min, active connections, plans generated, success rate
- **LLM Performance**: duration p50/p95/p99, TTFT p50/p95, tokens/s rate
- **Planning & Agents**: duration by agent (route / vehicle / accommodation), call rates
- **WebSocket & Errors**: connections, message throughput, LLM and agent error rates

### Message schemas (`common/schemas.py`)

| Request | Response | Agent |
|---|---|---|
| `RoutePlanRequest` | `RoutePlanResponse` (+ `RouteSegment` with lat/lon) | Route Agent |
| `ChargingRequest` | `ChargingPlanResponse` (+ elevation warnings) | Vehicle Agent |
| `AccommodationRequest` | `AccommodationPlanResponse` | Accommodation Agent |
| `RoadtripRequest` | `RoadtripPlan` | Orchestrator |

`RouteSegment` embeds geocoded coordinates (`start_lat/lon`, `end_lat/lon`),
avoiding re-geocoding in downstream agents and enabling interpolation of
charging stop positions along the route.

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

76 tests across 7 modules: schema validation, EV physics model, Haversine distance,
Nominatim geocoding (HTTP-mocked), LLM plan extraction (including fallback), A2A
message helpers, and Prometheus metrics.

## Roadmap

Planned features (not yet implemented):

- **AI route optimisation model**: replace fixed day-splitting with a model that optimises the trip under constraints (charging time, driver fatigue, weather, electricity prices)
- **GPX export**: download the complete itinerary for GPS/phone
- **Multi-vehicle comparison**: compare several vehicles side by side (range, cost)
- **Weather integration**: incorporate weather forecasts along the route to adjust EV consumption (cold, wind)
- **Real-time electricity prices**: OCM and Chargemap sometimes provide per-network tariffs
- **Plan persistence**: save and share a plan via URL
- **Mobile app**: PWA or React Native wrapper around the web UI
