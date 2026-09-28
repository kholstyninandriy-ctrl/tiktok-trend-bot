import type { Listing } from "./types";

/**
 * Demo inventory. In a real deployment this comes from the agency's MLS feed;
 * for the demo it is a static list so the receptionist has something concrete
 * to talk about.
 */
export const LISTINGS: Listing[] = [
  {
    id: "SF-101",
    title: "Sunlit 2-bed with bay views",
    address: "1450 Sacramento St, San Francisco, CA 94109",
    neighborhood: "Nob Hill",
    price: 1_185_000,
    bedrooms: 2,
    bathrooms: 2,
    sqft: 1180,
    type: "condo",
    highlights: ["Bay views", "Deeded parking", "Doorman building"],
  },
  {
    id: "SF-102",
    title: "Renovated Victorian townhouse",
    address: "742 Haight St, San Francisco, CA 94117",
    neighborhood: "Lower Haight",
    price: 1_650_000,
    bedrooms: 3,
    bathrooms: 2.5,
    sqft: 1840,
    type: "townhouse",
    highlights: ["Original detailing", "Private garden", "Chef's kitchen"],
  },
  {
    id: "SF-103",
    title: "Family home near Golden Gate Park",
    address: "1290 9th Ave, San Francisco, CA 94122",
    neighborhood: "Inner Sunset",
    price: 1_395_000,
    bedrooms: 4,
    bathrooms: 3,
    sqft: 2200,
    type: "single-family",
    highlights: ["Two-car garage", "Finished basement", "Top school district"],
  },
  {
    id: "SF-104",
    title: "Entry-level 1-bed, walk to transit",
    address: "555 Mission Bay Blvd, San Francisco, CA 94158",
    neighborhood: "Mission Bay",
    price: 749_000,
    bedrooms: 1,
    bathrooms: 1,
    sqft: 720,
    type: "condo",
    highlights: ["New construction", "Gym + roof deck", "Caltrain 6 min walk"],
  },
  {
    id: "SF-105",
    title: "Top-floor penthouse with terrace",
    address: "200 Brannan St, San Francisco, CA 94107",
    neighborhood: "South Beach",
    price: 2_450_000,
    bedrooms: 3,
    bathrooms: 3,
    sqft: 2050,
    type: "condo",
    highlights: ["500 sq ft terrace", "Concierge", "Two parking spaces"],
  },
];

export interface ListingFilter {
  max_price?: number;
  min_price?: number;
  neighborhood?: string;
  bedrooms?: number;
  property_type?: string;
}

export function searchListings(filter: ListingFilter): Listing[] {
  const wanted = filter.neighborhood?.toLowerCase().trim();

  return LISTINGS.filter((l) => {
    if (filter.max_price && l.price > filter.max_price * 1.1) return false;
    if (filter.min_price && l.price < filter.min_price * 0.9) return false;
    if (filter.bedrooms && l.bedrooms < filter.bedrooms) return false;
    if (filter.property_type && l.type !== filter.property_type) return false;
    if (wanted && !l.neighborhood.toLowerCase().includes(wanted)) return false;
    return true;
  }).slice(0, 4);
}

export function mapsEmbedUrl(address: string): string | null {
  const key = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY;
  if (!key) return null;
  return `https://www.google.com/maps/embed/v1/place?key=${key}&q=${encodeURIComponent(address)}&zoom=15`;
}

export function mapsLinkUrl(address: string): string {
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;
}

export function formatPrice(n: number): string {
  return `$${n.toLocaleString("en-US")}`;
}
