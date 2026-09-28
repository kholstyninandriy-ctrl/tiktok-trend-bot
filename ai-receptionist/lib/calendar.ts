import crypto from "node:crypto";

/**
 * Google Calendar through a service account — no OAuth consent screen.
 *
 * Setup: create a service account, download its JSON key, then share the
 * target calendar with the service account's email ("Make changes to events").
 *
 * Known limitation: a service account cannot email attendee invitations
 * without domain-wide delegation, so the lead's contact details go into the
 * event description instead of the attendee list.
 */

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const SCOPE = "https://www.googleapis.com/auth/calendar";

export function isCalendarConfigured(): boolean {
  return Boolean(
    process.env.GOOGLE_SERVICE_ACCOUNT_EMAIL &&
      process.env.GOOGLE_PRIVATE_KEY &&
      process.env.GOOGLE_CALENDAR_ID,
  );
}

function timezone(): string {
  return process.env.BOOKING_TIMEZONE || "America/New_York";
}

function b64url(input: string | Buffer): string {
  return Buffer.from(input)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

let cachedToken: { value: string; expiresAt: number } | null = null;

async function accessToken(): Promise<string> {
  if (cachedToken && cachedToken.expiresAt > Date.now() + 60_000) {
    return cachedToken.value;
  }

  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const claims = b64url(
    JSON.stringify({
      iss: process.env.GOOGLE_SERVICE_ACCOUNT_EMAIL,
      scope: SCOPE,
      aud: TOKEN_URL,
      iat: now,
      exp: now + 3600,
    }),
  );

  // Vercel's env editor stores the key with literal \n sequences.
  const privateKey = (process.env.GOOGLE_PRIVATE_KEY || "").replace(/\\n/g, "\n");
  const signature = b64url(
    crypto.createSign("RSA-SHA256").update(`${header}.${claims}`).sign(privateKey),
  );

  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: `${header}.${claims}.${signature}`,
    }),
  });

  if (!res.ok) {
    throw new Error(`Google token exchange failed: ${await res.text()}`);
  }

  const json = (await res.json()) as { access_token: string; expires_in: number };
  cachedToken = {
    value: json.access_token,
    expiresAt: Date.now() + json.expires_in * 1000,
  };
  return cachedToken.value;
}

export interface SlotSuggestion {
  start: string; // ISO
  label: string; // human-readable, e.g. "Tue, Oct 7 at 2:00 PM"
}

/** Weekday 10:00–17:00 slots over the next few days, minus anything busy. */
export async function suggestSlots(count = 4): Promise<SlotSuggestion[]> {
  const candidates: Date[] = [];
  const cursor = new Date();
  cursor.setMinutes(0, 0, 0);
  cursor.setHours(cursor.getHours() + 2);

  while (candidates.length < count * 3) {
    const day = cursor.getDay();
    const hour = cursor.getHours();
    if (day !== 0 && day !== 6 && hour >= 10 && hour <= 16) {
      candidates.push(new Date(cursor));
    }
    cursor.setHours(cursor.getHours() + 2);
    if (cursor.getHours() > 16) {
      cursor.setDate(cursor.getDate() + 1);
      cursor.setHours(10, 0, 0, 0);
    }
  }

  let free = candidates;
  if (isCalendarConfigured()) {
    try {
      free = await filterBusy(candidates);
    } catch {
      // If the free/busy lookup fails, still offer slots rather than nothing.
    }
  }

  return free.slice(0, count).map((d) => ({
    start: d.toISOString(),
    label: d.toLocaleString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZone: timezone(),
    }),
  }));
}

async function filterBusy(candidates: Date[]): Promise<Date[]> {
  const token = await accessToken();
  const timeMin = candidates[0].toISOString();
  const timeMax = new Date(
    candidates[candidates.length - 1].getTime() + 36e5,
  ).toISOString();

  const res = await fetch("https://www.googleapis.com/calendar/v3/freeBusy", {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      timeMin,
      timeMax,
      items: [{ id: process.env.GOOGLE_CALENDAR_ID }],
    }),
  });

  if (!res.ok) return candidates;

  const json = (await res.json()) as {
    calendars?: Record<string, { busy?: Array<{ start: string; end: string }> }>;
  };
  const busy =
    json.calendars?.[process.env.GOOGLE_CALENDAR_ID!]?.busy ?? [];

  return candidates.filter((slot) => {
    const s = slot.getTime();
    const e = s + 36e5;
    return !busy.some(
      (b) => s < new Date(b.end).getTime() && e > new Date(b.start).getTime(),
    );
  });
}

export interface BookingRequest {
  start: string;
  name: string;
  email?: string | null;
  phone?: string | null;
  listingId?: string | null;
  address?: string | null;
  notes?: string | null;
}

export interface BookingResult {
  ok: boolean;
  when: string;
  eventLink: string | null;
  simulated: boolean;
}

export async function createBooking(req: BookingRequest): Promise<BookingResult> {
  const start = new Date(req.start);
  const when = start.toLocaleString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone(),
  });

  if (!isCalendarConfigured()) {
    return { ok: true, when, eventLink: null, simulated: true };
  }

  const token = await accessToken();
  const contact = [
    `Lead: ${req.name}`,
    req.phone ? `Phone: ${req.phone}` : null,
    req.email ? `Email: ${req.email}` : null,
    req.listingId ? `Listing: ${req.listingId}` : null,
    req.notes ? `Notes: ${req.notes}` : null,
    "",
    "Booked automatically by the AI receptionist.",
  ]
    .filter(Boolean)
    .join("\n");

  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(
      process.env.GOOGLE_CALENDAR_ID!,
    )}/events`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        summary: `Showing — ${req.name}`,
        description: contact,
        location: req.address ?? undefined,
        start: { dateTime: start.toISOString(), timeZone: timezone() },
        end: {
          dateTime: new Date(start.getTime() + 36e5).toISOString(),
          timeZone: timezone(),
        },
      }),
    },
  );

  if (!res.ok) {
    throw new Error(`Calendar insert failed: ${await res.text()}`);
  }

  const event = (await res.json()) as { htmlLink?: string };
  return {
    ok: true,
    when,
    eventLink: event.htmlLink ?? null,
    simulated: false,
  };
}
