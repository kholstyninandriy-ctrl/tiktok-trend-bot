import { redirect } from "next/navigation";
import { currentUser, isAuthConfigured } from "@/lib/auth";

export const dynamic = "force-dynamic";

const ERRORS: Record<string, string> = {
  not_allowed: "That Google account is not on the allow-list for this dashboard.",
  google_failed: "Google sign-in failed. Try again.",
  missing_code: "Google did not return a login code. Try again.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  if (await currentUser()) redirect("/dashboard");

  const { error } = await searchParams;

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "var(--surface)",
        padding: 24,
      }}
    >
      <div
        style={{
          background: "#fff",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: "38px 34px",
          maxWidth: 380,
          width: "100%",
          textAlign: "center",
        }}
      >
        <div className="brand" style={{ marginBottom: 10 }}>
          Coastal<span>.</span> Front desk
        </div>
        <p style={{ color: "var(--muted)", fontSize: 14, margin: "0 0 24px" }}>
          Sign in to see the leads your receptionist captured.
        </p>

        {error && (
          <p style={{ color: "#a4342a", fontSize: 13, margin: "0 0 16px" }}>
            {ERRORS[error] ?? "Something went wrong."}
          </p>
        )}

        {isAuthConfigured() ? (
          <a className="btn" href="/api/auth/login" style={{ width: "100%" }}>
            Continue with Google
          </a>
        ) : (
          <>
            <a className="btn" href="/dashboard" style={{ width: "100%" }}>
              Open dashboard
            </a>
            <p style={{ color: "var(--muted)", fontSize: 12, marginTop: 16 }}>
              Google login is not configured yet, so the dashboard is open.
              Add GOOGLE_OAUTH_CLIENT_ID to lock it down.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
