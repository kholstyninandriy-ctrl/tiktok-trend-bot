import { NextResponse } from "next/server";
import { appUrl, currentUser } from "@/lib/auth";
import { AGENCY, GREETING, SYSTEM_PROMPT } from "@/lib/prompt";
import { vapiTools } from "@/lib/tools";

export const runtime = "nodejs";

/**
 * Creates (or updates) the Vapi voice assistant from the same prompt and tools
 * the chat widget uses. Run it once from the dashboard after adding
 * VAPI_PRIVATE_KEY; put the returned id into NEXT_PUBLIC_VAPI_ASSISTANT_ID.
 */
export async function POST(request: Request) {
  if (!(await currentUser())) {
    return NextResponse.json({ error: "Not signed in" }, { status: 401 });
  }

  const key = process.env.VAPI_PRIVATE_KEY;
  if (!key) {
    return NextResponse.json(
      { error: "VAPI_PRIVATE_KEY is not set" },
      { status: 400 },
    );
  }

  const existingId = new URL(request.url).searchParams.get("assistantId");

  const body = {
    name: `${AGENCY.name} — Front desk`,
    firstMessage: GREETING,
    server: { url: `${appUrl()}/api/vapi/webhook` },
    model: {
      provider: "anthropic",
      model: process.env.VAPI_MODEL || "claude-sonnet-4-5",
      messages: [{ role: "system", content: SYSTEM_PROMPT }],
      tools: vapiTools(`${appUrl()}/api/vapi/webhook`),
    },
    voice: { provider: "11labs", voiceId: "rachel" },
  };

  const res = await fetch(
    existingId
      ? `https://api.vapi.ai/assistant/${existingId}`
      : "https://api.vapi.ai/assistant",
    {
      method: existingId ? "PATCH" : "POST",
      headers: {
        authorization: `Bearer ${key}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    },
  );

  const text = await res.text();
  if (!res.ok) {
    // Surface Vapi's own message — it names the exact field it rejected.
    return NextResponse.json({ error: text }, { status: res.status });
  }

  const assistant = JSON.parse(text) as { id: string };
  return NextResponse.json({
    assistantId: assistant.id,
    next: "Put this id into NEXT_PUBLIC_VAPI_ASSISTANT_ID and redeploy.",
  });
}
