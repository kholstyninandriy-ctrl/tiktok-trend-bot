import type Anthropic from "@anthropic-ai/sdk";
import { createBooking, suggestSlots } from "./calendar";
import { formatPrice, LISTINGS, searchListings } from "./listings";
import { saveLead } from "./store";

/**
 * One tool surface shared by the chat widget and the Vapi voice agent, so both
 * channels qualify and book identically.
 */

export const TOOLS: Anthropic.Tool[] = [
  {
    name: "search_listings",
    description:
      "Search the agency's current inventory. Call this before describing any property; only mention properties this returns.",
    input_schema: {
      type: "object",
      properties: {
        min_price: { type: "number", description: "Lower bound in USD" },
        max_price: { type: "number", description: "Upper bound in USD" },
        neighborhood: { type: "string" },
        bedrooms: { type: "number", description: "Minimum bedrooms" },
        property_type: {
          type: "string",
          enum: ["condo", "single-family", "townhouse"],
        },
      },
    },
  },
  {
    name: "save_lead",
    description:
      "Record what you have learned about this visitor. Call it silently every time a new detail comes up. Send only the fields you learned.",
    input_schema: {
      type: "object",
      properties: {
        name: { type: "string" },
        email: { type: "string" },
        phone: { type: "string" },
        budget_min: { type: "number" },
        budget_max: { type: "number" },
        neighborhood: { type: "string" },
        property_type: { type: "string" },
        bedrooms: { type: "number" },
        timeline: {
          type: "string",
          description: "e.g. 'Within 30 days', '3-6 months', 'Just browsing'",
        },
        notes: { type: "string", description: "Anything else worth telling the agent" },
      },
    },
  },
  {
    name: "get_available_slots",
    description:
      "Get open appointment times. Call before proposing any time. Offer two or three of them, never the full list.",
    input_schema: { type: "object", properties: {} },
  },
  {
    name: "book_showing",
    description:
      "Book a showing or agent call. Requires a name and at least a phone or an email.",
    input_schema: {
      type: "object",
      properties: {
        start: {
          type: "string",
          description: "ISO timestamp, exactly as returned by get_available_slots",
        },
        name: { type: "string" },
        phone: { type: "string" },
        email: { type: "string" },
        listing_id: { type: "string", description: "e.g. SF-101, if a specific property" },
        notes: { type: "string" },
      },
      required: ["start", "name"],
    },
  },
];

type ToolInput = Record<string, unknown>;

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

/** Runs one tool call and returns the text handed back to the model. */
export async function runTool(
  name: string,
  input: ToolInput,
  conversationId: string,
): Promise<string> {
  switch (name) {
    case "search_listings": {
      const results = searchListings({
        min_price: num(input.min_price),
        max_price: num(input.max_price),
        neighborhood: str(input.neighborhood),
        bedrooms: num(input.bedrooms),
        property_type: str(input.property_type),
      });

      if (results.length === 0) {
        return "No matches in current inventory. Offer to note what they want and have an agent watch for new listings.";
      }

      return results
        .map(
          (l) =>
            `${l.id} — ${l.title}. ${formatPrice(l.price)}. ${l.bedrooms} bed / ${l.bathrooms} bath, ${l.sqft} sq ft. ${l.neighborhood}, ${l.address}. ${l.highlights.join(", ")}.`,
        )
        .join("\n");
    }

    case "save_lead": {
      await saveLead(conversationId, {
        name: str(input.name) ?? undefined,
        email: str(input.email) ?? undefined,
        phone: str(input.phone) ?? undefined,
        budget_min: num(input.budget_min) ?? undefined,
        budget_max: num(input.budget_max) ?? undefined,
        neighborhood: str(input.neighborhood) ?? undefined,
        property_type: str(input.property_type) ?? undefined,
        bedrooms: num(input.bedrooms) ?? undefined,
        timeline: str(input.timeline) ?? undefined,
        notes: str(input.notes) ?? undefined,
      });
      return "Saved.";
    }

    case "get_available_slots": {
      const slots = await suggestSlots(4);
      if (slots.length === 0) return "Nothing open soon. Offer to have an agent call to arrange a time.";
      return slots.map((s) => `${s.label} (start=${s.start})`).join("\n");
    }

    case "book_showing": {
      const name = str(input.name);
      const start = str(input.start);
      const phone = str(input.phone);
      const email = str(input.email);

      if (!name || !start) return "Missing name or time — ask for it before booking.";
      if (!phone && !email) {
        return "No contact method yet. Ask for a phone number or email before booking.";
      }

      const listingId = str(input.listing_id);
      const address = LISTINGS.find((l) => l.id === listingId)?.address ?? null;

      try {
        const booking = await createBooking({
          start,
          name,
          phone,
          email,
          listingId,
          address,
          notes: str(input.notes),
        });

        await saveLead(conversationId, {
          name,
          phone: phone ?? undefined,
          email: email ?? undefined,
          booked_at: start,
        });

        return `Booked for ${booking.when}. Confirm the time back to them and say an agent will follow up with details.`;
      } catch (err) {
        console.error("book_showing failed:", err);
        return "The calendar did not accept that booking. Apologize briefly, take their preferred time, and say an agent will confirm.";
      }
    }

    default:
      return `Unknown tool: ${name}`;
  }
}

/** The same tools in the shape Vapi expects for its voice assistant. */
export function vapiTools(serverUrl: string) {
  return TOOLS.map((t) => ({
    type: "function" as const,
    async: false,
    server: { url: serverUrl },
    function: {
      name: t.name,
      description: t.description,
      parameters: t.input_schema,
    },
  }));
}
