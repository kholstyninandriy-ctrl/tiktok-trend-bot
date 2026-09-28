import { NextResponse } from "next/server";
import { currentUser } from "@/lib/auth";
import { listConversations } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  if (!(await currentUser())) {
    return NextResponse.json({ error: "Not signed in" }, { status: 401 });
  }
  return NextResponse.json({ conversations: await listConversations() });
}
