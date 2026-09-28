import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { ChatMessage, Conversation, Lead } from "./types";

/**
 * Storage layer with two backends:
 *   - Supabase, when NEXT_PUBLIC_SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are set
 *   - an in-memory store otherwise, seeded with demo rows
 *
 * The fallback keeps the dashboard and the widget usable before any key exists.
 * It is per-process, so it resets on restart — fine for a demo, not for prod.
 */

let client: SupabaseClient | null = null;

export function isSupabaseConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.SUPABASE_SERVICE_ROLE_KEY,
  );
}

function db(): SupabaseClient | null {
  if (!isSupabaseConfigured()) return null;
  if (!client) {
    client = createClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.SUPABASE_SERVICE_ROLE_KEY!,
      { auth: { persistSession: false } },
    );
  }
  return client;
}

// ── in-memory fallback ───────────────────────────────────────

interface MemRow {
  conversation: Conversation;
  messages: ChatMessage[];
}

const mem = new Map<string, MemRow>();

function seed() {
  if (mem.size > 0) return;
  const now = Date.now();
  const demo: Array<[string, Partial<Lead>, number, string]> = [
    [
      "Booked a Saturday showing",
      {
        name: "Marcus Bell",
        email: "marcus.bell@example.com",
        phone: "+1 415 555 0142",
        budget_min: 1_100_000,
        budget_max: 1_400_000,
        neighborhood: "Nob Hill",
        property_type: "condo",
        bedrooms: 2,
        timeline: "Within 30 days",
        booked_at: new Date(now + 2 * 864e5).toISOString(),
      },
      14,
      "chat",
    ],
    [
      "Asked about school districts",
      {
        name: "Priya Raman",
        email: "praman@example.com",
        phone: null,
        budget_min: 1_200_000,
        budget_max: 1_500_000,
        neighborhood: "Inner Sunset",
        property_type: "single-family",
        bedrooms: 4,
        timeline: "3-6 months",
      },
      9,
      "chat",
    ],
    [
      "Called after hours, left budget",
      {
        name: "Dan Whitaker",
        email: null,
        phone: "+1 628 555 0199",
        budget_min: 700_000,
        budget_max: 800_000,
        neighborhood: "Mission Bay",
        property_type: "condo",
        bedrooms: 1,
        timeline: "Just browsing",
      },
      6,
      "voice",
    ],
  ];

  demo.forEach(([note, lead, count, channel], i) => {
    const id = `demo-${i + 1}`;
    mem.set(id, {
      conversation: {
        id,
        channel: channel as "chat" | "voice",
        source_url: "https://coastalrealtygroup.example.com/",
        started_at: new Date(now - (i + 1) * 36e5).toISOString(),
        message_count: count,
        lead: {
          id: `lead-${i + 1}`,
          conversation_id: id,
          name: null,
          email: null,
          phone: null,
          budget_min: null,
          budget_max: null,
          neighborhood: null,
          property_type: null,
          bedrooms: null,
          timeline: null,
          notes: note,
          booked_at: null,
          created_at: new Date(now - (i + 1) * 36e5).toISOString(),
          ...lead,
        } as Lead,
      },
      messages: [],
    });
  });
}

// ── public API ───────────────────────────────────────────────

export async function createConversation(
  id: string,
  channel: "chat" | "voice",
  sourceUrl: string | null,
): Promise<void> {
  const supa = db();
  if (supa) {
    await supa
      .from("conversations")
      .upsert({ id, channel, source_url: sourceUrl }, { onConflict: "id" });
    return;
  }
  seed();
  if (!mem.has(id)) {
    mem.set(id, {
      conversation: {
        id,
        channel,
        source_url: sourceUrl,
        started_at: new Date().toISOString(),
        message_count: 0,
        lead: null,
      },
      messages: [],
    });
  }
}

export async function appendMessages(
  conversationId: string,
  messages: ChatMessage[],
): Promise<void> {
  if (messages.length === 0) return;
  const supa = db();
  if (supa) {
    await supa.from("messages").insert(
      messages.map((m) => ({
        conversation_id: conversationId,
        role: m.role,
        content: m.content,
      })),
    );
    return;
  }
  const row = mem.get(conversationId);
  if (row) {
    row.messages.push(...messages);
    row.conversation.message_count = row.messages.length;
  }
}

export async function saveLead(
  conversationId: string,
  fields: Partial<Lead>,
): Promise<void> {
  const supa = db();
  if (supa) {
    await supa
      .from("leads")
      .upsert(
        { conversation_id: conversationId, ...fields },
        { onConflict: "conversation_id" },
      );
    return;
  }
  const row = mem.get(conversationId);
  if (!row) return;
  const existing = row.conversation.lead;
  row.conversation.lead = {
    id: existing?.id ?? `lead-${conversationId}`,
    conversation_id: conversationId,
    name: null,
    email: null,
    phone: null,
    budget_min: null,
    budget_max: null,
    neighborhood: null,
    property_type: null,
    bedrooms: null,
    timeline: null,
    notes: null,
    booked_at: null,
    created_at: existing?.created_at ?? new Date().toISOString(),
    ...existing,
    ...fields,
  } as Lead;
}

export async function listConversations(): Promise<Conversation[]> {
  const supa = db();
  if (supa) {
    const { data } = await supa
      .from("conversations")
      .select("*, leads(*), messages(count)")
      .order("started_at", { ascending: false })
      .limit(100);

    return (data ?? []).map((row: Record<string, unknown>) => ({
      id: row.id as string,
      channel: (row.channel as "chat" | "voice") ?? "chat",
      source_url: (row.source_url as string) ?? null,
      started_at: row.started_at as string,
      message_count:
        (row.messages as Array<{ count: number }> | null)?.[0]?.count ?? 0,
      lead: (row.leads as Lead[] | null)?.[0] ?? null,
    }));
  }
  seed();
  return [...mem.values()]
    .map((r) => r.conversation)
    .sort((a, b) => b.started_at.localeCompare(a.started_at));
}

export async function getMessages(
  conversationId: string,
): Promise<ChatMessage[]> {
  const supa = db();
  if (supa) {
    const { data } = await supa
      .from("messages")
      .select("role, content")
      .eq("conversation_id", conversationId)
      .order("created_at", { ascending: true });
    return (data ?? []) as ChatMessage[];
  }
  return mem.get(conversationId)?.messages ?? [];
}
