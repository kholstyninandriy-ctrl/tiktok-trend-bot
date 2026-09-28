import { NextResponse } from "next/server";
import { appUrl, googleAuthUrl, isAuthConfigured } from "@/lib/auth";

export const runtime = "nodejs";

export async function GET() {
  if (!isAuthConfigured()) {
    // Google login not set up yet — the dashboard is open in demo mode.
    return NextResponse.redirect(`${appUrl()}/dashboard`);
  }
  return NextResponse.redirect(googleAuthUrl());
}
