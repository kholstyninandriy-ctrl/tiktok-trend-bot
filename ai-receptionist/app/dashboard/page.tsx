import { redirect } from "next/navigation";
import { currentUser, isAuthConfigured } from "@/lib/auth";
import { isCalendarConfigured } from "@/lib/calendar";
import { isClaudeConfigured } from "@/lib/claude";
import { isSupabaseConfigured } from "@/lib/store";
import { listConversations } from "@/lib/store";
import type { Conversation } from "@/lib/types";
import "./dashboard.css";

export const dynamic = "force-dynamic";

function money(min: number | null, max: number | null): string {
  if (!min && !max) return "—";
  const fmt = (n: number) => `$${Math.round(n / 1000)}k`;
  if (min && max) return `${fmt(min)} – ${fmt(max)}`;
  return fmt((min ?? max)!);
}

function status(c: Conversation) {
  if (c.lead?.booked_at) return { cls: "booked", text: "Booked" };
  if (c.lead?.phone || c.lead?.email) return { cls: "warm", text: "Contact captured" };
  return { cls: "cold", text: "Browsing" };
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default async function DashboardPage() {
  const user = await currentUser();
  if (!user) redirect("/login");

  const conversations = await listConversations();
  const withContact = conversations.filter(
    (c) => c.lead?.phone || c.lead?.email,
  ).length;
  const booked = conversations.filter((c) => c.lead?.booked_at).length;
  const rate = conversations.length
    ? Math.round((withContact / conversations.length) * 100)
    : 0;

  const integrations = [
    { label: "Claude", on: isClaudeConfigured() },
    { label: "Supabase", on: isSupabaseConfigured() },
    { label: "Google Calendar", on: isCalendarConfigured() },
    { label: "Google login", on: isAuthConfigured() },
    { label: "Google Maps", on: Boolean(process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY) },
    { label: "Vapi voice", on: Boolean(process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY) },
  ];

  return (
    <div className="d-shell">
      <header className="d-top">
        <div className="wrap d-top-inner">
          <div className="brand">
            Coastal<span>.</span> Front desk
          </div>
          <div className="d-user">
            {isAuthConfigured() ? (
              <>
                <span>{user}</span>
                <a href="/api/auth/logout">Sign out</a>
              </>
            ) : (
              <span>Demo mode — Google login not configured</span>
            )}
            <a href="/">View site</a>
          </div>
        </div>
      </header>

      <main className="wrap d-main">
        <h1 className="d-h1">Leads</h1>
        <p className="d-sub">
          Every conversation the receptionist handled, and what it learned.
        </p>

        <div className="d-setup">
          {integrations.map((i) => (
            <span className={`d-chip ${i.on ? "on" : "off"}`} key={i.label}>
              <i />
              {i.label}
              {i.on ? "" : " · not set"}
            </span>
          ))}
        </div>

        <div className="d-stats">
          <div className="d-stat">
            <div className="d-stat-n">{conversations.length}</div>
            <div className="d-stat-l">Conversations</div>
          </div>
          <div className="d-stat">
            <div className="d-stat-n">{withContact}</div>
            <div className="d-stat-l">Contacts captured</div>
          </div>
          <div className="d-stat accent">
            <div className="d-stat-n">{booked}</div>
            <div className="d-stat-l">Showings booked</div>
          </div>
          <div className="d-stat">
            <div className="d-stat-n">{rate}%</div>
            <div className="d-stat-l">Capture rate</div>
          </div>
        </div>

        <section className="d-panel">
          <div className="d-panel-head">
            <h2>Recent conversations</h2>
            <small>
              {isSupabaseConfigured()
                ? "Live from Supabase"
                : "Sample data — connect Supabase to store real conversations"}
            </small>
          </div>

          {conversations.length === 0 ? (
            <div className="d-empty">
              No conversations yet. Open the demo site and talk to the widget.
            </div>
          ) : (
            <table className="d-table">
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Looking for</th>
                  <th>Budget</th>
                  <th>Timeline</th>
                  <th>Channel</th>
                  <th>Status</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {conversations.map((c) => {
                  const s = status(c);
                  const lead = c.lead;
                  return (
                    <tr key={c.id}>
                      <td>
                        <div className="d-name">{lead?.name ?? "Unknown visitor"}</div>
                        <div className="d-dim">
                          {lead?.phone ?? lead?.email ?? "no contact yet"}
                        </div>
                      </td>
                      <td>
                        {lead?.neighborhood ?? "—"}
                        <div className="d-dim">
                          {[lead?.bedrooms ? `${lead.bedrooms} bd` : null, lead?.property_type]
                            .filter(Boolean)
                            .join(" · ") || "—"}
                        </div>
                      </td>
                      <td>{money(lead?.budget_min ?? null, lead?.budget_max ?? null)}</td>
                      <td>{lead?.timeline ?? "—"}</td>
                      <td>
                        <span className={`d-pill ${c.channel}`}>{c.channel}</span>
                      </td>
                      <td>
                        <span className={`d-pill ${s.cls}`}>{s.text}</span>
                      </td>
                      <td className="d-dim">
                        {when(c.started_at)}
                        <div>{c.message_count} msgs</div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </section>
      </main>
    </div>
  );
}
