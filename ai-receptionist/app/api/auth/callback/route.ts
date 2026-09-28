import { NextResponse } from "next/server";
import {
  appUrl,
  createSessionValue,
  exchangeCode,
  isAllowed,
  SESSION_COOKIE,
  sessionCookieOptions,
} from "@/lib/auth";

export const runtime = "nodejs";

export async function GET(request: Request) {
  const code = new URL(request.url).searchParams.get("code");
  if (!code) {
    return NextResponse.redirect(`${appUrl()}/login?error=missing_code`);
  }

  try {
    const user = await exchangeCode(code);

    if (!isAllowed(user.email)) {
      return NextResponse.redirect(`${appUrl()}/login?error=not_allowed`);
    }

    const response = NextResponse.redirect(`${appUrl()}/dashboard`);
    response.cookies.set(
      SESSION_COOKIE,
      createSessionValue(user.email),
      sessionCookieOptions(),
    );
    return response;
  } catch (err) {
    console.error("Google callback failed:", err);
    return NextResponse.redirect(`${appUrl()}/login?error=google_failed`);
  }
}
