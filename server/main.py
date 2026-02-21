import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any, Dict, List, Literal, Optional

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

def _as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


async def get_hotels(city_code: str, check_in_date: str, check_out_date: str, adults: int = 1):
    """Fetch hotel offers from Amadeus API."""
    if not amadeus:
        return []

    def _format_hotel_offers(raw_offers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        offers: List[Dict[str, Any]] = []
        for hotel_offer in raw_offers[:5]:
            hotel_info = hotel_offer.get("hotel", {})
            offer_list = hotel_offer.get("offers", [])
            best_offer = offer_list[0] if offer_list else {}
            price_info = best_offer.get("price", {})
            avg_price_info = price_info.get("variations", {}).get("average", {})
            total = _as_float(price_info.get("total"))
            currency = (price_info.get("currency") or "USD").upper()
            nightly = _as_float(avg_price_info.get("base"), fallback=0.0)
            offers.append(
                {
                    "name": hotel_info.get("name") or "Unknown Hotel",
                    "total_amount": total,
                    "nightly_amount": nightly if nightly > 0 else None,
                    "currency": currency,
                    "rating": _as_float(hotel_info.get("rating"), fallback=0.0),
                    "latitude": hotel_info.get("latitude"),
                    "longitude": hotel_info.get("longitude"),
                    "amenities": hotel_info.get("amenities", [])[:8],
                    "cancellation": best_offer.get("policies", {}).get("cancellation", {}).get("description", {}).get("text"),
                    "booking_url": best_offer.get("self"),
                }
            )
        return offers

    try:
        check_in = datetime.strptime(check_in_date, "%Y-%m-%d").date()
        check_out = datetime.strptime(check_out_date, "%Y-%m-%d").date()
    except ValueError:
        print(f"Amadeus Error (Hotels): invalid date input check_in={check_in_date} check_out={check_out_date}")
        return []

    # Amadeus requires check-out strictly after check-in.
    if check_out <= check_in:
        check_out = check_in + timedelta(days=1)

    try:
        hotel_ref = amadeus.reference_data.locations.hotels.by_city.get(cityCode=city_code)
        hotel_ids = [h.get("hotelId") for h in hotel_ref.data if h.get("hotelId")]
        if not hotel_ids:
            print(f"Amadeus Error (Hotels): no hotelIds found for cityCode={city_code}")
            return []
        response = amadeus.shopping.hotel_offers_search.get(
            hotelIds=",".join(hotel_ids[:20]),
            checkInDate=check_in.isoformat(),
            checkOutDate=check_out.isoformat(),
            adults=max(1, adults),
            roomQuantity=1,
            bestRateOnly=True,
            view="FULL",
        )
        return _format_hotel_offers(response.data)
    except ResponseError as fallback_error:
        fallback_details = getattr(getattr(fallback_error, "response", None), "result", None)
        print(
            "Amadeus Error (Hotels hotelIds search): "
            f"cityCode={city_code} checkIn={check_in.isoformat()} checkOut={check_out.isoformat()} "
            f"error={fallback_error} details={fallback_details}"
        )
        return []

async def get_activities(latitude: float, longitude: float):
    """Fetch tours and activities from Amadeus API."""
    if not amadeus:
        return []
    try:
        response = amadeus.shopping.activities.get(latitude=latitude, longitude=longitude)
        activities = []
        for activity in response.data[:8]:
            price_info = activity.get("price", {})
            geo_code = activity.get("geoCode", {})
            booking_link = (
                activity.get("bookingLink")
                or activity.get("self", {}).get("href")
                or activity.get("self")
            )
            activities.append(
                {
                    "title": activity.get("name") or "Local activity",
                    "amount": _as_float(price_info.get("amount"), fallback=0.0),
                    "currency": (price_info.get("currencyCode") or "USD").upper(),
                    "booking_url": booking_link,
                    "rating": _as_float(activity.get("rating"), fallback=0.0),
                    "description": activity.get("shortDescription"),
                    "latitude": geo_code.get("latitude"),
                    "longitude": geo_code.get("longitude"),
                }
            )
        return activities
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
            price_info = offer.get("price", {})
            itineraries = offer.get("itineraries", [])
            first_itinerary = itineraries[0] if itineraries else {}
            raw_segments = first_itinerary.get("segments", [])
            segments = []
            for segment in raw_segments:
                dep = segment.get("departure", {})
                arr = segment.get("arrival", {})
                segments.append(
                    {
                        "from": dep.get("iataCode", origin),
                        "to": arr.get("iataCode", destination),
                        "depart_at": dep.get("at", f"{departure_date}T09:00:00"),
                        "arrive_at": arr.get("at", f"{departure_date}T12:00:00"),
                        "carrier": segment.get("carrierCode", "Unknown"),
                        "flight_number": segment.get("number"),
                    }
                )

            if not segments:
                segments = [
                    {
                        "from": origin,
                        "to": destination,
                        "depart_at": f"{departure_date}T09:00:00",
                        "arrive_at": f"{departure_date}T12:00:00",
                        "carrier": "Unknown",
                        "flight_number": None,
                    }
                ]

            offers.append(
                {
                    "price_total": _as_float(price_info.get("total"), fallback=0.0),
                    "currency": (price_info.get("currency") or "USD").upper(),
                    "segments": segments,
                    "refundable": offer.get("pricingOptions", {}).get("refundableFare"),
                    "fare_rules_summary": "Live fare from Amadeus",
                }
            )
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

    # Inline CSS/JS for maximum ChatGPT widget compatibility when external asset fetches are restricted.
    styles_path = WIDGET_DIR / "styles.css"
    script_path = WIDGET_DIR / "script.js"
    if styles_path.exists():
        styles = styles_path.read_text(encoding="utf-8")
        html = html.replace(
            '<link rel="stylesheet" href="__WIDGET_HOST__/widget/styles.css" />',
            f"<style>{styles}</style>",
        )
    if script_path.exists():
        script = script_path.read_text(encoding="utf-8")
        html = html.replace(
            '<script src="__WIDGET_HOST__/widget/script.js"></script>',
            f"<script>{script}</script>",
        )
    return html

def build_widget_meta() -> dict:
    host = os.getenv("APP_HOST", "").rstrip("/")
    resource_domains = []
    if host.startswith("http://") or host.startswith("https://"):
        resource_domains.append(host)
    # Hotel images use Unsplash URLs.
    resource_domains.append("https://images.unsplash.com")
    # Add additional image and media domains for robust image rendering
    resource_domains.append("https://*.unsplash.com")
    
    meta = {
        "openai/outputTemplate": "ui://widget/trip-plan.html",
        "openai/widgetAccessible": True,
        "openai/widgetDescription": "Interactive trip itinerary with hotels and day-by-day plan.",
        "openai/widgetHasImages": True,  # Explicitly declare image support
        "openai/widgetCSP": {
            "connect_domains": [],
            "resource_domains": resource_domains,
            "img_src": ["self", "https:", "data:"],  # Allow image sources
            "object_src": ["none"],
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
            mimeType="text/html",
            description="The interactive UI for the travel planner",
            _meta=build_widget_meta()
        )
    ]

@mcp_server.read_resource()
async def read_resource(uri: str) -> types.TextResourceContents | types.BlobResourceContents:
    requested_uri = str(uri)
    normalized_uri = requested_uri.rstrip("/")
    print(f"read_resource requested_uri={requested_uri!r} normalized_uri={normalized_uri!r}")
    if normalized_uri == "ui://widget/trip-plan.html":
        index_html_path = WIDGET_DIR / "index.html"
        if index_html_path.exists():
            html = build_widget_html()
            return types.TextResourceContents(
                uri="ui://widget/trip-plan.html",
                mimeType="text/html",
                text=html,
                _meta=build_widget_meta()
            )
        print(f"read_resource missing_widget_file path={index_html_path}")
    raise ValueError(f"Resource not found: {requested_uri}")

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
                    "destination_iata": {"type": "string", "description": "Optional destination IATA/city code (e.g., 'TYO', 'PAR')"},
                    "origin": {"type": "string", "description": "The origin city IATA code (e.g., 'LON')", "default": "LON"},
                    "departure_date": {"type": "string", "description": "Departure date in YYYY-MM-DD format"},
                    "days": {"type": "integer", "description": "Number of days for the trip", "default": 3},
                },
                "required": ["destination"],
            },
            _meta={
                **build_widget_meta(),
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
        destination_iata = location["iataCode"] if location else _fallback_iata_for_city(destination_city)
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
        destination_iata_arg = arguments.get("destination_iata")
        destination_iata = destination_iata_arg.upper() if destination_iata_arg else None

        if not destination_iata:
            location = await get_location(destination_name)
            destination_iata = location["iataCode"] if location else _fallback_iata_for_city(destination_name)
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
        tool_warnings = list(search_response.warnings)
        flight_cards = []
        for flight in flights[:3]:
            first_segment = flight.segments[0] if flight.segments else None
            last_segment = flight.segments[-1] if flight.segments else None
            if not first_segment:
                continue
            route = f"{first_segment.from_} -> {last_segment.to if last_segment else first_segment.to}"
            stop_count = max(0, len(flight.segments) - 1)
            duration = _format_duration(
                first_segment.depart_at,
                last_segment.arrive_at if last_segment else first_segment.arrive_at,
            )
            air_time = _flight_air_time(flight.segments)
            if flight.refundable is True:
                refundable_status = "Refundable"
            elif flight.refundable is False:
                refundable_status = "Non-refundable"
            else:
                refundable_status = "Refundability unknown"
            flight_cards.append(
                {
                    "route": route,
                    "carrier": first_segment.carrier,
                    "depart_at": first_segment.depart_at,
                    "arrive_at": last_segment.arrive_at if last_segment else first_segment.arrive_at,
                    "price": f"{flight.total_price.currency} {flight.total_price.amount:,.0f}",
                    "stops": stop_count,
                    "journey_duration": duration,
                    "air_time": air_time,
                    "refundable_status": refundable_status,
                }
            )

        hotels = []
        for hotel_offer in search_response.hotels:
            hotels.append(
                {
                    "name": hotel_offer.hotel_name,
                    "image": "https://images.unsplash.com/photo-1566073771259-6a8506099945?auto=format&fit=crop&w=400",
                    "price": _format_nightly_price(
                        hotel_offer.nightly_price.amount if hotel_offer.nightly_price else None,
                        hotel_offer.total_price.currency,
                    ),
                    "rating": f"{hotel_offer.star_rating:.1f}" if hotel_offer.star_rating else "N/A",
                }
            )

        # Generate itinerary from real activities, with unique fallbacks when supply is low.
        itinerary = []
        activity_pool: List[str] = []
        for activity in search_response.activities:
            if activity.title not in activity_pool:
                activity_pool.append(activity.title)
        if not activity_pool:
            activity_pool = _fallback_activities_for_city(destination_name)
            tool_warnings = [
                w for w in tool_warnings
                if w != "No live activities were returned from Amadeus for this query."
            ]
            tool_warnings.append(
                f"Live activities were unavailable, so curated fallback activities are shown for {destination_name}."
            )

        cursor = 0
        for i in range(1, days + 1):
            day_activities = []
            for slot in range(2):
                if cursor < len(activity_pool):
                    day_activities.append(activity_pool[cursor])
                    cursor += 1
                else:
                    day_activities.append(
                        f"Self-guided exploration in {destination_name} (Day {i}, stop {slot + 1})"
                    )

            day_activities.append("Dinner at a local restaurant")
            
            itinerary.append({
                "day": i,
                "activities": day_activities
            })

        trip_data = {
            "destination": destination_name,
            "flights": flight_cards,
            "hotels": hotels,
            "itinerary": itinerary,
            "request_id": search_response.request_id,
            "warnings": tool_warnings,
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

CITY_IATA_FALLBACKS: Dict[str, str] = {
    "tokyo": "TYO",
    "new york": "NYC",
    "london": "LON",
    "paris": "PAR",
    "los angeles": "LAX",
    "san francisco": "SFO",
    "singapore": "SIN",
    "dubai": "DXB",
    "rome": "ROM",
    "milan": "MIL",
}

CITY_ACTIVITY_FALLBACKS: Dict[str, List[str]] = {
    "tokyo": [
        "Senso-ji Temple and Asakusa walk",
        "Shibuya Crossing and Hachiko Square",
        "Meiji Shrine and Yoyogi Park",
        "Tsukiji Outer Market food tour",
        "TeamLab Planets digital art museum",
        "Tokyo Skytree sunset view",
    ],
    "paris": [
        "Louvre Museum highlights",
        "Seine river walk and bookstalls",
        "Montmartre and Sacre-Coeur",
        "Eiffel Tower and Champ de Mars",
        "Le Marais cafe and gallery hopping",
        "Latin Quarter evening stroll",
    ],
    "london": [
        "Westminster and St James's Park walk",
        "British Museum highlights",
        "South Bank and Borough Market",
        "Tower Bridge and Tower of London",
        "Covent Garden and Soho food walk",
        "Greenwich observatory and riverside",
    ],
}


def _safe_iata(location: LocationModel, fallback: str) -> str:
    if location.iata:
        return location.iata.upper()
    return fallback


def _fallback_iata_for_city(city_name: Optional[str]) -> Optional[str]:
    if not city_name:
        return None
    return CITY_IATA_FALLBACKS.get(city_name.strip().lower())


def _currency_symbol(currency: str) -> str:
    return {
        "USD": "$",
        "EUR": "EUR ",
        "GBP": "GBP ",
        "JPY": "JPY ",
    }.get((currency or "").upper(), f"{(currency or 'CUR').upper()} ")


def _format_nightly_price(amount: Optional[float], currency: str) -> str:
    if amount is None:
        return "Check for rates"
    symbol = _currency_symbol(currency)
    return f"{symbol}{amount:,.0f}/night"


def _fallback_activities_for_city(city_name: str) -> List[str]:
    key = (city_name or "").strip().lower()
    return CITY_ACTIVITY_FALLBACKS.get(
        key,
        [
            f"Old town walking tour in {city_name}",
            f"Local market and food tasting in {city_name}",
            f"Top viewpoints around {city_name}",
            f"Museum and cultural district visit in {city_name}",
            f"Neighborhood cafe hopping in {city_name}",
            f"Riverside or waterfront evening walk in {city_name}",
        ],
    )


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _format_duration(depart_at: str, arrive_at: str) -> Optional[str]:
    depart_dt = _parse_iso_datetime(depart_at)
    arrive_dt = _parse_iso_datetime(arrive_at)
    if not depart_dt or not arrive_dt:
        return None
    total_minutes = int((arrive_dt - depart_dt).total_seconds() // 60)
    if total_minutes <= 0:
        return None
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _format_minutes(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _flight_air_time(segments: List[Segment]) -> Optional[str]:
    if not segments:
        return None
    minutes_total = 0
    for segment in segments:
        depart_dt = _parse_iso_datetime(segment.depart_at)
        arrive_dt = _parse_iso_datetime(segment.arrive_at)
        if not depart_dt or not arrive_dt:
            continue
        seg_minutes = int((arrive_dt - depart_dt).total_seconds() // 60)
        if seg_minutes > 0:
            minutes_total += seg_minutes
    if minutes_total <= 0:
        return None
    return _format_minutes(minutes_total)


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
    destination_iata = request.destination.iata.upper() if request.destination.iata else None
    origin_iata = _safe_iata(request.origin, "LON")
    destination_name = request.destination.city or destination_iata or "Destination"
    warnings: List[str] = []

    if not destination_iata:
        location = await get_location(destination_name)
        if location and location.get("iataCode"):
            destination_iata = location["iataCode"].upper()
        else:
            destination_iata = _fallback_iata_for_city(destination_name)
            if destination_iata:
                warnings.append(
                    f"Resolved destination '{destination_name}' using fallback IATA '{destination_iata}'."
                )
            else:
                warnings.append(
                    f"Could not resolve destination IATA for '{destination_name}'. "
                    "Set destination.iata explicitly for better provider matches."
                )

    if not destination_iata:
        response = SearchResponse(
            request_id=request_id,
            freshness_ts=utc_now_iso(),
            flights=[],
            hotels=[],
            activities=[],
            warnings=warnings,
        )
        print(
            f"search_travel request_id={request_id} destination={destination_name} "
            f"resolved_iata=None flights=0 hotels=0 activities=0 warnings={warnings}"
        )
        search_store[request_id] = response
        return response

    flights_raw = await search_flight_offers(
        origin_iata,
        destination_iata,
        request.dates.start_date,
    )
    flights: List[FlightOffer] = []
    for idx, offer in enumerate(flights_raw):
        flights.append(
            FlightOffer(
                id=f"flight_{request_id}_{idx}",
                provider="amadeus",
                total_price=Money(
                    amount=offer["price_total"],
                    currency=offer["currency"],
                ),
                segments=[Segment(**segment) for segment in offer["segments"]],
                fare_rules_summary=offer["fare_rules_summary"],
                refundable=offer["refundable"],
                booking_mode="redirect",
                score=max(1.0, 95.0 - idx * 5),
            )
        )
    if not flights:
        warnings.append("No live flight offers were returned from Amadeus for this query.")

    hotels_raw = await get_hotels(
        city_code=destination_iata,
        check_in_date=request.dates.start_date,
        check_out_date=request.dates.end_date,
        adults=request.travelers.adults,
    )
    hotels: List[HotelOffer] = []
    for idx, hotel in enumerate(hotels_raw):
        star_rating = hotel["rating"] if hotel["rating"] > 0 else None
        total_amount = hotel["total_amount"]
        nightly_amount = hotel["nightly_amount"]
        hotels.append(
            HotelOffer(
                id=f"hotel_{request_id}_{idx}",
                provider="expedia_rapid",
                hotel_name=hotel["name"],
                star_rating=star_rating,
                total_price=Money(amount=total_amount, currency=hotel["currency"]),
                nightly_price=(
                    Money(amount=nightly_amount, currency=hotel["currency"])
                    if nightly_amount is not None
                    else None
                ),
                cancellation_policy_summary=hotel["cancellation"],
                refundable=bool(hotel["cancellation"]),
                location=HotelLocation(
                    lat=hotel["latitude"],
                    lng=hotel["longitude"],
                    area=destination_name,
                ),
                amenities=hotel["amenities"],
                booking_url=hotel["booking_url"],
                score=(star_rating * 20.0) if star_rating else max(1.0, 88.0 - idx * 4),
            )
        )
    if not hotels:
        warnings.append("No live hotel offers were returned from Amadeus for this query.")

    latitude = request.destination.lat
    longitude = request.destination.lng
    if latitude is None or longitude is None:
        location = await get_location(destination_name)
        if location:
            latitude = location.get("latitude")
            longitude = location.get("longitude")

    activities_raw = []
    if latitude is not None and longitude is not None:
        activities_raw = await get_activities(latitude, longitude)

    activities: List[ActivityOffer] = []
    for idx, activity in enumerate(activities_raw):
        rating = activity["rating"] if activity["rating"] > 0 else None
        activities.append(
            ActivityOffer(
                id=f"activity_{request_id}_{idx}",
                provider="viator",
                title=activity["title"],
                duration_minutes=None,
                total_price=Money(amount=activity["amount"], currency=activity["currency"]),
                rating=rating,
                rating_count=None,
                cancellation_policy_summary=activity["description"],
                meeting_point=destination_name,
                booking_url=activity["booking_url"],
                score=(rating * 20.0) if rating else max(1.0, 86.0 - idx * 3),
            )
        )
    if not activities:
        warnings.append("No live activities were returned from Amadeus for this query.")

    response = SearchResponse(
        request_id=request_id,
        freshness_ts=utc_now_iso(),
        flights=flights,
        hotels=hotels,
        activities=activities,
        warnings=warnings,
    )
    print(
        f"search_travel request_id={request_id} destination={destination_name} "
        f"resolved_iata={destination_iata} flights={len(flights)} hotels={len(hotels)} "
        f"activities={len(activities)} warnings={warnings}"
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
