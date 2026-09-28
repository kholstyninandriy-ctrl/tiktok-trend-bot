import { NextResponse } from "next/server";
import { appUrl, SESSION_COOKIE } from "@/lib/auth";

export const runtime = "nodejs";

export async function GET() {
  const response = NextResponse.redirect(`${appUrl()}/login`);
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
