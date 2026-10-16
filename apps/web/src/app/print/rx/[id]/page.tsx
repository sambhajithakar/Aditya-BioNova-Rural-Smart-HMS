"use client";

/**
 * Printable A4/A5 prescription & receipt (M3).
 *
 * Trilingual by design: labels render in English, Marathi and Hindi
 * together (Indian clinical paperwork convention), while the clinician's
 * own text stays in the language it was written. Uses the browser's
 * print pipeline (Save as PDF) — no server-side PDF dependency.
 * `?type=receipt` switches the layout to the billing receipt.
 */
import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ApiError, Patient, PrescriptionDetail, Drug, Receipt, api } from "@/lib/api";

type Label = { en: string; mr: string; hi: string };

const L = {
  prescription: { en: "PRESCRIPTION", mr: "औषधपत्र", hi: "पर्चा" },
  receipt: { en: "RECEIPT", mr: "पावती", hi: "रसीद" },
  patient: { en: "Patient", mr: "रुग्ण", hi: "रोगी" },
  uhid: { en: "UHID", mr: "यूएचआयडी", hi: "यूएचआईडी" },
  date: { en: "Date", mr: "दिनांक", hi: "तिथि" },
  diagnosis: { en: "Diagnosis", mr: "निदान", hi: "निदान" },
  advice: { en: "Advice", mr: "सल्ला", hi: "सलाह" },
  followUp: { en: "Follow-up", mr: "पुनरावृत्ती", hi: "फ़ॉलो-अप" },
  medicines: { en: "Medicines", mr: "औषधे", hi: "दवाइयाँ" },
  dose: { en: "Dose", mr: "प्रमाण", hi: "खुराक" },
  frequency: { en: "Frequency", mr: "वारंवारता", hi: "आवृत्ति" },
  days: { en: "Days", mr: "दिवस", hi: "दिन" },
  signature: { en: "Signature & Seal", mr: "सही व मुद्रा", hi: "हस्ताक्षर व मुहर" },
  items: { en: "Description", mr: "वर्णन", hi: "विवरण" },
  qty: { en: "Qty", mr: "नग", hi: "मात्रा" },
  rate: { en: "Rate", mr: "दर", hi: "दर" },
  amount: { en: "Amount", mr: "रक्कम", hi: "राशि" },
  total: { en: "Total", mr: "एकूण", hi: "कुल" },
  paid: { en: "Paid", mr: "भरले", hi: "भुगतान" },
  balance: { en: "Balance due", mr: "थकबाकी", hi: "शेष" },
  payment: { en: "Payment", mr: "पेमेंट", hi: "भुगतान" },
  allergies: { en: "Allergies", mr: "ऍलर्जी", hi: "एलर्जी" },
  version: { en: "Version", mr: "आवृत्ती", hi: "संस्करण" },
  amendments: { en: "Amendments are versioned; this copy is immutable.", mr: "दुरुस्त्या आवृत्तीनुसार; ही प्रत अपरिवर्तनीय आहे.", hi: "संशोधन संस्करण में हैं; यह प्रति अपरिवर्तनीय है।" },
};

function tri(l: Label) {
  return (
    <>
      {l.en} <span className="tri">/ {l.mr} / {l.hi}</span>
    </>
  );
}

export default function PrintPage() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const type = search.get("type") === "receipt" ? "receipt" : "rx";

  const [rx, setRx] = useState<PrescriptionDetail | null>(null);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [drugs, setDrugs] = useState<Record<string, Drug>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        if (type === "receipt") {
          const r = await api.get<Receipt>(`/billing/invoices/${params.id}/receipt`);
          setReceipt(r);
        } else {
          const detail = await api.get<PrescriptionDetail>(
            `/prescriptions/${params.id}`
          );
          setRx(detail);
          const p = await api.get<Patient>(`/patients/${detail.patient_id}`);
          setPatient(p);
          const catalog = await api.get<Drug[]>("/prescriptions/drugs?limit=200");
          setDrugs(Object.fromEntries(catalog.map((d) => [d.id, d])));
        }
      } catch (err) {
        setError(err instanceof ApiError ? String(err.message) : "Load failed");
      }
    }
    void load();
  }, [params.id, type]);

  if (error) {
    return (
      <main className="print-sheet">
        <p className="error">{error}</p>
      </main>
    );
  }

  if (type === "receipt" && receipt) {
    const inv = receipt.invoice;
    return (
      <main className="print-sheet">
        <header className="print-head">
          <div>
            <h1>{receipt.org_name}</h1>
            <p className="tri">
              {receipt.branch_name} — {tri(L.receipt)}
            </p>
          </div>
          <div style={{ textAlign: "right" }}>
            <p>
              <strong>{inv.invoice_number}</strong>
            </p>
            <p>{new Date(inv.created_at).toLocaleDateString("en-IN")}</p>
          </div>
        </header>

        <section className="print-meta">
          <p>
            {tri(L.patient)}: <strong>{receipt.patient_name}</strong>
          </p>
          <p>
            {tri(L.uhid)}: <strong>{receipt.patient_uhid}</strong>
          </p>
        </section>

        <table className="print-table">
          <thead>
            <tr>
              <th>{tri(L.items)}</th>
              <th>{tri(L.qty)}</th>
              <th>{tri(L.rate)}</th>
              <th>{tri(L.amount)}</th>
            </tr>
          </thead>
          <tbody>
            {receipt.items.map((it) => (
              <tr key={it.id}>
                <td>
                  {it.description} <span className="tri">({it.item_type})</span>
                </td>
                <td>{it.quantity}</td>
                <td>{Number(it.unit_price).toFixed(2)}</td>
                <td>{Number(it.line_total).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <section className="print-totals">
          <p>
            {tri(L.total)}: <strong>{Number(inv.total).toFixed(2)}</strong>
          </p>
          <p>
            {tri(L.paid)}: <strong>{Number(inv.amount_paid).toFixed(2)}</strong>
          </p>
          <p>
            {tri(L.balance)}: <strong>{receipt.balance_due.toFixed(2)}</strong>
          </p>
        </section>

        {receipt.payments.length > 0 && (
          <section>
            <h3>{tri(L.payment)}</h3>
            <ul>
              {receipt.payments.map((p) => (
                <li key={p.id}>
                  {Number(p.amount).toFixed(2)} — {p.method} ({p.status})
                </li>
              ))}
            </ul>
          </section>
        )}

        <footer className="print-foot">
          <span>{tri(L.signature)}</span>
        </footer>
        <style>{PRINT_CSS}</style>
      </main>
    );
  }

  if (!rx || !patient) {
    return (
      <main className="print-sheet">
        <p className="muted">Loading…</p>
        <style>{PRINT_CSS}</style>
      </main>
    );
  }

  return (
    <main className="print-sheet">
      <header className="print-head">
        <div>
          <h1>{tri(L.prescription)}</h1>
          <p className="tri">Aditya BioNova Smart HMS</p>
        </div>
        <div style={{ textAlign: "right" }}>
          <p>
            {tri(L.date)}: {new Date(rx.signed_at ?? rx.created_at).toLocaleDateString("en-IN")}
          </p>
          <p>
            {tri(L.version)}: v{rx.version}
          </p>
        </div>
      </header>

      <section className="print-meta">
        <p>
          {tri(L.patient)}: <strong>{patient.full_name}</strong>
        </p>
        <p>
          {tri(L.uhid)}: <strong>{patient.uhid}</strong> · {patient.gender} ·{" "}
          {patient.date_of_birth
            ? new Date(patient.date_of_birth).toLocaleDateString("en-IN")
            : "—"}
        </p>
        {patient.allergies && (
          <p className="error">
            ⚠ {tri(L.allergies)}: <strong>{patient.allergies}</strong>
            {rx.allergy_acknowledged && " (acknowledged by prescriber)"}
          </p>
        )}
        {rx.diagnosis_text && (
          <p>
            {tri(L.diagnosis)}: <strong>{rx.diagnosis_text}</strong>
          </p>
        )}
      </section>

      <table className="print-table">
        <thead>
          <tr>
            <th>#</th>
            <th>{tri(L.medicines)}</th>
            <th>{tri(L.dose)}</th>
            <th>{tri(L.frequency)}</th>
            <th>{tri(L.days)}</th>
          </tr>
        </thead>
        <tbody>
          {rx.items.map((it, i) => (
            <tr key={it.id}>
              <td>{i + 1}</td>
              <td>
                <strong>{drugs[it.drug_id]?.generic_name ?? "—"}</strong>
                {drugs[it.drug_id]?.brand_hint && (
                  <span className="tri"> ({drugs[it.drug_id].brand_hint})</span>
                )}
                {it.instructions && <div className="tri">{it.instructions}</div>}
              </td>
              <td>{it.dose}</td>
              <td>{it.frequency}</td>
              <td>{it.duration_days}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <section className="print-advice">
        {rx.advice && (
          <p>
            {tri(L.advice)}: {rx.advice}
          </p>
        )}
        {rx.follow_up && (
          <p>
            {tri(L.followUp)}: {rx.follow_up}
          </p>
        )}
        <p className="tri">{L.amendments.en}</p>
      </section>

      <footer className="print-foot">
        <span>
          {tri(L.signature)}
          {rx.prescriber_user_id ? "" : ""}
        </span>
      </footer>

      <button
        type="button"
        className="no-print"
        onClick={() => window.print()}
        style={{ margin: "1rem auto", display: "block" }}
      >
        Print / Save as PDF
      </button>

      <style>{PRINT_CSS}</style>
    </main>
  );
}

const PRINT_CSS = `
  .print-sheet { font-family: Georgia, "Noto Serif", serif; color: #101828;
    max-width: 800px; margin: 0 auto; padding: 1.5rem; background: #fff; }
  .print-head { display: flex; justify-content: space-between;
    border-bottom: 3px double #1d3557; padding-bottom: 0.5rem; }
  .print-head h1 { font-size: 1.4rem; margin: 0; color: #1d3557;
    letter-spacing: 0.05em; }
  .tri { color: #486581; font-size: 0.85em; font-weight: normal; }
  .print-meta { margin: 0.75rem 0; }
  .print-meta p { margin: 0.2rem 0; }
  .print-table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
  .print-table th, .print-table td { border: 1px solid #bcccdc;
    padding: 0.4rem 0.5rem; text-align: left; font-size: 0.95rem; }
  .print-table th { background: #f0f4f8; }
  .print-advice { margin-top: 0.75rem; }
  .print-totals { text-align: right; margin-top: 0.5rem; }
  .print-totals p { margin: 0.15rem 0; }
  .print-foot { margin-top: 2.5rem; display: flex; justify-content: flex-end;
    border-top: 1px solid #bcccdc; padding-top: 0.5rem; }
  @media print {
    @page { size: A4; margin: 14mm; }
    .no-print { display: none !important; }
    .print-sheet { max-width: none; padding: 0; }
  }
  @media print and (max-width: 210mm) {
    @page { size: A5; margin: 10mm; }
  }
`;
