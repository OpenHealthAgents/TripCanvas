# TripCanvas - ChatGPT Native App

TripCanvas provides:
- MCP endpoints for ChatGPT-native tool use and widget rendering.
- REST v1 travel endpoints for direct API integration.

Both MCP tools and REST endpoints share the same backend orchestration path.

## Prerequisites

- Node.js (v18+)
- Python (v3.10+)
- `ngrok` (for local development access)

## Setup & Running (Local)

### 1. Start the backend server
Navigate to the server directory, create a virtual environment, install dependencies, and start the server.

```bash
cd server
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```
The server will be running at `http://localhost:8000`.

### 2. Verify local endpoints
```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/openapi.json
```

### 3. Test REST v1 API
Example:
```bash
curl -X POST http://localhost:8000/v1/search_travel \
  -H "Content-Type: application/json" \
  -d '{
    "origin": {"iata":"LON","city":"London"},
    "destination": {"iata":"PAR","city":"Paris"},
    "dates": {"start_date":"2026-04-10","end_date":"2026-04-13"},
    "travelers": {"adults":1}
  }'
```

### 4. Expose to the Internet (optional for local dev)
Open a new terminal and use ngrok to create a public tunnel.

```bash
ngrok http 8000
```
Copy the public URL (e.g., `https://abc-123.ngrok-free.app`).

### 5. Set widget host for MCP/widget assets
Set environment variable with your public URL:
```bash
export APP_HOST="https://abc-123.ngrok-free.app"
```
Restart the Python server after setting this.

### 6. Configure ChatGPT
1. Go to **ChatGPT > Explore GPTs > Create > Configure**.
2. Scroll to **Actions** and click **Create New Action**.
3. Point to your MCP endpoint: `https://<your-host>/mcp`.
4. *Note:* For the Apps SDK "Native" experience, you will typically register this as an **MCP Connector** in the OpenAI Developer Portal.

## How it Works
1. **Trigger:** The user asks for a trip plan, flights, or activities.
2. **Tool Call:** ChatGPT calls `plan_trip` (with destination, origin, date), `search_flights`, or `search_activities` on your MCP server.
3. **Orchestration:** MCP tools internally call the same v1 orchestration handlers used by REST.
4. **Response:** The server returns trip data (plus structured content for widget rendering).
5. **Rendering:** ChatGPT renders the widget in the chat interface.

## REST v1 Endpoints
- `POST /v1/search_travel`
- `POST /v1/refine_results`
- `POST /v1/start_booking`
- `POST /v1/save_itinerary`
- `GET /v1/get_policy_summary/{offer_id}`

## Deploy on Render (Recommended)
This avoids ngrok browser warning pages that block ChatGPT widget rendering.

1. Push this repo to GitHub (already done).
2. In Render, create a new **Web Service** from this repo.
3. Render will detect `render.yaml` and deploy the Docker service.
4. In Render service settings, set env vars:
   - `AMADEUS_API_KEY`
   - `AMADEUS_API_SECRET`
   - `APP_HOST` = your Render service URL, e.g. `https://tripcanvas-mcp.onrender.com`
5. Redeploy after setting env vars.

Use this MCP endpoint in ChatGPT:
- `https://<your-render-domain>/mcp`

Use this REST base URL for integrations:
- `https://www.api.tripcanvas.site`
