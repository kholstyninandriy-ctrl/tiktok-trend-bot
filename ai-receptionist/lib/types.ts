export type Role = "user" | "assistant";

export interface ChatMessage {
  role: Role;
  content: string;
}

export interface Lead {
  id: string;
  conversation_id: string;
  name: string | null;
  email: string | null;
  phone: string | null;
  budget_min: number | null;
  budget_max: number | null;
  neighborhood: string | null;
  property_type: string | null;
  bedrooms: number | null;
  timeline: string | null;
  notes: string | null;
  booked_at: string | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  channel: "chat" | "voice";
  source_url: string | null;
  started_at: string;
  message_count: number;
  lead: Lead | null;
}

export interface Listing {
  id: string;
  title: string;
  address: string;
  neighborhood: string;
  price: number;
  bedrooms: number;
  bathrooms: number;
  sqft: number;
  type: "condo" | "single-family" | "townhouse";
  highlights: string[];
}
