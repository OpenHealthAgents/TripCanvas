# TripCanvas - ChatGPT Native App

This project demonstrates a ChatGPT Native App using the OpenAI Apps SDK and Model Context Protocol (MCP).

## Prerequisites

- Node.js (v18+)
- Python (v3.10+)
- `ngrok` (for local development access)

## Setup & Running

### 1. Build the Frontend
Navigate to the client directory, install dependencies, and build the static assets.

```bash
cd client
npm install
npm run build
cd ..
```

### 2. Start the MCP Server
Navigate to the server directory, create a virtual environment, and start the server.

```bash
cd server
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```
The server will be running at `http://localhost:8000`.

### 3. Expose to the Internet
Open a new terminal and use ngrok to create a public tunnel.

```bash
ngrok http 8000
```
Copy the public URL (e.g., `https://abc-123.ngrok-free.app`).

### 4. Update the Server URL
In `server/main.py`, update the `host` variable with your ngrok URL:
```python
host = "https://abc-123.ngrok-free.app"
```
Restart the Python server.

### 5. Configure ChatGPT
1. Go to **ChatGPT > Explore GPTs > Create > Configure**.
2. Scroll to **Actions** and click **Create New Action**.
3. Set the **Import from URL** to your ngrok URL + `/mcp/openapi.json` (if using FastAPI's auto-generated docs) or manually define the Action to point to your MCP endpoint.
4. *Note:* For the Apps SDK "Native" experience, you will typically register this as an **MCP Connector** in the OpenAI Developer Portal.

## How it Works
1. **Trigger:** The user asks for a trip plan, flights, or activities.
2. **Tool Call:** ChatGPT calls `plan_trip` (with destination, origin, date), `search_flights`, or `search_activities` on your MCP server.
3. **Response:** The server returns trip data and a `widget_url` (for plans) or text info (for flights/activities).
4. **Rendering:** ChatGPT embeds the `widget_url` (your React app) directly in the chat interface.
