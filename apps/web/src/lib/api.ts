"use client";

import { clearTokens, getAccessToken, saveTokens } from "./auth";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  const token = getAccessToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`/api/v1${path}`, { ...options, headers });
  if (res.status === 401 && token) {
    clearTokens();
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown, idempotencyKey?: string) =>
    request<T>(path, {
      method: "POST",
      body: JSON.stringify(body),
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
};

export type Me = {
  user: { id: string; email: string; full_name: string; status: string };
  organization: { id: string; name: string; slug: string; status: string };
  membership_id: string;
  branch_id: string | null;
  role: string;
  permissions: string[];
};

export type LoginResponse = {
  user: Me["user"];
  organization: Me["organization"];
  role: string;
  permissions: string[];
  tokens: { access_token: string; refresh_token: string; expires_in: number };
};

export async function login(input: {
  email: string;
  password: string;
  organization_slug?: string;
}): Promise<LoginResponse> {
  const data = await api.post<LoginResponse>("/auth/login", input);
  saveTokens(data.tokens);
  return data;
}

// --- M2 clinical types -------------------------------------------------------
export type Patient = {
  id: string;
  organization_id: string;
  uhid: string;
  full_name: string;
  gender: string;
  date_of_birth: string | null;
  dob_estimated: boolean;
  phone: string | null;
  address: string;
  blood_group: string | null;
  allergies: string;
  status: string;
  merged_into_id: string | null;
  created_at: string;
};

export type DuplicateCandidate = {
  id: string;
  uhid: string;
  full_name: string;
  gender: string;
  date_of_birth: string | null;
  phone: string | null;
  matched_on: string[];
};

export type Appointment = {
  id: string;
  patient_id: string;
  branch_id: string;
  provider_user_id: string | null;
  starts_at: string;
  ends_at: string;
  reason: string;
  status: string;
  notes: string;
};

export type QueueEntry = {
  id: string;
  patient_id: string;
  branch_id: string;
  appointment_id: string | null;
  service_date: string;
  token_number: number;
  status: string;
  priority: string;
  doctor_user_id: string | null;
  called_at: string | null;
  started_at: string | null;
  completed_at: string | null;
};

export type PageOf<T> = { items: T[]; total: number; limit: number; offset: number };

export type Branch = { id: string; name: string; code: string; status: string };

// --- M3 clinical + billing types --------------------------------------------
export type Encounter = {
  id: string;
  organization_id: string;
  branch_id: string;
  patient_id: string;
  appointment_id: string | null;
  queue_entry_id: string | null;
  provider_user_id: string | null;
  chief_complaint: string;
  history: string;
  examination: string;
  assessment: string;
  plan: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  created_at: string;
};

export type Vitals = {
  id: string;
  encounter_id: string;
  bp_systolic: number | null;
  bp_diastolic: number | null;
  pulse: number | null;
  temperature_c: number | null;
  spo2: number | null;
  respiratory_rate: number | null;
  weight_kg: number | null;
  height_cm: number | null;
  notes: string;
  recorded_at: string;
};

export type Diagnosis = {
  id: string;
  encounter_id: string;
  code: string;
  label: string;
  kind: string;
  status: string;
  created_at: string;
};

export type EncounterDetail = Encounter & {
  vitals: Vitals[];
  diagnoses: Diagnosis[];
  prescription_ids: string[];
};

export type Drug = {
  id: string;
  generic_name: string;
  brand_hint: string;
  drug_class: string;
};

export type RxItem = {
  id: string;
  prescription_id: string;
  drug_id: string;
  dose: string;
  route: string;
  frequency: string;
  duration_days: number;
  quantity: string;
  instructions: string;
  seq: number;
};

export type Prescription = {
  id: string;
  organization_id: string;
  encounter_id: string | null;
  patient_id: string;
  prescriber_user_id: string | null;
  status: string;
  version: number;
  supersedes_id: string | null;
  diagnosis_text: string;
  advice: string;
  follow_up: string;
  allergy_acknowledged: boolean;
  signed_at: string | null;
  amendment_reason: string;
  created_at: string;
};

export type PrescriptionDetail = Prescription & { items: RxItem[] };

export type RxWarning = { kind: string; message: string; drug_ids: string[] };
export type RxWarnings = { warnings: RxWarning[]; blocking: boolean };

export type InvoiceItem = {
  id: string;
  invoice_id: string;
  item_type: string;
  description: string;
  quantity: number;
  unit_price: number;
  tax_rate: number;
  line_total: number;
  seq: number;
};

export type Invoice = {
  id: string;
  organization_id: string;
  branch_id: string;
  patient_id: string;
  encounter_id: string | null;
  invoice_number: string;
  status: string;
  subtotal: number;
  discount: number;
  tax_total: number;
  total: number;
  amount_paid: number;
  notes: string;
  issued_at: string | null;
  created_at: string;
};

export type Payment = {
  id: string;
  invoice_id: string;
  amount: number;
  method: string;
  gateway: string;
  status: string;
  gateway_ref: string;
  failure_reason: string;
  created_at: string;
};

export type InvoiceDetail = Invoice & { items: InvoiceItem[]; payments: Payment[] };

export type Refund = {
  id: string;
  payment_id: string;
  amount: number;
  reason: string;
  status: string;
  created_at: string;
};

export type LedgerEntry = {
  id: number;
  invoice_id: string;
  payment_id: string | null;
  refund_id: string | null;
  entry_type: string;
  direction: string;
  amount: number;
  description: string;
  created_at: string;
};

export type Receipt = {
  invoice: Invoice;
  items: InvoiceItem[];
  payments: Payment[];
  refunds: Refund[];
  ledger: LedgerEntry[];
  balance_due: number;
  org_name: string;
  branch_name: string;
  patient_name: string;
  patient_uhid: string;
};
