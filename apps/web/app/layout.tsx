import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./styles.css";

export const metadata: Metadata = {
  title: "MixAI | Laboratorio de mezcla",
  description: "Analiza el balance, la dinámica y la imagen estéreo de tu mezcla."
};

export default function Layout({ children }: { children: ReactNode }) {
  return <html lang="es"><body>{children}</body></html>;
}
