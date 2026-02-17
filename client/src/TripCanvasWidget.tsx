import React from 'react';
import { MapPin, Calendar, Hotel, ChevronRight } from 'lucide-react';
import { Button } from '@openai/apps-sdk-ui/components/Button';

interface HotelData {
  name: string;
  image: string;
  price: string;
  rating: string;
}

interface DayPlan {
  day: number;
  activities: string[];
}

interface TripCanvasProps {
  destination: string;
  hotels: HotelData[];
  itinerary: DayPlan[];
}

const TripCanvasWidget: React.FC<TripCanvasProps> = ({ destination, hotels, itinerary }) => {
  return (
    <div className="bg-surface rounded-2xl border border-default overflow-hidden font-sans text-primary">
      {/* Header */}
      <div className="bg-primary/5 p-6 border-b border-default">
        <div className="flex items-center gap-2 mb-2">
          <MapPin size={20} className="text-primary" />
          <h1 className="text-2xl font-bold tracking-tight">{destination}</h1>
        </div>
        <div className="flex items-center gap-4 text-secondary text-sm">
          <span className="flex items-center gap-1">
            <Calendar size={14} /> {itinerary.length} Days
          </span>
          <span className="flex items-center gap-1">
            <Hotel size={14} /> {hotels.length} Hotels
          </span>
        </div>
      </div>

      <div className="p-6 space-y-8">
        {/* Hotels Carousel */}
        <section>
          <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
            Recommended Hotels
          </h2>
          <div className="flex gap-4 overflow-x-auto pb-4 scrollbar-hide">
            {hotels.map((hotel, i) => (
              <div key={i} className="min-w-[220px] bg-subtle rounded-xl overflow-hidden border border-default shadow-sm hover:shadow-md transition-shadow">
                <img 
                  src={hotel.image} 
                  alt={hotel.name} 
                  className="w-full h-32 object-cover"
                />
                <div className="p-3">
                  <h3 className="font-semibold text-sm truncate">{hotel.name}</h3>
                  <div className="flex justify-between items-center mt-2">
                    <span className="text-primary font-bold text-sm">{hotel.price}</span>
                    <span className="text-yellow-500 text-xs font-medium">★ {hotel.rating}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Itinerary */}
        <section>
          <h2 className="text-lg font-semibold mb-4">Daily Itinerary</h2>
          <div className="space-y-4">
            {itinerary.map((day) => (
              <div key={day.day} className="border-l-2 border-primary/20 pl-4 py-1">
                <h3 className="font-bold text-primary mb-2">Day {day.day}</h3>
                <ul className="space-y-3">
                  {day.activities.map((activity, idx) => (
                    <li key={idx} className="flex items-start gap-2 text-sm text-secondary">
                      <ChevronRight size={16} className="text-primary/40 mt-0.5 shrink-0" />
                      <span>{activity}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="p-4 bg-primary/5 border-t border-default text-center">
        <Button 
          color="primary" 
          variant="solid" 
          size="md" 
          className="w-full sm:w-auto px-10"
          onClick={() => window.openai?.sendFollowUpMessage?.({ prompt: `Help me book this trip to ${destination}` })}
        >
          Book This Trip
        </Button>
      </div>
    </div>
  );
};

export default TripCanvasWidget;
