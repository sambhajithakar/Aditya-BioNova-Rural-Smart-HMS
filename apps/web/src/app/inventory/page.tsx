"use client";

/**
 * M6 inventory page: general supply items per branch with receive / issue /
 * adjust movements (append-only ledger on the server), low-stock alerts
 * against the reorder level, and the recent movement trail.
 */
import { CSSProperties, useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  ApiError,
  Branch,
  InventoryItem,
  InventoryMovement,
  PageOf,
  api,
} from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

const rowBtn: CSSProperties = {
  border: "1px solid var(--border)",
  background: "var(--white)",
  color: "var(--navy-800)",
  borderRadius: "var(--radius)",
  padding: "0.35rem 0.7rem",
  minHeight: "40px",
  fontWeight: 600,
  cursor: "pointer",
};

export default function InventoryPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [items, setItems] = useState<InventoryItem[]>([]);
  const [movements, setMovements] = useState<InventoryMovement[]>([]);
  const [alerts, setAlerts] = useState<InventoryItem[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);

  const [branchId, setBranchId] = useState("");
  const [name, setName] = useState("");
  const [sku, setSku] = useState("");
  const [unit, setUnit] = useState("pcs");
  const [reorder, setReorder] = useState("0");

  const [active, setActive] = useState<string | null>(null);
  const [moveType, setMoveType] = useState("receive");
  const [quantity, setQuantity] = useState("1");
  const [moveReason, setMoveReason] = useState("");
  const [writeOff, setWriteOff] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [itemPage, movePage, alertPage, branchList] = await Promise.all([
        api.get<PageOf<InventoryItem>>("/inventory/items?limit=100"),
        api.get<PageOf<InventoryMovement>>("/inventory/movements?limit=30"),
        api.get<PageOf<InventoryItem>>("/inventory/alerts?limit=30"),
        api.get<Branch[]>("/branches"),
      ]);
      setItems(itemPage.items);
      setMovements(movePage.items);
      setAlerts(alertPage.items);
      setBranches(branchList.filter((b) => b.status === "active"));
      setBranchId((prev) => prev || branchList[0]?.id || "");
    } catch {
      setError("Could not load inventory.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function fail(err: unknown) {
    setError(err instanceof ApiError ? String(err.message) : "Action failed");
  }

  async function addItem() {
    if (!name.trim() || !branchId) {
      setError("Item name and branch are required.");
      return;
    }
    setError(null);
    try {
      await api.post("/inventory/items", {
        branch_id: branchId,
        name: name.trim(),
        sku,
        unit,
        reorder_level: Number(reorder || 0),
      });
      setName("");
      setSku("");
      setNotice("Item created — receive stock to see on-hand levels.");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  async function postMovement(itemId: string) {
    const qty = Number(quantity);
    if (!qty || moveReason.trim().length < 3) {
      setError("Quantity and a reason (3+ chars) are required.");
      return;
    }
    setError(null);
    try {
      await api.post(
        `/inventory/items/${itemId}/movements`,
        {
          movement_type: moveType,
          quantity: moveType === "issue" ? Math.abs(qty) : qty,
          reason: moveReason,
          write_off: writeOff,
        },
        `web-inv-${itemId}-${moveType}-${qty}-${Date.now()}`
      );
      setMoveReason("");
      setWriteOff(false);
      setNotice("Movement recorded on the ledger.");
      await load();
    } catch (err) {
      fail(err);
    }
  }

  const money = (n: number) => `₹${Number(n).toLocaleString("en-IN")}`;

  return (
    <AppShell active="inventory">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("inventory", lang)}</h1>
        <p className="notice">{t("m6Notice", lang)}</p>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("newItem", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="ibr">{t("branch", lang)}</label>
              <select
                id="ibr"
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
              <label htmlFor="in">{t("itemName", lang)}</label>
              <input
                id="in"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="isk">SKU</label>
              <input
                id="isk"
                value={sku}
                onChange={(e) => setSku(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="iu">{t("unit", lang)}</label>
              <input
                id="iu"
                value={unit}
                onChange={(e) => setUnit(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="irl">{t("reorderLevel", lang)}</label>
              <input
                id="irl"
                value={reorder}
                onChange={(e) => setReorder(e.target.value)}
              />
            </div>
            <button
              className="primary"
              type="button"
              style={{ alignSelf: "end" }}
              onClick={() => void addItem()}
            >
              {t("newItem", lang)}
            </button>
          </div>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>
            {t("alerts", lang)}{" "}
            <span className="tag">
              {alerts.length} {t("lowStock", lang)}
            </span>
          </h3>
          {alerts.length === 0 && <p className="muted">All items healthy.</p>}
          {alerts.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>{t("itemName", lang)}</th>
                  <th>{t("onHand", lang)}</th>
                  <th>{t("reorderLevel", lang)}</th>
                  <th>{t("unit", lang)}</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}</td>
                    <td>
                      <strong>{a.quantity_on_hand}</strong>
                    </td>
                    <td>{a.reorder_level}</td>
                    <td>{a.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("inventory", lang)}</h3>
          <table>
            <thead>
              <tr>
                <th>{t("itemName", lang)}</th>
                <th>{t("onHand", lang)}</th>
                <th>{t("reorderLevel", lang)}</th>
                <th>{t("unitCost", lang)}</th>
                <th>{t("status", lang)}</th>
                <th>{t("movementType", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td>
                    {it.name}
                    {it.low_stock && <span className="tag"> {t("lowStock", lang)}</span>}
                    {it.status === "retired" && <span className="tag"> retired</span>}
                  </td>
                  <td>{it.quantity_on_hand}</td>
                  <td>{it.reorder_level}</td>
                  <td>{money(it.unit_cost)}</td>
                  <td>{it.status}</td>
                  <td>
                    {active === it.id ? (
                      <span
                        style={{
                          display: "inline-flex",
                          gap: "0.4rem",
                          alignItems: "end",
                          flexWrap: "wrap",
                        }}
                      >
                        <span className="field">
                          {/* biome-ignore lint: small inline control */}
                          <select
                            aria-label={t("movementType", lang)}
                            value={moveType}
                            onChange={(e) => setMoveType(e.target.value)}
                          >
                            {["receive", "issue", "adjustment", "return"].map(
                              (v) => (
                                <option key={v} value={v}>
                                  {v === "receive"
                                    ? t("receive", lang)
                                    : v === "issue"
                                      ? t("issue", lang)
                                      : v === "adjustment"
                                        ? t("adjustment", lang)
                                        : v}
                                </option>
                              )
                            )}
                          </select>
                        </span>
                        <span className="field">
                          <input
                            aria-label={t("qty", lang)}
                            style={{ width: "70px" }}
                            value={quantity}
                            onChange={(e) => setQuantity(e.target.value)}
                          />
                        </span>
                        <span className="field">
                          {/* biome-ignore lint: small inline control */}
                          <input
                            aria-label={t("reason", lang)}
                            style={{ width: "160px" }}
                            value={moveReason}
                            onChange={(e) => setMoveReason(e.target.value)}
                          />
                        </span>
                        <button
                          type="button"
                          style={rowBtn}
                          disabled={it.status !== "active"}
                          onClick={() => void postMovement(it.id)}
                        >
                          {t("save", lang)}
                        </button>
                        <button
                          type="button"
                          style={rowBtn}
                          onClick={() => setActive(null)}
                        >
                          {t("cancel", lang)}
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        style={rowBtn}
                        disabled={it.status !== "active"}
                        onClick={() => {
                          setActive(it.id);
                          setQuantity("1");
                          setMoveType("receive");
                        }}
                      >
                        {t("movementType", lang)}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {items.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    No items yet — create one above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <label className="field" style={{ marginTop: "0.5rem" }}>
            <input
              type="checkbox"
              checked={writeOff}
              onChange={(e) => setWriteOff(e.target.checked)}
              style={{ minWidth: "auto" }}
            />
            <span style={{ fontWeight: 600 }}>
              {t("writeOff", lang)} (always stock OUT)
            </span>
          </label>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("movements", lang)}</h3>
          <table>
            <thead>
              <tr>
                <th>{t("time", lang)}</th>
                <th>{t("itemName", lang)}</th>
                <th>{t("movementType", lang)}</th>
                <th>±</th>
                <th>{t("reason", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {movements.map((m) => (
                <tr key={m.id}>
                  <td>{new Date(m.created_at).toLocaleString()}</td>
                  <td>{items.find((i) => i.id === m.item_id)?.name ?? m.item_id}</td>
                  <td>{m.movement_type}</td>
                  <td>
                    {m.direction === "in" ? "+" : "−"}
                    {m.quantity}
                  </td>
                  <td>{m.reason}</td>
                </tr>
              ))}
              {movements.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    The ledger is empty.
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