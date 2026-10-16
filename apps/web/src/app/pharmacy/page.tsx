"use client";

/**
 * Pharmacy desk (M5): FEFO stock batches with expiry/low-stock alerts,
 * receiving against the validated drug catalog, and dispensing — either
 * against a signed prescription (items limited to what the Rx contains)
 * or OTC against a registered patient. The backend enforces FEFO
 * allocation, expiry/recall freezes and idempotent retries; illegal
 * operations surface the API's 409/422/403 verbatim.
 */
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  ApiError,
  Branch,
  Dispense,
  Drug,
  PageOf,
  Patient,
  Prescription,
  PrescriptionDetail,
  StockAlerts,
  StockBatch,
  StockMovement,
  api,
} from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

type RxCheck = { on: boolean; qty: string };

const parseQty = (raw: string): number => {
  const n = parseInt(raw.replace(/[^0-9]/g, ""), 10);
  return Number.isFinite(n) && n > 0 ? n : 1;
};

export default function PharmacyPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [alerts, setAlerts] = useState<StockAlerts | null>(null);
  const [batches, setBatches] = useState<StockBatch[]>([]);
  const [movements, setMovements] = useState<StockMovement[]>([]);
  const [drugs, setDrugs] = useState<Drug[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [signedRx, setSignedRx] = useState<Prescription[]>([]);

  // receive form
  const [rDrug, setRDrug] = useState("");
  const [rBranch, setRBranch] = useState("");
  const [rBatchNo, setRBatchNo] = useState("");
  const [rExpiry, setRExpiry] = useState("");
  const [rQty, setRQty] = useState("10");
  const [rCost, setRCost] = useState("0");
  const [rSupplier, setRSupplier] = useState("");

  // dispense form
  const [rxId, setRxId] = useState("");
  const [rxDetail, setRxDetail] = useState<PrescriptionDetail | null>(null);
  const [rxChecks, setRxChecks] = useState<Record<string, RxCheck>>({});
  const [otcPatient, setOtcPatient] = useState("");
  const [dDrug, setDDrug] = useState("");
  const [dQty, setDQty] = useState("1");

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [alertData, batchPage, movePage, drugList, branchList] =
        await Promise.all([
          api.get<StockAlerts>("/pharmacy/stock/alerts"),
          api.get<PageOf<StockBatch>>("/pharmacy/stock/batches?limit=100"),
          api.get<PageOf<StockMovement>>("/pharmacy/stock/movements?limit=30"),
          api.get<Drug[]>("/prescriptions/drugs?limit=200"),
          api.get<Branch[]>("/branches"),
        ]);
      setAlerts(alertData);
      setBatches(batchPage.items);
      setMovements(movePage.items);
      setDrugs(drugList);
      setBranches(branchList.filter((b) => b.status === "active"));
      setRBranch((prev) => prev || branchList[0]?.id || "");
      setRDrug((prev) => prev || drugList[0]?.id || "");
      setDDrug((prev) => prev || drugList[0]?.id || "");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? String(err.message)
          : "Could not load the pharmacy."
      );
    }
  }, []);

  // Roles without patient/Rx read still get the rest of the page.
  useEffect(() => {
    void load();
    api
      .get<PageOf<Patient>>("/patients?limit=200")
      .then((p) => {
        setPatients(p.items);
        setOtcPatient((prev) => prev || p.items[0]?.id || "");
      })
      .catch(() => setPatients([]));
    api
      .get<PageOf<Prescription>>("/prescriptions?rx_status=signed&limit=50")
      .then((p) => setSignedRx(p.items))
      .catch(() => setSignedRx([]));
  }, [load]);

  async function openRx(id: string) {
    setRxId(id);
    setRxDetail(null);
    setRxChecks({});
    if (!id) return;
    setError(null);
    try {
      const detail = await api.get<PrescriptionDetail>(`/prescriptions/${id}`);
      setRxDetail(detail);
      const checks: Record<string, RxCheck> = {};
      for (const item of detail.items) {
        checks[item.id] = { on: true, qty: item.quantity };
      }
      setRxChecks(checks);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Rx load failed");
    }
  }

  async function receiveStock() {
    if (!rDrug || !rBranch || !rBatchNo || !rExpiry) {
      setError("Fill in drug, branch, batch number and expiry.");
      return;
    }
    setError(null);
    try {
      const batch = await api.post<StockBatch>(
        "/pharmacy/stock/batches",
        {
          branch_id: rBranch,
          drug_id: rDrug,
          batch_number: rBatchNo,
          expiry_date: rExpiry,
          quantity: Number(rQty) || 0,
          unit_cost: Number(rCost) || 0,
          supplier: rSupplier,
        },
        `web-recv-${rBranch}-${rBatchNo}`
      );
      setNotice(`${t("receiveStock", lang)}: ${batch.batch_number}`);
      setRBatchNo("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Receive failed");
    }
  }

  async function dispense() {
    setError(null);
    try {
      let out: Dispense;
      if (rxDetail) {
        const items = rxDetail.items
          .filter((i) => rxChecks[i.id]?.on)
          .map((i) => ({
            drug_id: i.drug_id,
            quantity: parseQty(rxChecks[i.id]?.qty ?? i.quantity),
          }));
        if (items.length === 0) {
          setError("Tick at least one medicine.");
          return;
        }
        out = await api.post<Dispense>(
          "/pharmacy/dispenses",
          { prescription_id: rxDetail.id, items },
          `web-disp-${rxDetail.id}-${items.length}`
        );
      } else {
        if (!otcPatient || !dDrug) {
          setError("Pick a patient and a medicine.");
          return;
        }
        out = await api.post<Dispense>(
          "/pharmacy/dispenses",
          {
            patient_id: otcPatient,
            items: [{ drug_id: dDrug, quantity: parseQty(dQty) }],
          },
          `web-disp-${otcPatient}-${dDrug}-${Date.now()}`
        );
      }
      const lines = (out.items ?? [])
        .map((i) => {
          const drug = drugs.find((d) => d.id === i.drug_id);
          return `${drug?.generic_name ?? i.drug_id.slice(0, 8)} × ${i.quantity}`;
        })
        .join(", ");
      setNotice(`${t("dispense", lang)} ✓ ${lines}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Dispense failed");
    }
  }

  const drugName = (id: string) =>
    drugs.find((d) => d.id === id)?.generic_name ?? id.slice(0, 8);

  const patientLabel = (id: string | null) => {
    if (!id) return "—";
    const p = patients.find((x) => x.id === id);
    return p ? `${p.uhid} — ${p.full_name}` : id.slice(0, 8);
  };

  const rxLabel = (rx: Prescription) =>
    `${patientLabel(rx.patient_id)} · ${new Date(rx.created_at).toLocaleDateString()}`;

  const alertBatchRow = (b: StockBatch) => (
    <li key={b.id}>
      <strong>{b.generic_name || drugName(b.drug_id)}</strong> — {b.batch_number}
      <span className="muted"> · {b.quantity_on_hand}</span>
    </li>
  );

  return (
    <AppShell active="pharmacy">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("pharmacy", lang)}</h1>
        <p className="muted">{t("m5Notice", lang)}</p>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="notice">{notice}</p>}

        {alerts && (
          <section className="card">
            <h3 style={{ marginTop: 0 }}>{t("alerts", lang)}</h3>
            <div className="grid">
              <div>
                <p className="label">{t("expiringSoon", lang)}</p>
                <ul>
                  {alerts.expiring_soon.map(alertBatchRow)}
                  {alerts.expiring_soon.length === 0 && (
                    <li className="muted">—</li>
                  )}
                </ul>
              </div>
              <div>
                <p className="label">{t("expired", lang)}</p>
                <ul>
                  {alerts.expired.map(alertBatchRow)}
                  {alerts.expired.length === 0 && <li className="muted">—</li>}
                </ul>
              </div>
              <div>
                <p className="label">{t("lowStock", lang)}</p>
                <ul>
                  {alerts.low_stock.map((e) => (
                    <li key={e.drug_id}>
                      <strong>{e.generic_name || drugName(e.drug_id)}</strong>
                      <span className="muted"> · {e.on_hand}</span>
                    </li>
                  ))}
                  {alerts.low_stock.length === 0 && (
                    <li className="muted">—</li>
                  )}
                </ul>
              </div>
            </div>
          </section>
        )}

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("receiveStock", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field">
              <label htmlFor="rc_drug">{t("drug", lang)}</label>
              <select
                id="rc_drug"
                value={rDrug}
                onChange={(e) => setRDrug(e.target.value)}
              >
                {drugs.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.generic_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="rc_branch">{t("branch", lang)}</label>
              <select
                id="rc_branch"
                value={rBranch}
                onChange={(e) => setRBranch(e.target.value)}
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="rc_batch">{t("batchNo", lang)}</label>
              <input
                id="rc_batch"
                value={rBatchNo}
                onChange={(e) => setRBatchNo(e.target.value)}
                placeholder="LOT-1234"
              />
            </div>
            <div className="field">
              <label htmlFor="rc_exp">{t("expiry", lang)}</label>
              <input
                id="rc_exp"
                type="date"
                value={rExpiry}
                onChange={(e) => setRExpiry(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="rc_qty">{t("qty", lang)}</label>
              <input
                id="rc_qty"
                type="number"
                min={1}
                value={rQty}
                onChange={(e) => setRQty(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="rc_cost">{t("unitCost", lang)}</label>
              <input
                id="rc_cost"
                type="number"
                min={0}
                step="0.01"
                value={rCost}
                onChange={(e) => setRCost(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="rc_sup">{t("supplier", lang)}</label>
              <input
                id="rc_sup"
                value={rSupplier}
                onChange={(e) => setRSupplier(e.target.value)}
              />
            </div>
            <button
              className="primary"
              type="button"
              style={{ alignSelf: "end" }}
              onClick={() => void receiveStock()}
            >
              {t("receiveStock", lang)}
            </button>
          </div>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("dispenseMed", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field" style={{ minWidth: "18rem" }}>
              <label htmlFor="dp_rx">{t("signedRx", lang)}</label>
              <select
                id="dp_rx"
                value={rxId}
                onChange={(e) => void openRx(e.target.value)}
              >
                <option value="">— OTC —</option>
                {signedRx.map((rx) => (
                  <option key={rx.id} value={rx.id}>
                    {rxLabel(rx)}
                  </option>
                ))}
              </select>
            </div>
            {!rxDetail && (
              <>
                <div className="field">
                  <label htmlFor="dp_pat">{t("patients", lang)}</label>
                  <select
                    id="dp_pat"
                    value={otcPatient}
                    onChange={(e) => setOtcPatient(e.target.value)}
                  >
                    {patients.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.uhid} — {p.full_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="dp_drug">{t("drug", lang)}</label>
                  <select
                    id="dp_drug"
                    value={dDrug}
                    onChange={(e) => setDDrug(e.target.value)}
                  >
                    {drugs.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.generic_name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="dp_qty">{t("qty", lang)}</label>
                  <input
                    id="dp_qty"
                    type="number"
                    min={1}
                    value={dQty}
                    onChange={(e) => setDQty(e.target.value)}
                  />
                </div>
              </>
            )}
          </div>

          {rxDetail && (
            <table style={{ marginTop: "0.75rem" }}>
              <thead>
                <tr>
                  <th />
                  <th>{t("drug", lang)}</th>
                  <th>{t("dose", lang)}</th>
                  <th>{t("frequency", lang)}</th>
                  <th>{t("duration", lang)}</th>
                  <th>{t("qty", lang)}</th>
                </tr>
              </thead>
              <tbody>
                {rxDetail.items.map((item) => {
                  const check = rxChecks[item.id] ?? { on: false, qty: "1" };
                  return (
                    <tr key={item.id}>
                      <td>
                        <input
                          type="checkbox"
                          checked={check.on}
                          onChange={(e) =>
                            setRxChecks((prev) => ({
                              ...prev,
                              [item.id]: {
                                ...check,
                                on: e.target.checked,
                              },
                            }))
                          }
                        />
                      </td>
                      <td>{drugName(item.drug_id)}</td>
                      <td>{item.dose}</td>
                      <td>{item.frequency}</td>
                      <td>{item.duration_days}</td>
                      <td>
                        <input
                          style={{ width: "5rem" }}
                          value={check.qty}
                          onChange={(e) =>
                            setRxChecks((prev) => ({
                              ...prev,
                              [item.id]: { ...check, qty: e.target.value },
                            }))
                          }
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          <button
            className="primary"
            type="button"
            style={{ marginTop: "0.75rem" }}
            onClick={() => void dispense()}
          >
            {t("dispense", lang)}
          </button>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("stockBatches", lang)}</h3>
          <table>
            <thead>
              <tr>
                <th>{t("drug", lang)}</th>
                <th>{t("batchNo", lang)}</th>
                <th>{t("expiry", lang)}</th>
                <th>{t("onHand", lang)}</th>
                <th>{t("supplier", lang)}</th>
                <th>{t("status", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((b) => (
                <tr key={b.id}>
                  <td>{b.generic_name || drugName(b.drug_id)}</td>
                  <td>{b.batch_number}</td>
                  <td>{b.expiry_date}</td>
                  <td>
                    {b.quantity_on_hand}
                    {b.quantity_on_hand < 10 && (
                      <span className="tag">{t("lowStock", lang)}</span>
                    )}
                  </td>
                  <td>{b.supplier || "—"}</td>
                  <td>{b.status}</td>
                </tr>
              ))}
              {batches.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    —
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("stockMovements", lang)}</h3>
          <table>
            <thead>
              <tr>
                <th>{t("status", lang)}</th>
                <th>{t("drug", lang)}</th>
                <th>{t("qty", lang)}</th>
                <th>{t("reason", lang)}</th>
                <th>{t("time", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {movements.map((m) => {
                const batch = batches.find((b) => b.id === m.batch_id);
                return (
                  <tr key={m.id}>
                    <td>
                      {m.movement_type}
                      <span className="tag">{m.direction}</span>
                    </td>
                    <td>
                      {batch
                        ? batch.generic_name || drugName(batch.drug_id)
                        : m.batch_id.slice(0, 8)}
                    </td>
                    <td>{m.quantity}</td>
                    <td>{m.reason}</td>
                    <td>{new Date(m.created_at).toLocaleString()}</td>
                  </tr>
                );
              })}
              {movements.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    —
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>
      </main>
    </AppShell>
  );
}
