"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, Me, api } from "@/lib/api";
import { clearTokens, getAccessToken } from "@/lib/auth";
import { LANGS, Lang, getLang, setLang, t } from "@/lib/i18n";

const NAV = [
  { key: "dashboard", href: "#", enabled: true },
  { key: "patients", href: "#", enabled: false },
  { key: "queue", href: "#", enabled: false },
];

export default function DashboardPage() {
  const router = useRouter();
  const [lang, setLangState] = useState<Lang>("en");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!getAccessToken()) {
      router.replace("/login");
      return;
    }
    try {
      setMe(await api.get<Me>("/auth/me"));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
      } else {
        setError("Could not load your session.");
      }
    }
  }, [router]);

  useEffect(() => {
    setLangState(getLang());
    void load();
  }, [load]);

  function signOut() {
    void api.post("/auth/logout", {}).catch(() => undefined);
    clearTokens();
    router.replace("/login");
  }

  if (error) {
    return (
      <main className="page">
        <p className="error" role="alert">
          {error}
        </p>
      </main>
    );
  }

  if (!me) {
    return (
      <main className="page">
        <p className="muted">Loading…</p>
      </main>
    );
  }

  return (
    <>
      <header className="app-header">
        <span className="brand">{t("brand", lang)}</span>
        <span className="context">
          {me.organization.name} <span className="muted">/</span>{" "}
          {me.user.full_name}
        </span>
        <span className="spacer" />
        <span className="lang-switch" role="group" aria-label="Language">
          {LANGS.map((l) => (
            <button
              key={l.code}
              type="button"
              aria-pressed={lang === l.code}
              onClick={() => {
                setLang(l.code);
                setLangState(l.code);
              }}
            >
              {l.native}
            </button>
          ))}
        </span>
        <button className="ghost" type="button" onClick={signOut}>
          {t("logout", lang)}
        </button>
      </header>

      <nav className="layout-nav" aria-label="Modules">
        {NAV.map((item) => (
          <a
            key={item.key}
            href={item.href}
            className={item.key === "dashboard" ? "active" : undefined}
            aria-disabled={!item.enabled}
            title={item.enabled ? undefined : "Coming in M2"}
          >
            {t(item.key, lang)}
          </a>
        ))}
        <a href="#" aria-disabled="true" title="Coming in M2">
          {t("patients", lang)}
        </a>
      </nav>

      <main className="page">
        <div className="grid kpis">
          {[
            { label: t("waiting", lang), value: "—" },
            { label: t("seen", lang), value: "—" },
            { label: t("followUps", lang), value: "—" },
            { label: t("outstanding", lang), value: "—" },
          ].map((kpi) => (
            <div className="card kpi" key={kpi.label}>
              <div className="value">{kpi.value}</div>
              <div className="label">{kpi.label}</div>
            </div>
          ))}
        </div>

        <div className="card" style={{ marginTop: "1.25rem" }}>
          <h2 style={{ marginTop: 0 }}>{t("welcome", lang)}, {me.user.full_name}</h2>
          <p className="notice">{t("m1Notice", lang)}</p>
          <p className="muted" style={{ marginBottom: "0.4rem" }}>
            {t("org", lang)}: <strong>{me.organization.name}</strong> (
            {me.organization.slug}) — {t("role", lang)}:{" "}
            <strong>{me.role}</strong>
          </p>
          <p className="muted" style={{ margin: 0 }}>
            {t("permissions", lang)}:
            {me.permissions.map((p) => (
              <span className="tag" key={p}>
                {p}
              </span>
            ))}
          </p>
        </div>

        <div className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>M1 module checklist</h3>
          <ul className="muted">
            <li>✅ Authentication, sessions, refresh rotation</li>
            <li>✅ Multi-tenant organizations, branches, RBAC, audit trail</li>
            <li>⬜ Patients, appointments, OPD queue — milestone M2</li>
            <li>⬜ Consultation, e-prescription, billing — milestone M3</li>
            <li>⬜ Offline Android app — milestone M4</li>
          </ul>
        </div>
      </main>
    </>
  );
}
