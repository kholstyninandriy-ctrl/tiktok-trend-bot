import crypto from "node:crypto";
import { cookies } from "next/headers";

/**
 * Google sign-in for /dashboard. Plain OAuth 2.0 over fetch — no auth library.
 *
 * When GOOGLE_OAUTH_CLIENT_ID is not set the dashboard opens unauthenticated
 * (demo mode) so the product is viewable before Google is configured.
 */

export const SESSION_COOKIE = "ar_session";
const MAX_AGE_SECONDS = 60 * 60 * 24 * 7;

export function isAuthConfigured(): boolean {
  return Boolean(
    process.env.GOOGLE_OAUTH_CLIENT_ID &&
      process.env.GOOGLE_OAUTH_CLIENT_SECRET &&
      process.env.AUTH_SECRET,
  );
}

export function appUrl(): string {
  return (process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000").replace(
    /\/$/,
    "",
  );
}

function redirectUri(): string {
  return `${appUrl()}/api/auth/callback`;
}

export function googleAuthUrl(): string {
  const params = new URLSearchParams({
    client_id: process.env.GOOGLE_OAUTH_CLIENT_ID!,
    redirect_uri: redirectUri(),
    response_type: "code",
    scope: "openid email profile",
    access_type: "online",
    prompt: "select_account",
  });
  return `https://accounts.google.com/o/oauth2/v2/auth?${params}`;
}

export interface GoogleUser {
  email: string;
  name: string;
  picture: string | null;
}

export async function exchangeCode(code: string): Promise<GoogleUser> {
  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: process.env.GOOGLE_OAUTH_CLIENT_ID!,
      client_secret: process.env.GOOGLE_OAUTH_CLIENT_SECRET!,
      redirect_uri: redirectUri(),
      grant_type: "authorization_code",
    }),
  });

  if (!tokenRes.ok) {
    throw new Error(`Google token exchange failed: ${await tokenRes.text()}`);
  }

  const { access_token } = (await tokenRes.json()) as { access_token: string };

  const infoRes = await fetch(
    "https://www.googleapis.com/oauth2/v2/userinfo",
    { headers: { authorization: `Bearer ${access_token}` } },
  );

  if (!infoRes.ok) throw new Error("Could not read Google profile");

  const info = (await infoRes.json()) as {
    email: string;
    name?: string;
    picture?: string;
  };

  return {
    email: info.email,
    name: info.name ?? info.email,
    picture: info.picture ?? null,
  };
}

/** An empty allow-list lets any Google account in — set it before going public. */
export function isAllowed(email: string): boolean {
  const raw = process.env.DASHBOARD_ALLOWED_EMAILS?.trim();
  if (!raw) return true;
  return raw
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .includes(email.toLowerCase());
}

function sign(payload: string): string {
  return crypto
    .createHmac("sha256", process.env.AUTH_SECRET!)
    .update(payload)
    .digest("base64url");
}

export function createSessionValue(email: string): string {
  const payload = Buffer.from(
    JSON.stringify({ email, exp: Date.now() + MAX_AGE_SECONDS * 1000 }),
  ).toString("base64url");
  return `${payload}.${sign(payload)}`;
}

export function sessionCookieOptions() {
  return {
    httpOnly: true,
    secure: appUrl().startsWith("https://"),
    sameSite: "lax" as const,
    path: "/",
    maxAge: MAX_AGE_SECONDS,
  };
}

function verify(value: string | undefined): string | null {
  if (!value) return null;
  const [payload, signature] = value.split(".");
  if (!payload || !signature) return null;

  const expected = sign(payload);
  const a = Buffer.from(signature);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;

  try {
    const { email, exp } = JSON.parse(
      Buffer.from(payload, "base64url").toString(),
    ) as { email: string; exp: number };
    if (Date.now() > exp) return null;
    return email;
  } catch {
    return null;
  }
}

/** Returns the signed-in email, or null. In demo mode returns a placeholder. */
export async function currentUser(): Promise<string | null> {
  if (!isAuthConfigured()) return "demo-mode";
  const store = await cookies();
  return verify(store.get(SESSION_COOKIE)?.value);
}
