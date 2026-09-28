import { NextResponse } from "next/server";
import { appendMessages, createConversation } from "@/lib/store";
import { runTool } from "@/lib/tools";
import type { ChatMessage } from "@/lib/types";

export const runtime = "nodejs";

/**
 * Vapi calls this while a voice call is running:
 *   - "tool-calls"          → run the same tools the chat widget uses
 *   - "end-of-call-report"  → store the transcript next to the chat ones
 */

interface VapiToolCall {
  id?: string;
  toolCallId?: string;
  function?: { name?: string; arguments?: unknown };
  name?: string;
  arguments?: unknown;
}

function parseArgs(raw: unknown): Record<string, unknown> {
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return {};
    }
  }
  return (raw as Record<string, unknown>) ?? {};
}

export async function POST(request: Request) {
  const expected = process.env.VAPI_WEBHOOK_SECRET;
  if (expected && request.headers.get("x-vapi-secret") !== expected) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let payload: Record<string, unknown>;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const message = (payload.message ?? payload) as Record<string, unknown>;
  const type = message.type as string | undefined;

  const call = message.call as Record<string, unknown> | undefined;
  const metadata = (call?.metadata ?? {}) as Record<string, unknown>;
  const conversationId =
    (metadata.conversationId as string) ?? (call?.id as string) ?? "voice-unknown";

  if (type === "tool-calls") {
    const calls = (message.toolCallList ??
      message.toolCalls ??
      []) as VapiToolCall[];

    await createConversation(conversationId, "voice", null);

    const results = await Promise.all(
      calls.map(async (c) => {
        const name = c.function?.name ?? c.name ?? "";
        const args = parseArgs(c.function?.arguments ?? c.arguments);
        const result = await runTool(name, args, conversationId);
        return { toolCallId: c.id ?? c.toolCallId ?? "", result };
      }),
    );

    return NextResponse.json({ results });
  }

  if (type === "end-of-call-report") {
    const artifact = message.artifact as Record<string, unknown> | undefined;
    const turns = (artifact?.messages ?? []) as Array<{
      role?: string;
      message?: string;
      content?: string;
    }>;

    await createConversation(conversationId, "voice", null);
    await appendMessages(
      conversationId,
      turns
        .filter((t) => t.role === "user" || t.role === "bot" || t.role === "assistant")
        .map<ChatMessage>((t) => ({
          role: t.role === "user" ? "user" : "assistant",
          content: t.message ?? t.content ?? "",
        }))
        .filter((m) => m.content),
    );

    return NextResponse.json({ ok: true });
  }

  return NextResponse.json({ ok: true });
}
