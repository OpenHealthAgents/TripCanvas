import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
import mcp.types as types
from starlette.responses import Response
from dotenv import load_dotenv
from amadeus import Client, ResponseError, Location

# Load environment variables
load_dotenv()

# Initialize Amadeus Client
try:
    amadeus = Client(
        client_id=os.getenv("AMADEUS_API_KEY"),
        client_secret=os.getenv("AMADEUS_API_SECRET")
    )
except Exception as e:
    print(f"Warning: Amadeus Client failed to initialize: {e}")
    amadeus = None

# Initialize MCP Server
mcp_server = Server("trip-canvas")

async def get_hotels(city_code: str):
    """Fetch real hotels from Amadeus API."""
    if not amadeus:
        return []
    try:
        # Search hotels in the city
        response = amadeus.reference_data.locations.hotels.by_city.get(cityCode=city_code)
        hotel_list = response.data[:3]  # Get top 3
        
        formatted_hotels = []
        for hotel in hotel_list:
            formatted_hotels.append({
                "name": hotel.get('name', 'Unknown Hotel').title(),
                "image": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=400", 
                "price": "Check for rates",
                "rating": "4.5"
            })
        return formatted_hotels
    except ResponseError as error:
        print(f"Amadeus Error (Hotels): {error}")
        return []

async def get_activities(latitude: float, longitude: float):
    """Fetch tours and activities from Amadeus API."""
    if not amadeus:
        return []
    try:
        response = amadeus.shopping.activities.get(
            latitude=latitude, longitude=longitude
        )
        return [activity.get('name') for activity in response.data[:5]]
    except ResponseError as error:
        print(f"Amadeus Error (Activities): {error}")
        return []

async def get_location(keyword: str):
    """Search for a location (city/airport) to get coordinates and IATA code."""
    if not amadeus:
        return None
    try:
        response = amadeus.reference_data.locations.get(
            keyword=keyword,
            subType=Location.CITY
        )
        if response.data:
            location = response.data[0]
            return {
                "name": location.get('name'),
                "iataCode": location.get('iataCode'),
                "latitude": location.get('geoCode', {}).get('latitude'),
                "longitude": location.get('geoCode', {}).get('longitude')
            }
        return None
    except ResponseError as error:
        print(f"Amadeus Error (Location): {error}")
        return None

async def search_flight_offers(origin: str, destination: str, departure_date: str):
    """Search for flight offers."""
    if not amadeus:
        return []
    try:
        response = amadeus.shopping.flight_offers_search.get(
            originLocationCode=origin,
            destinationLocationCode=destination,
            departureDate=departure_date,
            adults=1
        )
        offers = []
        for offer in response.data[:3]:
            price = offer.get('price', {}).get('total')
            currency = offer.get('price', {}).get('currency')
            itineraries = offer.get('itineraries', [])
            if itineraries:
                segments = itineraries[0].get('segments', [])
                carrier = segments[0].get('carrierCode') if segments else "Unknown"
                offers.append(f"Flight by {carrier}: {price} {currency}")
        return offers
    except ResponseError as error:
        print(f"Amadeus Error (Flights): {error}")
        return []

# Serve built React files
FRONTEND_DIST = Path(__file__).parent.parent / "client" / "dist"
def build_widget_html() -> str:
    """Load built index HTML and optionally rewrite asset URLs to an absolute APP_HOST."""
    index_html_path = FRONTEND_DIST / "index.html"
    html = index_html_path.read_text(encoding="utf-8")
    host = os.getenv("APP_HOST", "").rstrip("/")
    if host:
        html = html.replace('src="./assets/', f'src="{host}/assets/')
        html = html.replace('href="./assets/', f'href="{host}/assets/')
    return html

def build_widget_meta() -> dict:
    host = os.getenv("APP_HOST", "").rstrip("/")
    resource_domains = []
    if host.startswith("http://") or host.startswith("https://"):
        resource_domains.append(host)
    # Hotel images use Unsplash URLs.
    resource_domains.append("https://images.unsplash.com")
    meta = {
        "openai/outputTemplate": "ui://widget/trip-plan.html",
        "openai/widgetAccessible": True,
        "openai/widgetDescription": "Interactive trip itinerary with hotels and day-by-day plan.",
        "openai/widgetCSP": {
            "connect_domains": [],
            "resource_domains": resource_domains,
        },
    }
    if host.startswith("http://") or host.startswith("https://"):
        meta["openai/widgetDomain"] = host
    return meta

@mcp_server.list_resources()
async def list_resources() -> List[types.Resource]:
    return [
        types.Resource(
            uri="ui://widget/trip-plan.html",
            name="Trip Plan Widget",
            mimeType="text/html+skybridge",
            description="The interactive UI for the travel planner",
            _meta=build_widget_meta()
        )
    ]

@mcp_server.read_resource()
async def read_resource(uri: str) -> types.TextResourceContents | types.BlobResourceContents:
    if uri == "ui://widget/trip-plan.html":
        index_html_path = FRONTEND_DIST / "index.html"
        if index_html_path.exists():
            html = build_widget_html()
            
            return types.TextResourceContents(
                uri=uri,
                mimeType="text/html+skybridge",
                text=html,
                _meta=build_widget_meta()
            )
    raise ValueError(f"Resource not found: {uri}")

@mcp_server.list_tools()
async def list_tools() -> List[types.Tool]:
    return [
        types.Tool(
            name="plan_trip",
            description="Plans a comprehensive travel itinerary including flights, hotels and activities using Amadeus.",
            inputSchema={
                "type": "object",
                "properties": {
                    "destination": {"type": "string", "description": "The city name (e.g., 'Paris', 'New York')"},
                    "origin": {"type": "string", "description": "The origin city IATA code (e.g., 'LON')", "default": "LON"},
                    "departure_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                    "days": {"type": "integer", "description": "Number of days for the trip", "default": 3},
                },
                "required": ["destination"],
            },
            _meta={
                "openai/outputTemplate": "ui://widget/trip-plan.html",
                "openai/widgetAccessible": True
            },
            annotations={
                "destructiveHint": False,
                "openWorldHint": False,
                "readOnlyHint": True,
            }
        ),
        types.Tool(
            name="search_flights",
            description="Search for flight offers between two cities.",
            inputSchema={
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Origin IATA code (e.g., LHR)"},
                    "destination": {"type": "string", "description": "Destination IATA code (e.g., JFK)"},
                    "departure_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                },
                "required": ["origin", "destination", "departure_date"],
            },
        ),
        types.Tool(
            name="search_activities",
            description="Find tours and activities at a destination.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "City name to search for activities"},
                },
                "required": ["keyword"],
            },
        )
    ]

@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
    if name == "search_flights":
        origin = arguments.get("origin")
        destination = arguments.get("destination")
        date = arguments.get("departure_date")
        offers = await search_flight_offers(origin, destination, date)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Found flights:\n" + "\n".join(offers))]
        )

    elif name == "search_activities":
        keyword = arguments.get("keyword")
        location = await get_location(keyword)
        if location:
            activities = await get_activities(location['latitude'], location['longitude'])
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Activities in {keyword}:\n" + "\n".join(activities))]
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Could not find location: {keyword}")],
            isError=True
        )

    elif name == "plan_trip":
        destination_name = arguments.get("destination", "Paris")
        origin = arguments.get("origin", "LON")
        days = arguments.get("days", 3)
        
        # Default departure date to 30 days from now if not provided
        departure_date = arguments.get("departure_date")
        if not departure_date:
            from datetime import datetime, timedelta
            departure_date = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')

        # Resolve location
        location = await get_location(destination_name)
        
        # Defaults if location fails
        city_code = "PAR"
        lat, lon = 48.8566, 2.3522
        
        if location:
            city_code = location['iataCode']
            lat, lon = location['latitude'], location['longitude']
            destination_name = location['name'] # normalize name

        # Fetch real data
        hotels = await get_hotels(city_code)
        activities = await get_activities(lat, lon)
        flights = await search_flight_offers(origin, city_code, departure_date)
        
        # Fallback for hotels
        if not hotels:
            hotels = [
                {"name": "Local Boutique Hotel", "image": "https://images.unsplash.com/photo-1542314831-068cd1dbfeeb?auto=format&fit=crop&w=400", "price": "$200/night", "rating": "4.2"},
                {"name": "Grand City View", "image": "https://images.unsplash.com/photo-1571896349842-33c89424de2d?auto=format&fit=crop&w=400", "price": "$350/night", "rating": "4.8"}
            ]

        # Generate Itinerary with real activities
        itinerary = []
        activity_pool = list(activities)
        
        # If no real activities found, use generic ones
        if not activity_pool:
            activity_pool = [f"Explore {destination_name} center", "Visit local museum", "Walk in the park", "City tour", "Shopping district"]

        # Ensure we have enough activities for the days
        while len(activity_pool) < days * 2:
            activity_pool.append(f"Discover hidden gems in {destination_name}")

        for i in range(1, days + 1):
            day_activities = []
            # Pick 2 activities per day
            if len(activity_pool) >= 2:
                day_activities.append(activity_pool.pop(0))
                day_activities.append(activity_pool.pop(0))
            else:
                 day_activities.append(f"Explore {destination_name}")

            day_activities.append("Dinner at a local restaurant")
            
            itinerary.append({
                "day": i,
                "activities": day_activities
            })

        trip_data = {
            "destination": destination_name,
            "hotels": hotels,
            "itinerary": itinerary
        }

        flight_msg = f" Best flight found: {flights[0]}" if flights else " (No direct flights found for this date)"

        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=f"I've planned a {days}-day trip to {destination_name} starting {departure_date}!{flight_msg}"
                )
            ],
            structuredContent=trip_data,
            _meta={
                "openai/outputTemplate": "ui://widget/trip-plan.html",
                "openai/toolInvocation/invoking": f"Planning your trip to {destination_name}...",
                "openai/toolInvocation/invoked": f"Trip to {destination_name} planned."
            }
        )
    
    else:
        raise ValueError(f"Unknown tool: {name}")

# Initialize FastAPI app with Streamable HTTP session lifecycle.
streamable_session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    json_response=False,
    stateless=False,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with streamable_session_manager.run():
        yield

app = FastAPI(title="TripCanvas", lifespan=lifespan)
transport = SseServerTransport("/messages")

class StreamableHTTPASGIApp:
    async def __call__(self, scope, receive, send) -> None:
        await streamable_session_manager.handle_request(scope, receive, send)

# Primary endpoint for ChatGPT connectors (supports POST /mcp).
app.mount("/mcp", StreamableHTTPASGIApp())

# Optional SSE fallback endpoint.
@app.get("/sse")
async def handle_mcp_sse(request: Request):
    async with transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )
    return Response()

# Mount the message endpoint directly as an ASGI app to avoid FastAPI sending a second response.
app.mount("/messages", transport.handle_post_message)

# Serve built React files (for assets like CSS/JS)
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST, html=True), name="assets")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
