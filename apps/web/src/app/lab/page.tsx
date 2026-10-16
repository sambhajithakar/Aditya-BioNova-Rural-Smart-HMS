"use client";

/**
 * Laboratory worklist (M5): place orders against the validated test
 * catalog, collect specimens (unique barcode), import results, then walk
 * the imported → verified → released state machine. Four-eyes verification
 * (403 from the API) and supersession-only amendments are surfaced verbatim;
 * the backend's DB triggers refuse anything illegal.
 */
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  ApiError,
  Branch,
  LabOrder,
  LabOrderDetail,
  LabResult,
  LabTest,
  PageOf,
  Patient,
  api,
} from "@/lib/api";
import { getLang, t } from "@/lib/i18n";

type Lang = "en" | "mr" | "hi";

const ORDER_STATUS_KEY: Record<string, string> = {
  ordered: "ordered",
  specimen_collected: "specimenCollected",
  in_progress: "inProgress",
  released: "released",
  cancelled: "cancelled",
};

const RESULT_STATUS_KEY: Record<string, string> = {
  imported: "imported",
  verified: "verified",
  released: "released",
  superseded: "superseded",
};

const FLAGS = ["normal", "low", "high", "abnormal", "critical"];
const SPECIMENS = ["blood", "urine", "stool", "swab", "sputum", "tissue", "other"];

export default function LabPage() {
  const [lang, setLang] = useState<Lang>("en");
  const [orders, setOrders] = useState<LabOrder[]>([]);
  const [tests, setTests] = useState<LabTest[]>([]);
  const [patients, setPatients] = useState<Patient[]>([]);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [selected, setSelected] = useState<LabOrderDetail | null>(null);

  // new order form
  const [nPatient, setNPatient] = useState("");
  const [nBranch, setNBranch] = useState("");
  const [nPriority, setNPriority] = useState("routine");
  const [nNotes, setNNotes] = useState("");
  const [nPicked, setNPicked] = useState<Record<string, boolean>>({});

  // order detail actions
  const [specimenType, setSpecimenType] = useState("blood");
  const [reason, setReason] = useState("");

  // import form
  const [iTest, setITest] = useState("");
  const [iValue, setIValue] = useState("");
  const [iNumeric, setINumeric] = useState("");
  const [iFlag, setIFlag] = useState("normal");

  // amend form
  const [amendId, setAmendId] = useState<string | null>(null);
  const [amendValue, setAmendValue] = useState("");
  const [amendReason, setAmendReason] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => setLang(getLang()), []);

  const load = useCallback(async () => {
    try {
      const [orderPage, testList, branchList] = await Promise.all([
        api.get<PageOf<LabOrder>>("/lab/orders?limit=100"),
        api.get<LabTest[]>("/lab/tests"),
        api.get<Branch[]>("/branches"),
      ]);
      setOrders(orderPage.items);
      setTests(testList);
      setBranches(branchList.filter((b) => b.status === "active"));
      setNBranch((prev) => prev || branchList[0]?.id || "");
    } catch (err) {
      setError(
        err instanceof ApiError ? String(err.message) : "Could not load the lab."
      );
    }
  }, []);

  useEffect(() => {
    void load();
    api
      .get<PageOf<Patient>>("/patients?limit=200")
      .then((p) => {
        setPatients(p.items);
        setNPatient((prev) => prev || p.items[0]?.id || "");
      })
      .catch(() => setPatients([]));
  }, [load]);

  const openOrder = useCallback(async (id: string) => {
    setError(null);
    try {
      const detail = await api.get<LabOrderDetail>(`/lab/orders/${id}`);
      setSelected(detail);
      const live = new Set(
        detail.results
          .filter((r) => r.status !== "superseded")
          .map((r) => r.test_id)
      );
      setITest((prev) => (prev && !live.has(prev) ? prev : ""));
      setAmendId(null);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Load failed");
    }
  }, []);

  async function refresh(detail = true) {
    await load();
    if (detail && selected) await openOrder(selected.id);
  }

  async function createOrder() {
    const testIds = Object.entries(nPicked)
      .filter(([, on]) => on)
      .map(([id]) => id);
    if (!nPatient || !nBranch || testIds.length === 0) {
      setError("Pick a patient, a branch and at least one test.");
      return;
    }
    setError(null);
    try {
      const order = await api.post<LabOrderDetail>("/lab/orders", {
        patient_id: nPatient,
        branch_id: nBranch,
        test_ids: testIds,
        priority: nPriority,
        clinical_notes: nNotes,
      });
      setNPicked({});
      setNNotes("");
      setNotice(`${t("labOrders", lang)} ✓ ${order.status}`);
      await refresh(false);
      await openOrder(order.id);
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Order failed");
    }
  }

  async function act(path: string, body: unknown = {}, keepNotice?: string) {
    if (!selected) return;
    setError(null);
    try {
      await api.post(`/lab${path}`, body);
      if (keepNotice) setNotice(keepNotice);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Action failed");
    }
  }

  async function importResults() {
    if (!selected) return;
    if (!iTest) {
      setError("Pick a test to import.");
      return;
    }
    setError(null);
    try {
      await api.post(`/lab/orders/${selected.id}/results`, {
        results: [
          {
            test_id: iTest,
            value_text: iValue,
            value_numeric: iNumeric === "" ? null : Number(iNumeric),
            flag: iFlag,
          },
        ],
      });
      setIValue("");
      setINumeric("");
      setNotice(t("importResults", lang));
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Import failed");
    }
  }

  async function saveAmend(resultId: string) {
    setError(null);
    try {
      await api.post(`/lab/results/${resultId}/amend`, {
        value_text: amendValue,
        value_numeric: amendValue === "" ? null : Number(amendValue),
        flag: iFlag,
        reason: amendReason,
      });
      setAmendId(null);
      setAmendValue("");
      setAmendReason("");
      setNotice(t("amend", lang));
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? String(err.message) : "Amend failed");
    }
  }

  const testLabel = (id: string) => {
    const test = tests.find((x) => x.id === id);
    return test ? `${test.code} — ${test.name}` : id.slice(0, 8);
  };

  const patientLabel = (id: string) => {
    const p = patients.find((x) => x.id === id);
    return p ? `${p.uhid} — ${p.full_name}` : id.slice(0, 8);
  };

  const statusLabel = (key: Record<string, string>, status: string) =>
    t(key[status] ?? status, lang);

  const liveTestIds = new Set(
    (selected?.results ?? [])
      .filter((r) => r.status !== "superseded")
      .map((r) => r.test_id)
  );
  const importableTests = tests.filter((x) => !liveTestIds.has(x.id));

  const resultValue = (r: LabResult) =>
    r.value_numeric !== null
      ? `${r.value_numeric}${r.unit ? ` ${r.unit}` : ""}`
      : r.value_text || "—";

  return (
    <AppShell active="lab">
      <main className="page">
        <h1 style={{ marginTop: 0 }}>{t("lab", lang)}</h1>
        <p className="muted">{t("m5Notice", lang)}</p>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {notice && <p className="notice">{notice}</p>}

        <section className="card">
          <h3 style={{ marginTop: 0 }}>{t("newLabOrder", lang)}</h3>
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <div className="field" style={{ minWidth: "16rem" }}>
              <label htmlFor="lo_pat">{t("patients", lang)}</label>
              <select
                id="lo_pat"
                value={nPatient}
                onChange={(e) => setNPatient(e.target.value)}
              >
                {patients.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.uhid} — {p.full_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="lo_branch">{t("branch", lang)}</label>
              <select
                id="lo_branch"
                value={nBranch}
                onChange={(e) => setNBranch(e.target.value)}
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="lo_pri">{t("priority", lang)}</label>
              <select
                id="lo_pri"
                value={nPriority}
                onChange={(e) => setNPriority(e.target.value)}
              >
                <option value="routine">{t("routine", lang)}</option>
                <option value="urgent">{t("urgent", lang)}</option>
              </select>
            </div>
            <div className="field" style={{ minWidth: "16rem" }}>
              <label htmlFor="lo_notes">{t("clinicalNotes", lang)}</label>
              <input
                id="lo_notes"
                value={nNotes}
                onChange={(e) => setNNotes(e.target.value)}
              />
            </div>
          </div>
          <p className="label" style={{ marginTop: "0.75rem" }}>
            {t("tests", lang)}
          </p>
          <div className="grid">
            {tests.map((test) => (
              <label key={test.id} style={{ display: "block" }}>
                <input
                  type="checkbox"
                  checked={!!nPicked[test.id]}
                  onChange={(e) =>
                    setNPicked((prev) => ({
                      ...prev,
                      [test.id]: e.target.checked,
                    }))
                  }
                />{" "}
                {test.code} <span className="muted">{test.name}</span>
              </label>
            ))}
          </div>
          <button
            className="primary"
            type="button"
            style={{ marginTop: "0.75rem" }}
            onClick={() => void createOrder()}
          >
            {t("newLabOrder", lang)}
          </button>
        </section>

        <section className="card" style={{ marginTop: "1.25rem" }}>
          <h3 style={{ marginTop: 0 }}>{t("labOrders", lang)}</h3>
          <table>
            <thead>
              <tr>
                <th>{t("patients", lang)}</th>
                <th>{t("priority", lang)}</th>
                <th>{t("status", lang)}</th>
                <th>{t("startsAt", lang)}</th>
                <th>{t("actions", lang)}</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id}>
                  <td>{patientLabel(o.patient_id)}</td>
                  <td>
                    {o.priority === "urgent" && (
                      <span className="tag">{t("urgent", lang)}</span>
                    )}
                    {o.priority}
                  </td>
                  <td>{statusLabel(ORDER_STATUS_KEY, o.status)}</td>
                  <td>{new Date(o.created_at).toLocaleString()}</td>
                  <td>
                    <button
                      className="ghost"
                      type="button"
                      onClick={() => void openOrder(o.id)}
                    >
                      {t("open", lang)}
                    </button>
                  </td>
                </tr>
              ))}
              {orders.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    —
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </section>

        {selected && (
          <section className="card" style={{ marginTop: "1.25rem" }}>
            <h3 style={{ marginTop: 0 }}>
              {patientLabel(selected.patient_id)}{" "}
              <span className="tag">
                {statusLabel(ORDER_STATUS_KEY, selected.status)}
              </span>
            </h3>
            {selected.clinical_notes && (
              <p className="muted">{selected.clinical_notes}</p>
            )}
            {selected.cancel_reason && (
              <p className="error">
                {t("cancelled", lang)}: {selected.cancel_reason}
              </p>
            )}

            <p className="label">{t("specimen", lang)}</p>
            <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
              <div className="field">
                <label htmlFor="sp_type">{t("specimen", lang)}</label>
                <select
                  id="sp_type"
                  value={specimenType}
                  onChange={(e) => setSpecimenType(e.target.value)}
                >
                  {SPECIMENS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <button
                className="primary"
                type="button"
                style={{ alignSelf: "end" }}
                disabled={selected.status !== "ordered"}
                onClick={() =>
                  void act(
                    `/orders/${selected.id}/specimens`,
                    { specimen_type: specimenType },
                    t("collectSpecimen", lang)
                  )
                }
              >
                {t("collectSpecimen", lang)}
              </button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>{t("barcode", lang)}</th>
                  <th>{t("specimen", lang)}</th>
                  <th>{t("status", lang)}</th>
                  <th>{t("reason", lang)}</th>
                  <th>{t("actions", lang)}</th>
                </tr>
              </thead>
              <tbody>
                {selected.specimens.map((s) => (
                  <tr key={s.id}>
                    <td>
                      <strong>{s.barcode}</strong>
                    </td>
                    <td>{s.specimen_type}</td>
                    <td>{s.status}</td>
                    <td>{s.rejection_reason || "—"}</td>
                    <td>
                      {s.status === "collected" && (
                        <button
                          className="ghost"
                          type="button"
                          onClick={() =>
                            void act(
                              `/specimens/${s.id}/reject`,
                              { reason: reason || "Rejected at reception" },
                              t("rejectSpecimen", lang)
                            )
                          }
                        >
                          {t("rejectSpecimen", lang)}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {selected.specimens.length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted">
                      —
                    </td>
                  </tr>
                )}
              </tbody>
            </table>

            <p className="label" style={{ marginTop: "1rem" }}>
              {t("results", lang)}
            </p>
            <table>
              <thead>
                <tr>
                  <th>{t("tests", lang)}</th>
                  <th>{t("value", lang)}</th>
                  <th>{t("reference", lang)}</th>
                  <th>{t("flag", lang)}</th>
                  <th>{t("status", lang)}</th>
                  <th>{t("actions", lang)}</th>
                </tr>
              </thead>
              <tbody>
                {selected.results.map((r) => (
                  <tr key={r.id}>
                    <td>{testLabel(r.test_id)}</td>
                    <td>{resultValue(r)}</td>
                    <td className="muted">{r.reference_range || "—"}</td>
                    <td>{r.flag}</td>
                    <td>
                      {statusLabel(RESULT_STATUS_KEY, r.status)}
                      {r.amendment_reason && (
                        <span className="muted"> · {r.amendment_reason}</span>
                      )}
                    </td>
                    <td>
                      {r.status === "imported" && (
                        <button
                          className="ghost"
                          type="button"
                          onClick={() =>
                            void act(`/results/${r.id}/verify`, {}, t("verify", lang))
                          }
                        >
                          {t("verify", lang)}
                        </button>
                      )}
                      {r.status === "verified" && (
                        <button
                          className="ghost"
                          type="button"
                          onClick={() =>
                            void act(
                              `/results/${r.id}/release`,
                              {},
                              t("release", lang)
                            )
                          }
                        >
                          {t("release", lang)}
                        </button>
                      )}
                      {r.status === "released" && (
                        <button
                          className="ghost"
                          type="button"
                          onClick={() => {
                            setAmendId(r.id);
                            setAmendValue(
                              r.value_numeric !== null
                                ? String(r.value_numeric)
                                : r.value_text
                            );
                            setAmendReason("");
                          }}
                        >
                          {t("amend", lang)}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {selected.results.length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      —
                    </td>
                  </tr>
                )}
              </tbody>
            </table>

            {amendId && (
              <div
                className="card"
                style={{ marginTop: "0.75rem", background: "transparent" }}
              >
                <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
                  <div className="field">
                    <label htmlFor="am_val">{t("value", lang)}</label>
                    <input
                      id="am_val"
                      value={amendValue}
                      onChange={(e) => setAmendValue(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="am_flag">{t("flag", lang)}</label>
                    <select
                      id="am_flag"
                      value={iFlag}
                      onChange={(e) => setIFlag(e.target.value)}
                    >
                      {FLAGS.map((f) => (
                        <option key={f} value={f}>
                          {f}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field" style={{ minWidth: "16rem" }}>
                    <label htmlFor="am_reason">{t("amendmentReason", lang)}</label>
                    <input
                      id="am_reason"
                      value={amendReason}
                      onChange={(e) => setAmendReason(e.target.value)}
                    />
                  </div>
                  <button
                    className="primary"
                    type="button"
                    style={{ alignSelf: "end" }}
                    onClick={() => void saveAmend(amendId)}
                  >
                    {t("save", lang)}
                  </button>
                  <button
                    className="ghost"
                    type="button"
                    style={{ alignSelf: "end" }}
                    onClick={() => setAmendId(null)}
                  >
                    {t("cancel", lang)}
                  </button>
                </div>
              </div>
            )}

            {importableTests.length > 0 &&
              ["specimen_collected", "in_progress"].includes(selected.status) && (
                <div
                  style={{
                    display: "flex",
                    gap: "0.75rem",
                    flexWrap: "wrap",
                    marginTop: "1rem",
                    alignItems: "end",
                  }}
                >
                  <div className="field" style={{ minWidth: "16rem" }}>
                    <label htmlFor="im_test">{t("tests", lang)}</label>
                    <select
                      id="im_test"
                      value={iTest}
                      onChange={(e) => setITest(e.target.value)}
                    >
                      <option value="">—</option>
                      {importableTests.map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.code} — {x.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="im_val">{t("value", lang)}</label>
                    <input
                      id="im_val"
                      value={iValue}
                      onChange={(e) => setIValue(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="im_num">{t("value", lang)} (num)</label>
                    <input
                      id="im_num"
                      type="number"
                      step="0.001"
                      value={iNumeric}
                      onChange={(e) => setINumeric(e.target.value)}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="im_flag">{t("flag", lang)}</label>
                    <select
                      id="im_flag"
                      value={iFlag}
                      onChange={(e) => setIFlag(e.target.value)}
                    >
                      {FLAGS.map((f) => (
                        <option key={f} value={f}>
                          {f}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    className="primary"
                    type="button"
                    onClick={() => void importResults()}
                  >
                    {t("importResults", lang)}
                  </button>
                </div>
              )}

            <div
              style={{
                display: "flex",
                gap: "0.75rem",
                flexWrap: "wrap",
                marginTop: "1rem",
                alignItems: "end",
              }}
            >
              <div className="field" style={{ minWidth: "16rem" }}>
                <label htmlFor="cx_reason">{t("reason", lang)}</label>
                <input
                  id="cx_reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </div>
              <button
                className="ghost"
                type="button"
                disabled={["released", "cancelled", "in_progress"].includes(
                  selected.status
                )}
                onClick={() =>
                  void act(
                    `/orders/${selected.id}/cancel`,
                    { reason: reason || "Cancelled at reception" },
                    t("cancelOrder", lang)
                  )
                }
              >
                {t("cancelOrder", lang)}
              </button>
            </div>
          </section>
        )}
      </main>
    </AppShell>
  );
}
