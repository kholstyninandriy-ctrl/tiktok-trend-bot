import Script from "next/script";
import { AGENCY } from "@/lib/prompt";
import { formatPrice, LISTINGS, mapsEmbedUrl, mapsLinkUrl } from "@/lib/listings";

/**
 * Stand-in for the agency's own website. The point of this page is to show the
 * widget behaving exactly as it would once embedded on a real client site.
 */
export default function Home() {
  return (
    <>
      <div className="notice">
        Demo site — the chat bubble in the corner is the product.{" "}
        <a href="/dashboard">Open the lead dashboard →</a>
      </div>

      <div className="wrap">
        <nav className="nav">
          <div className="brand">
            Coastal<span>.</span> Realty Group
          </div>
          <div className="nav-links">
            <a href="#listings">Listings</a>
            <a href="#listings">Neighborhoods</a>
            <a href="#listings">Sell</a>
            <a href="/dashboard">Dashboard</a>
          </div>
        </nav>

        <section className="hero">
          <div>
            <h1>
              Find the right home in {AGENCY.city}, without the guesswork.
            </h1>
            <p>
              Thirty years in the city, and an agent who actually picks up. Browse
              our current listings, or ask our front desk anything — day or night.
            </p>
            <a className="btn" href="#listings">
              Browse listings
            </a>
          </div>
          <div className="hero-img" />
        </section>

        <section id="listings">
          <h2 className="section-title">Current listings</h2>
          <p className="section-sub">
            {LISTINGS.length} active properties · updated daily
          </p>

          <div className="listings">
            {LISTINGS.map((listing) => {
              const embed = mapsEmbedUrl(listing.address);
              return (
                <article className="card" key={listing.id}>
                  {embed ? (
                    <iframe
                      className="card-map"
                      src={embed}
                      loading="lazy"
                      title={`Map of ${listing.address}`}
                      referrerPolicy="no-referrer-when-downgrade"
                    />
                  ) : (
                    <a
                      className="card-map"
                      href={mapsLinkUrl(listing.address)}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`Open ${listing.address} in Google Maps`}
                    />
                  )}
                  <div className="card-body">
                    <div className="card-price">{formatPrice(listing.price)}</div>
                    <div className="card-title">{listing.title}</div>
                    <div className="card-meta">
                      {listing.bedrooms} bd · {listing.bathrooms} ba ·{" "}
                      {listing.sqft.toLocaleString("en-US")} sq ft
                      <br />
                      {listing.neighborhood}
                    </div>
                    {listing.highlights.slice(0, 2).map((h) => (
                      <span className="tag" key={h}>
                        {h}
                      </span>
                    ))}
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <footer className="footer">
          {AGENCY.name} · {AGENCY.phone} · {AGENCY.hours}
          <br />
          Fictional agency used for demonstration purposes.
        </footer>
      </div>

      <Script src="/embed.js" strategy="afterInteractive" />
    </>
  );
}
