import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Aditya BioNova Smart HMS",
  description:
    "Multi-tenant clinic & hospital management — foundation build (M1). Synthetic data only.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
