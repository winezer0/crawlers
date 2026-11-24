"use client";
import { useEffect } from "react";

export default function SharedProbe({ variant = "default", options = {} }) {
  const { label = "fallback" } = options;
  useEffect(() => {
    const headers = { "X-Component-Proof": variant + ":" + label };
    if (variant === "alpha") {
      fetch("/api/context-regression/alpha", { headers });
    } else if (variant === "beta") {
      fetch("/api/context-regression/beta", { headers });
    } else if (variant === "default") {
      fetch("/api/context-regression/default", { headers });
    }
  }, [variant, label]);
  return <p>{variant}:{label}</p>;
}
