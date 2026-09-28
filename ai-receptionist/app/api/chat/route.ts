import { NextResponse } from "next/server";
import { respond } from "@/lib/claude";
import { appendMessages, createConversation } from "@/lib/store";
import type { ChatMessage } from "@/lib/types";

export const runtime = "nodejs";

/** The endpoint is public and spends money per call, so cap each visitor. */
const MAX_TURNS = 40;
const WINDOW_MS = 60 * 60 * 1000;
const MAX_PER_IP_PER_HOUR = 120;

const ipHits = new Map<string, number[]>();

function rateLimited(ip: string): boolean {
  const now = Date.now();
  const hits = (ipHits.get(ip) ?? []).filter((t) => now - t < WINDOW_MS);
  hits.push(now);
  ipHits.set(ip, hits);
  return hits.length > MAX_PER_IP_PER_HOUR;
}

export async function POST(request: Request) {
  let body: {
    conversationId?: string;
    messages?: ChatMessage[];
    sourceUrl?: string | null;
  };

  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const { conversationId, messages, sourceUrl } = body;

  if (!conversationId || !Array.isArray(messages) || messages.length === 0) {
    return NextResponse.json(
      { error: "conversationId and messages are required" },
      { status: 400 },
    );
  }

  if (messages.length > MAX_TURNS) {
    return NextResponse.json({
      reply:
        "We've covered a lot here — let me have an agent call you directly. What's the best number?",
    });
  }

  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "local";
  if (rateLimited(ip)) {
    return NextResponse.json(
      { error: "Too many requests", reply: "Give me a moment — try again shortly." },
      { status: 429 },
    );
  }

  try {
    await createConversation(conversationId, "chat", sourceUrl ?? null);

    const history = messages.filter(
      (m) => m.role === "user" || m.role === "assistant",
    );
    const reply = await respond(conversationId, history);

    const lastUser = history[history.length - 1];
    await appendMessages(conversationId, [
      ...(lastUser?.role === "user" ? [lastUser] : []),
      { role: "assistant", content: reply },
    ]);

    return NextResponse.json({ reply });
  } catch (err) {
    console.error("chat route failed:", err);
    return NextResponse.json(
      {
        reply:
          "Sorry — my system hiccuped. Could you say that once more?",
      },
      { status: 200 },
    );
  }
}
