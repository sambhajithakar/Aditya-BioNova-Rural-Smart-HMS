"use client";

/**
 * M6 expenses page: record operational expenditure (frozen amounts on the
 * server), void mistakes with a reason (the ONLY legal change — records are
 * permanent), and a per-category period summary.
 */
import { CSSProperties, useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { ApiError, Branch, Expense, PageOf, api } from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

const CATEGORIES = [
  "rent", "salaries", "utilities", "supplies", "equipment",
  "maintenance", "service", "transport", "marketing", "other",
];

const rowBtn: CSSProperties = {
  border: "1px solid var(--border)",
  background: "var(--white)",
  color: "var(--danger)",
  borderRadius: "var(--radius)",
  padding: "0.35rem 0.7rem",
  minHeight: "40px",
  fontWeight: 600,
  cursor: "pointer",
};

type SummaryRow = { category: string; count: number; total: number };

export default function ExpensesPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [rows, setRows] = useState<Expense[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [summary, setSummary] = useState<SummaryRow[]>([]);

  const [branchId, setBranchId] = useState("");
  const [category, setCategory] = useState("supplies");
  const [amount, setAmount] = useState("");
  const [expenseDate, setExpenseDate] = useState(
    new Date().toISOString().slice(0, 10)
  );
  const [description, setDescription] = useState("");
  const [reference, setReference] = useState("");
  const [voidReasons, setVoidReasons] = useState<Record<string, string>>({});

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 864e5)
    .toISOString()
    .slice(0, 10);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [expensePage, branchList, sumPage] = await Promise.all([
        api.get<PageOf<Expense>>("/expenses?limit=100"),
        api.get<Branch[]>("/branches"),
        api.get<PageOf<SummaryRow>>(
          `/expenses/summary?from=${monthAgo}&to=${today}`
        ),
      ]);
      setRows(expensePage.items);
      setBranches(branchList.filter((b) => b.status === "active"));
      setBranchId((prev) => prev || branchList[0]?.id || "");
      setSummary(sumPage.items);
    } catch {
      setError("Could not load expenses.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function fail(err: unknown) {
    setError(err instanceof ApiError ? String(err.message) : "Action failed");
  }

  async function recordExpense() {
    const value = Number(amount);
    if (!value || value <= 0 || !expenseDate) {
      setError("A positive amount and a date are required.");
      return;
    }
    setError(null);
    try {
      await api.post(
        "/expenses",
        {
          category,
          amount: value,
          expense_date: expenseDate,
          description,
          reference,
          branch_id: branchId || undefined,
        },
        `web-exp-${category}-${value}-${expenseDate}`
      );
      setAmount("");
      setDescription("");
      setReference("");
      setNotice("Expense recorded (immutable — void to correct).");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function voidExpense(id: string) {
    const reason = (voidReasons[id] || "").trim();
    if (reason.length < 3) {
      setError("A void reason (3+ chars) is required.");
      return;
    }
    setError(null);
    try {
      await api.post(`/expenses/${id}/void`, { reason });
      setVoidReasons((prev) => ({ ...prev, [id]: "" }));
      setNotice("Expense voided — it stays on the books for audit.");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  const money = (n: number) => `₹${Number(n).toLocaleString("en-IN")}`;

  return (
    <AppShell active="expenses">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("expenses", lang)}</h1>
        <p className="notice">{t("m6Notice", lang)}</p>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("addExpense", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="ebr">{t("branch", lang)}</label>
              <select
                id="ebr"
                value={branchId}
                onChange={(e) => setBranchId(e.target.value)}
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ec">{t("category", lang)}</label>
              <select
                id="ec"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ea">{t("amount", lang)}</label>
              <input
                id="ea"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="ed">{t("expenseDate", lang)}</label>
              <input
                id="ed"
                type="date"
                value={expenseDate}
                onChange={(e) => setExpenseDate(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="eref">{t("reference", lang)}</label>
              <input
                id="eref"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
              />
            </div>
            <div className="field" style={{ minWidth: "220px" }}>
              <label htmlFor="edesc">{t("itemDescription", lang)}</label>
              <input
                id="edesc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            <button
              className="primary"
              type="button"
              style={{ alignSelf: "end" }}
              onClick={() => void recordExpense()}
            >
              {t("addExpense", lang)}
            </button>
          </div>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>
            {t("summary", lang)}{" "}
            <span className="muted" style={{ fontWeight: 400 }}>
              {t("period", lang)}: {monthAgo} → {today}
            </span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>{t("category", lang)}</th>
                <th>#</th>
                <th>{t("total", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {summary.map((s) => (
                <tr key={s.category}>
                  <td>{s.category}</td>
                  <td>{s.count}</td>
                  <td>{money(s.total)}</td>
                </tr>
              ))}
              {summary.length === 0 && (
                <tr>
                  <td colSpan={3} className="muted">
                    No expenses in this period.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("expenses", lang)}</h3>
          <table>
            <thead>
              <tr>
                <th>{t("expenseDate", lang)}</th>
                <th>{t("category", lang)}</th>
                <th>{t("amount", lang)}</th>
                <th>{t("itemDescription", lang)}</th>
                <th>{t("status", lang)}</th>
                <th>{t("actions", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id}>
                  <td>{e.expense_date}</td>
                  <td>{e.category}</td>
                  <td>{money(e.amount)}</td>
                  <td>
                    {e.description || "—"}
                    {e.reference && <span className="tag">{e.reference}</span>}
                  </td>
                  <td>
                    {e.status}
                    {e.void_reason && (
                      <span className="tag">{e.void_reason}</span>
                    )}
                  </td>
                  <td>
                    {e.status === "recorded" ? (
                      <span style={{ display: "inline-flex", gap: "0.4rem" }}>
                        <input
                          aria-label={`${t("reason", lang)} (${e.reference ||
                            e.category})`}
                          style={{ width: "150px", minHeight: "40px" }}
                          placeholder={t("reason", lang)}
                          value={voidReasons[e.id] ?? ""}
                          onChange={(ev) =>
                            setVoidReasons((prev) => ({
                              ...prev,
                              [e.id]: ev.target.value,
                            }))
                          }
                        />
                        <button
                          type="button"
                          style={rowBtn}
                          onClick={() => void voidExpense(e.id)}
                        >
                          {t("void", lang)}
                        </button>
                      </span>
                    ) : (
                      <span className="muted">{e.void_reason}</span>
                    )}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    No expenses yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        {notice && <p className="notice">{notice}</p>}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </main>
    </AppShell>
  );
}