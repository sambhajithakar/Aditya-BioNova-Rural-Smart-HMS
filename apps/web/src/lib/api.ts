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
