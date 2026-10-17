"use client";

/**
 * M6 operations report: IPD occupancy (current inpatients, admissions,
 * discharges and the bed split), finance for the period (succeeded
 * payments − refunds − recorded expenses, split by category), and inventory
 * health — every figure tenant-scoped and read-only.
 */
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ApiError, ReportSummary, api } from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

export default function ReportsPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [summary, setSummary] = useState<ReportSummary | null>(null);
  const [from, setFrom] = useState(
    new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10)
  );
  const [to, setTo] = useState(new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api.get<ReportSummary>(
        `/reports/summary?from=${from}&to=${to}`
      );
      setSummary(data);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? String(err.message)
          : "Could not load the report."
      );
    }
  }, [from, to]);

  useEffect(() => {
    void load();
  }, [load]);

  const money = (n: number) => `₹${Number(n).toLocaleString("en-IN")}`;
  const pct = (occupied: number, total: number) =>
    total ? `${Math.round((occupied / total) * 100)}%` : "—";

  return (
    <AppShell active="reports">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("reports", lang)}</h1>

        <section className="card">
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="rf">{t("period", lang)} — from</label>
              <input
                id="rf"
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="rt">to</label>
              <input
                id="rt"
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
              />
            </div>
            <button
              className="primary"
              type="button"
              style={{ alignSelf: "end" }}
              onClick={() => void load()}
            >
              {t("reports", lang)}
            </button>
          </div>
        </section>

        {summary && (
          <>
            <section className="card" style={{ marginTop: "1.25rem" }}>
              <h3 style={{ marginTop: 0 }}>{t("ipd", lang)}</h3>
              <div className="grid kpis">
                <div className="kpi">
                  <div className="value">{summary.ipd.currently_admitted}</div>
                  <div className="label">{t("inpatients", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">{summary.ipd.admissions_in_period}</div>
                  <div className="label">{t("admissionsLabel", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">{summary.ipd.discharges_in_period}</div>
                  <div className="label">{t("discharges", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">{summary.ipd.deaths_in_period}</div>
                  <div className="label">{t("deaths", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">
                    {summary.ipd.beds_occupied}/{summary.ipd.beds_total}
                  </div>
                  <div className="label">
                    {t("occupancy", lang)} ·{" "}
                    {pct(summary.ipd.beds_occupied, summary.ipd.beds_total)}
                  </div>
                </div>
                <div className="kpi">
                  <div className="value">{summary.ipd.beds_maintenance}</div>
                  <div className="label">{t("maintenance", lang)}</div>
                </div>
              </div>
            </section>

            <section className="card" style={{ marginTop: "1.25rem" }}>
              <h3 style={{ marginTop: 0 }}>
                {t("billing", lang)} · {summary.from_date} → {summary.to_date}
              </h3>
              <div className="grid kpis">
                <div className="kpi">
                  <div className="value">{money(summary.finance.revenue)}</div>
                  <div className="label">{t("revenue", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">{money(summary.finance.refunds)}</div>
                  <div className="label">{t("refundsLabel", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">{money(summary.finance.expenses)}</div>
                  <div className="label">{t("expenses", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">{money(summary.finance.net)}</div>
                  <div className="label">{t("net", lang)}</div>
                </div>
              </div>
              <table style={{ marginTop: "1rem" }}>
                <thead>
                  <tr>
                    <th>{t("byCategory", lang)}</th>
                    <th>{t("total", lang)}</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.finance.by_category.map((c) => (
                    <tr key={c.category}>
                      <td>{c.category}</td>
                      <td>{money(c.total)}</td>
                    </tr>
                  ))}
                  {summary.finance.by_category.length === 0 && (
                    <tr>
                      <td colSpan={2} className="muted">
                        No expenses booked in this period.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </section>

            <section className="card" style={{ marginTop: "1.25rem" }}>
              <h3 style={{ marginTop: 0 }}>{t("inventory", lang)}</h3>
              <div className="grid kpis">
                <div className="kpi">
                  <div className="value">{summary.inventory.items_active}</div>
                  <div className="label">{t("activeItems", lang)}</div>
                </div>
                <div className="kpi">
                  <div className="value">{summary.inventory.low_stock.length}</div>
                  <div className="label">
                    {t("lowStock", lang)} — {t("lowStockItems", lang)}
                  </div>
                </div>
              </div>
              {summary.inventory.low_stock.length > 0 && (
                <table style={{ marginTop: "1rem" }}>
                  <thead>
                    <tr>
                      <th>{t("itemName", lang)}</th>
                      <th>{t("onHand", lang)}</th>
                      <th>{t("reorderLevel", lang)}</th>
                      <th>{t("unit", lang)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.inventory.low_stock.map((i) => (
                      <tr key={i.id}>
                        <td>{i.name}</td>
                        <td>{i.quantity_on_hand}</td>
                        <td>{i.reorder_level}</td>
                        <td>{i.unit}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </>
        )}

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </main>
    </AppShell>
  );
}