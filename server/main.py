import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from typing import Dict, List, Literal, Optional

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
import mcp.types as types
from starlette.responses import Response
from dotenv import load_dotenv
from amadeus import Client, ResponseError, Location
from pydantic import BaseModel, Field

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


class Money(BaseModel):
    amount: float
    currency: str = Field(min_length=3, max_length=3)


class DateRange(BaseModel):
    start_date: str
    end_date: str


class Traveler(BaseModel):
    adults: int = Field(ge=1)
    children_ages: List[int] = Field(default_factory=list)


class LocationModel(BaseModel):
    iata: Optional[str] = Field(default=None, min_length=3, max_length=3)
    city: Optional[str] = None
    country: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


class TripPreferences(BaseModel):
    cabin_class: Optional[Literal["economy", "premium_economy", "business", "first"]] = None
    hotel_stars_min: Optional[int] = Field(default=None, ge=1, le=5)
    max_stops: Optional[int] = Field(default=None, ge=0, le=3)
    refundable_only: Optional[bool] = None
    activity_categories: List[str] = Field(default_factory=list)


class TripRequest(BaseModel):
    origin: LocationModel
    destination: LocationModel
    dates: DateRange
    travelers: Traveler
    budget: Optional[Money] = None
    preferences: Optional[TripPreferences] = None


class Segment(BaseModel):
    from_: str = Field(alias="from")
    to: str
    depart_at: str
    arrive_at: str
    carrier: str
    flight_number: Optional[str] = None


class FlightOffer(BaseModel):
    id: str
    provider: Literal["amadeus", "duffel", "skyscanner"]
    total_price: Money
    segments: List[Segment]
    baggage_summary: Optional[str] = None
    fare_rules_summary: Optional[str] = None
    refundable: Optional[bool] = None
    booking_mode: Literal["redirect", "api_order"] = "redirect"
    booking_url: Optional[str] = None
    score: float


class HotelLocation(BaseModel):
    lat: Optional[float] = None
    lng: Optional[float] = None
    area: Optional[str] = None


class HotelOffer(BaseModel):
    id: str
    provider: Literal["expedia_rapid", "booking_demand"]
    hotel_name: str
    star_rating: Optional[float] = None
    total_price: Money
    nightly_price: Optional[Money] = None
    cancellation_policy_summary: Optional[str] = None
    refundable: Optional[bool] = None
    location: Optional[HotelLocation] = None
    amenities: List[str] = Field(default_factory=list)
    booking_url: Optional[str] = None
    score: float


class ActivityOffer(BaseModel):
    id: str
    provider: Literal["viator"]
    title: str
    duration_minutes: Optional[int] = None
    total_price: Money
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    cancellation_policy_summary: Optional[str] = None
    meeting_point: Optional[str] = None
    booking_url: Optional[str] = None
    score: float


class SearchResponse(BaseModel):
    request_id: str
    freshness_ts: str
    flights: List[FlightOffer] = Field(default_factory=list)
    hotels: List[HotelOffer] = Field(default_factory=list)
    activities: List[ActivityOffer] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RefineFilters(BaseModel):
    max_price: Optional[Money] = None
    airline_whitelist: List[str] = Field(default_factory=list)
    hotel_stars_min: Optional[int] = None
    refundable_only: Optional[bool] = None
    activity_categories: List[str] = Field(default_factory=list)


class RefineRequest(BaseModel):
    request_id: str
    filters: RefineFilters = Field(default_factory=RefineFilters)


class TravelerContact(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None


class StartBookingRequest(BaseModel):
    offer_type: Literal["flight", "hotel", "activity"]
    offer_id: str
    traveler_contact: Optional[TravelerContact] = None


class StartBookingResponse(BaseModel):
    status: Literal["ready", "requires_input", "failed"]
    booking_mode: Literal["redirect", "api_order"]
    booking_url: Optional[str] = None
    provider_order_id: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)


class ItineraryItem(BaseModel):
    type: Literal["flight", "hotel", "activity", "poi"]
    offer_id: str
    day: int = Field(ge=1)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    notes: Optional[str] = None


class SaveItineraryRequest(BaseModel):
    trip_request: TripRequest
    items: List[ItineraryItem]


class SaveItineraryResponse(BaseModel):
    itinerary_id: str

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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# Serve raw widget files
WIDGET_DIR = Path(__file__).parent.parent / "widget"

def build_widget_html() -> str:
    """Load widget HTML and rewrite asset URLs to an absolute APP_HOST when configured."""
    index_html_path = WIDGET_DIR / "index.html"
    html = index_html_path.read_text(encoding="utf-8")
    host = os.getenv("APP_HOST", "").rstrip("/")
    html = html.replace("__WIDGET_HOST__", host)
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
        index_html_path = WIDGET_DIR / "index.html"
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
        origin = (arguments.get("origin") or "LON").upper()
        destination = (arguments.get("destination") or "PAR").upper()
        departure_date = arguments.get("departure_date") or default_departure_date()
        request = build_trip_request(
            origin_iata=origin,
            destination_city=destination,
            destination_iata=destination,
            departure_date=departure_date,
            days=1,
        )
        search_response = await search_travel(request)
        offers = []
        for offer in search_response.flights:
            segment = offer.segments[0] if offer.segments else None
            route = f"{segment.from_}->{segment.to}" if segment else f"{origin}->{destination}"
            price = f"{offer.total_price.amount:.2f} {offer.total_price.currency}"
            details = offer.fare_rules_summary or "Live fare details unavailable."
            offers.append(f"{route} | {price} | {details}")
        offers_text = "\n".join(offers) if offers else "No flights found."
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Found flights:\n{offers_text}")]
        )

    elif name == "search_activities":
        keyword = arguments.get("keyword")
        location = await get_location(keyword) if keyword else None
        destination_city = keyword or "Paris"
        destination_iata = location["iataCode"] if location else None
        if location and location.get("name"):
            destination_city = location["name"]
        request = build_trip_request(
            origin_iata="LON",
            destination_city=destination_city,
            destination_iata=destination_iata,
            departure_date=default_departure_date(),
            days=1,
        )
        search_response = await search_travel(request)
        activities = [activity.title for activity in search_response.activities]
        text = f"Activities in {destination_city}:\n" + ("\n".join(activities) if activities else "No activities found.")
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])

    elif name == "plan_trip":
        destination_name = arguments.get("destination", "Paris")
        origin = arguments.get("origin", "LON").upper()
        days = arguments.get("days", 3)
        departure_date = arguments.get("departure_date") or default_departure_date()

        location = await get_location(destination_name)
        destination_iata = location["iataCode"] if location else None
        if location and location.get("name"):
            destination_name = location["name"]

        request = build_trip_request(
            origin_iata=origin,
            destination_city=destination_name,
            destination_iata=destination_iata,
            departure_date=departure_date,
            days=days,
        )
        search_response = await search_travel(request)
        flights = search_response.flights

        hotels = []
        for hotel_offer in search_response.hotels:
            hotels.append(
                {
                    "name": hotel_offer.hotel_name,
                    "image": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=400",
                    "price": f"${hotel_offer.nightly_price.amount:.0f}/night" if hotel_offer.nightly_price else "Check for rates",
                    "rating": f"{hotel_offer.star_rating:.1f}" if hotel_offer.star_rating else "N/A",
                }
            )

        # Generate Itinerary with real activities
        itinerary = []
        activity_pool = [activity.title for activity in search_response.activities]
        
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
            "itinerary": itinerary,
            "request_id": search_response.request_id,
        }

        if flights:
            best_flight = flights[0]
            flight_msg = (
                " Best flight found: "
                f"{best_flight.segments[0].from_}->{best_flight.segments[0].to} "
                f"{best_flight.total_price.amount:.2f} {best_flight.total_price.currency}"
            )
        else:
            flight_msg = " (No direct flights found for this date)"

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


def _openapi_server_url() -> str:
    return (
        os.getenv("APP_HOST", "").rstrip("/")
        or os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
        or "http://localhost:8000"
    )


app = FastAPI(
    title="TripCanvas",
    version="1.0.0",
    lifespan=lifespan,
    servers=[{"url": _openapi_server_url()}],
)

search_store: Dict[str, SearchResponse] = {}
itinerary_store: Dict[str, SaveItineraryRequest] = {}


def _safe_iata(location: LocationModel, fallback: str) -> str:
    if location.iata:
        return location.iata.upper()
    return fallback


def default_departure_date() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()


def build_trip_request(
    origin_iata: str,
    destination_city: str,
    departure_date: str,
    destination_iata: Optional[str] = None,
    days: int = 3,
) -> TripRequest:
    start_date = datetime.strptime(departure_date, "%Y-%m-%d").date()
    end_date = start_date + timedelta(days=max(days, 1) - 1)
    return TripRequest(
        origin=LocationModel(iata=origin_iata, city=origin_iata),
        destination=LocationModel(
            iata=destination_iata,
            city=destination_city,
        ),
        dates=DateRange(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        ),
        travelers=Traveler(adults=1),
    )


@app.post("/v1/search_travel", response_model=SearchResponse, operation_id="search_travel")
async def search_travel(request: TripRequest):
    request_id = str(uuid4())
    destination_iata = _safe_iata(request.destination, "PAR")
    origin_iata = _safe_iata(request.origin, "LON")

    flights_raw = await search_flight_offers(
        origin_iata,
        destination_iata,
        request.dates.start_date,
    )
    flights: List[FlightOffer] = []
    for idx, flight in enumerate(flights_raw):
        flights.append(
            FlightOffer(
                id=f"flight_{request_id}_{idx}",
                provider="amadeus",
                total_price=Money(amount=0.0, currency="USD"),
                segments=[
                    Segment(
                        **{
                            "from": origin_iata,
                            "to": destination_iata,
                            "depart_at": f"{request.dates.start_date}T09:00:00Z",
                            "arrive_at": f"{request.dates.start_date}T12:00:00Z",
                            "carrier": "Unknown",
                            "flight_number": None,
                        }
                    )
                ],
                fare_rules_summary=flight,
                booking_mode="redirect",
                score=max(1.0, 100.0 - idx * 5),
            )
        )

    hotels = [
        HotelOffer(
            id=f"hotel_{request_id}_0",
            provider="expedia_rapid",
            hotel_name=f"{request.destination.city or destination_iata} Central Hotel",
            star_rating=4.3,
            total_price=Money(amount=850.0, currency="USD"),
            nightly_price=Money(amount=170.0, currency="USD"),
            cancellation_policy_summary="Free cancellation up to 24h before check-in.",
            refundable=True,
            location=HotelLocation(lat=request.destination.lat, lng=request.destination.lng, area="City Center"),
            amenities=["wifi", "breakfast", "gym"],
            booking_url="https://www.tripcanvas.site",
            score=91.0,
        )
    ]

    activities = [
        ActivityOffer(
            id=f"activity_{request_id}_0",
            provider="viator",
            title=f"{request.destination.city or destination_iata} City Highlights Tour",
            duration_minutes=180,
            total_price=Money(amount=59.0, currency="USD"),
            rating=4.6,
            rating_count=1200,
            cancellation_policy_summary="Free cancellation up to 24h before activity.",
            meeting_point="Central square",
            booking_url="https://www.tripcanvas.site",
            score=89.0,
        )
    ]

    response = SearchResponse(
        request_id=request_id,
        freshness_ts=utc_now_iso(),
        flights=flights,
        hotels=hotels,
        activities=activities,
        warnings=[] if flights else ["No live flight offers were returned from provider for this query."],
    )
    search_store[request_id] = response
    return response


@app.post("/v1/refine_results", response_model=SearchResponse, operation_id="refine_results")
async def refine_results(request: RefineRequest):
    existing = search_store.get(request.request_id)
    if not existing:
        return SearchResponse(
            request_id=request.request_id,
            freshness_ts=utc_now_iso(),
            flights=[],
            hotels=[],
            activities=[],
            warnings=["Unknown request_id. Run /v1/search_travel first."],
        )

    max_price = request.filters.max_price.amount if request.filters.max_price else None
    flights = existing.flights
    hotels = existing.hotels
    activities = existing.activities

    if max_price is not None:
        flights = [f for f in flights if f.total_price.amount <= max_price]
        hotels = [h for h in hotels if h.total_price.amount <= max_price]
        activities = [a for a in activities if a.total_price.amount <= max_price]

    refined = SearchResponse(
        request_id=existing.request_id,
        freshness_ts=utc_now_iso(),
        flights=flights,
        hotels=hotels,
        activities=activities,
        warnings=existing.warnings,
    )
    search_store[request.request_id] = refined
    return refined


@app.post("/v1/start_booking", response_model=StartBookingResponse, operation_id="start_booking")
async def start_booking(request: StartBookingRequest):
    return StartBookingResponse(
        status="ready",
        booking_mode="redirect",
        booking_url=f"https://www.tripcanvas.site/booking/{request.offer_type}/{request.offer_id}",
        provider_order_id=None,
        missing_fields=[],
    )


@app.post("/v1/save_itinerary", response_model=SaveItineraryResponse, operation_id="save_itinerary")
async def save_itinerary(request: SaveItineraryRequest):
    itinerary_id = str(uuid4())
    itinerary_store[itinerary_id] = request
    return SaveItineraryResponse(itinerary_id=itinerary_id)


@app.get("/v1/get_policy_summary/{offer_id}", operation_id="get_policy_summary")
async def get_policy_summary(offer_id: str):
    return {
        "offer_id": offer_id,
        "refundable": True,
        "policy_summary": "Free cancellation within 24 hours, then provider policy applies.",
    }

transport = SseServerTransport("/messages")

@app.get("/healthz")
async def healthz():
    return {"ok": True}

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

# Serve raw widget assets (CSS/JS)
if WIDGET_DIR.exists():
    app.mount("/widget", StaticFiles(directory=WIDGET_DIR, html=True), name="widget")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
