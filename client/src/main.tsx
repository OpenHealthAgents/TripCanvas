import React, { useState, useEffect, useSyncExternalStore } from 'react'
import ReactDOM from 'react-dom/client'
import TripCanvasWidget from './TripCanvasWidget'
import './index.css'

declare global {
  interface Window {
    openai?: {
      toolOutput?: any;
      [key: string]: any;
    };
  }
}

const SET_GLOBALS_EVENT = "openai:set_globals";

function useOpenAIGlobal(property: string) {
  return useSyncExternalStore(
    (callback) => {
      if (typeof window === "undefined") return () => {};
      const handler = (event: any) => {
        if (event.detail?.globals?.[property] !== undefined) {
          callback();
        }
      };
      window.addEventListener(SET_GLOBALS_EVENT, handler, { passive: true });
      return () => window.removeEventListener(SET_GLOBALS_EVENT, handler);
    },
    () => (window.openai as any)?.[property] ?? null,
    () => null
  );
}

const App = () => {
  const toolOutput = useOpenAIGlobal("toolOutput");
  const [polledToolOutput, setPolledToolOutput] = useState<any>(null);
  const [urlData, setUrlData] = useState<any>(null);

  useEffect(() => {
    // Fallback to URL parameters if not in Native mode
    try {
      const params = new URLSearchParams(window.location.search);
      const dataParam = params.get('data');
      if (dataParam) {
        setUrlData(JSON.parse(decodeURIComponent(dataParam)));
      }
    } catch (e) {
      console.error("Failed to parse data from URL", e);
    }
  }, []);

  useEffect(() => {
    // Fallback for hosts that don't emit openai:set_globals updates.
    const timer = window.setInterval(() => {
      const liveOutput = (window.openai as any)?.toolOutput ?? null;
      if (liveOutput) {
        setPolledToolOutput(liveOutput);
        window.clearInterval(timer);
      }
    }, 250);
    return () => window.clearInterval(timer);
  }, []);

  const initialData =
    toolOutput?.structuredContent ||
    toolOutput ||
    polledToolOutput?.structuredContent ||
    polledToolOutput ||
    urlData?.structuredContent ||
    urlData;

  if (!initialData) {
    return (
      <div className="p-8 text-center text-gray-500">
        Loading TripCanvas...
      </div>
    );
  }

  return (
    <div className="p-4 max-w-2xl mx-auto">
      <TripCanvasWidget 
        destination={initialData.destination || "Kyoto"}
        hotels={initialData.hotels || []}
        itinerary={initialData.itinerary || []}
      />
    </div>
  );
};

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
