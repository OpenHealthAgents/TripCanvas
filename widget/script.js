(function () {
  function fromUrlParam() {
    try {
      var params = new URLSearchParams(window.location.search);
      var dataParam = params.get("data");
      if (!dataParam) return null;
      return JSON.parse(decodeURIComponent(dataParam));
    } catch (_err) {
      return null;
    }
  }

  function normalizeData(value) {
    if (!value) return null;
    if (value.structuredContent) return value.structuredContent;
    if (value.structured_content) return value.structured_content;
    if (value.output && value.output.structuredContent) return value.output.structuredContent;
    return value;
  }

  function render(data) {
    var root = document.getElementById("app");
    if (!root) return;

    if (!data) {
      root.innerHTML = '<div class="empty">No trip data available.</div>';
      return;
    }

    var destination = data.destination || "Trip";
    var flights = Array.isArray(data.flights) ? data.flights : [];
    var hotels = Array.isArray(data.hotels) ? data.hotels : [];
    var itinerary = Array.isArray(data.itinerary) ? data.itinerary : [];
    var warnings = Array.isArray(data.warnings) ? data.warnings : [];

    var flightsHtml = flights
      .map(function (flight) {
        var stops = typeof flight.stops === "number"
          ? (flight.stops === 0 ? "Non-stop" : (flight.stops + " stop" + (flight.stops > 1 ? "s" : "")))
          : "Stops unknown";
        var durationSummary = "Journey " + (flight.journey_duration || "unknown") +
          " • Air time " + (flight.air_time || "unknown");
        var details = [flight.carrier || "Carrier unavailable", stops, durationSummary].join(" • ");
        return (
          '<article class="flight">' +
          '<div class="flight-main">' +
          '<p class="flight-route">' + (flight.route || "Route unavailable") + "</p>" +
          '<p class="flight-meta-line">' + details + "</p>" +
          "</div>" +
          '<div class="flight-side">' +
          '<p class="flight-price">' + (flight.price || "") + "</p>" +
          '<p class="flight-meta-line">' + (flight.refundable_status || "Refundability unknown") + "</p>" +
          "</div>" +
          "</article>"
        );
      })
      .join("");

    var hotelsHtml = hotels
      .map(function (hotel) {
        return (
          '<article class="hotel">' +
          '<img src="' + (hotel.image || "") + '" alt="' + (hotel.name || "Hotel") + '" ' +
          'loading="lazy" data-image-source="hotel" style="display: block; width: 100%; height: 122px; object-fit: cover;" />' +
          '<div class="hotel-info">' +
          '<p class="hotel-name">' + (hotel.name || "Hotel") + "</p>" +
          '<div class="hotel-meta"><span>' + (hotel.price || "") + "</span><span>" +
          (hotel.rating ? "★ " + hotel.rating : "") + "</span></div>" +
          "</div>" +
          "</article>"
        );
      })
      .join("");

    var daysHtml = itinerary
      .map(function (day) {
        var activities = Array.isArray(day.activities) ? day.activities : [];
        return (
          '<section class="day">' +
          "<h3>Day " + (day.day || "") + "</h3>" +
          "<ul>" +
          activities.map(function (a) {
            return "<li>" + a + "</li>";
          }).join("") +
          "</ul>" +
          "</section>"
        );
      })
      .join("");

    var warningsHtml = warnings
      .map(function (w) {
        return '<li>' + w + "</li>";
      })
      .join("");

    root.innerHTML =
      '<div class="card">' +
      '<header class="header">' +
      "<h1>" + destination + "</h1>" +
      '<div class="meta">' + itinerary.length + " Days • " + flights.length + " Flights • " + hotels.length + " Hotels</div>" +
      "</header>" +
      '<section class="section"><h2>Best Flights</h2><div class="flights">' +
      (flightsHtml || '<div class="empty">No flights found.</div>') +
      "</div></section>" +
      '<section class="section"><h2>Recommended Hotels</h2><div class="hotels">' +
      (hotelsHtml || '<div class="empty">No hotels found.</div>') +
      "</div></section>" +
      '<section class="section"><h2>Daily Itinerary</h2>' +
      (daysHtml || '<div class="empty">No itinerary found.</div>') +
      "</section>" +
      '<section class="section"><h2>Notes</h2>' +
      (warningsHtml ? ('<ul class="warnings">' + warningsHtml + "</ul>") : '<div class="empty">No warnings.</div>') +
      "</section>" +
      '<div class="footer"><button class="btn" id="book-btn">Book This Trip</button></div>' +
      "</div>";

    var button = document.getElementById("book-btn");
    if (button) {
      button.addEventListener("click", function () {
        if (window.openai && typeof window.openai.sendFollowUpMessage === "function") {
          window.openai.sendFollowUpMessage({
            prompt: "Help me book this trip to " + destination
          });
        }
      });
    }

    // Add image error handling with fallback placeholder
    var images = document.querySelectorAll('.hotel img');
    images.forEach(function (img) {
      img.addEventListener('error', function () {
        // If image fails to load, use a placeholder background
        this.style.backgroundColor = '#e5eaf2';
        this.style.backgroundImage = 'linear-gradient(135deg, #f5f5f5 0%, #e5e5e5 100%)';
        this.alt = 'Hotel image unavailable';
      });
      // Ensure images are visible by setting initial display
      img.style.display = 'block';
    });
  }

  var initial = normalizeData(window.openai && window.openai.toolOutput) || normalizeData(fromUrlParam());
  render(initial);

  // React to host global updates (official Apps SDK pattern).
  window.addEventListener("openai:set_globals", function (event) {
    var globals = event && event.detail && event.detail.globals;
    if (!globals || globals.toolOutput === undefined) return;
    var next = normalizeData(globals.toolOutput);
    if (next) {
      render(next);
    }
  });

  // Fallback for delayed bridge injection.
  var attempts = 0;
  var timer = window.setInterval(function () {
    attempts += 1;
    var live = normalizeData(window.openai && window.openai.toolOutput);
    if (live) {
      render(live);
      window.clearInterval(timer);
      return;
    }
    if (attempts > 40) {
      window.clearInterval(timer);
    }
  }, 250);
})();
